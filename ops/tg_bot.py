#!/usr/bin/env python3
"""Telegram bot frontend for Kira.

Long-poll Telegram, accept messages from KIRA_TG_ALLOWED_USERS,
proxy them to /agent SSE, stream the reply back by editing one
Telegram message as tokens arrive.

Required env:
  KIRA_TG_BOT_TOKEN     - Telegram bot token (from @BotFather)
  KIRA_TG_ALLOWED_USERS - comma-separated TG user IDs
  KIRA_URL              - Kira base URL (default: http://localhost:3000)
  KIRA_AUTH_TOKEN       - Kira bearer token

Optional:
  KIRA_TG_MODEL         - default model (default: claude-haiku-4.5)
  KIRA_TG_CHUNK_LEN     - chars per TG message (default: 3900; TG hard cap=4096)
  KIRA_TG_PARSE_MODE    - 'Markdown' (default), 'MarkdownV2', or '' (off)
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from typing import Optional

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("kira-tg")

BOT_TOKEN = os.environ["KIRA_TG_BOT_TOKEN"]
ALLOWED = {int(x) for x in os.environ["KIRA_TG_ALLOWED_USERS"].split(",") if x.strip()}
KIRA_URL = os.environ.get("KIRA_URL", "http://localhost:3000").rstrip("/")
KIRA_TOKEN = os.environ.get("KIRA_AUTH_TOKEN", "")
MODEL_DEFAULT = os.environ.get("KIRA_TG_MODEL", "claude-haiku-4.5")
# Per TG message; hard cap is 4096. Leave headroom for the tools-used header.
CHUNK_LEN = int(os.environ.get("KIRA_TG_CHUNK_LEN", "3900"))
# Legacy 'Markdown' is more forgiving than MarkdownV2 (no need to escape
# every '.', '!', '-'). We try parse_mode first, fall back to plain text on 400.
PARSE_MODE = os.environ.get("KIRA_TG_PARSE_MODE", "Markdown")
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# user_id -> session_id (one persistent kira session per user)
USER_SESSIONS: dict[int, str] = {}


async def tg(http: httpx.AsyncClient, method: str, **params) -> dict:
    r = await http.post(f"{TG_API}/{method}", data=params, timeout=30)
    return r.json()


def split_into_chunks(text: str, limit: int = CHUNK_LEN) -> list[str]:
    """Split a long string into Telegram-sized pieces.

    Preference order for split points: blank line, newline, space, hard cut.
    Code fences are tracked: if a chunk ends inside a ``` block we close it,
    and the next chunk starts with a re-opening fence so each piece renders.
    """
    if len(text) <= limit:
        return [text]
    raw_pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        idx = window.rfind("\n\n")
        if idx < limit // 2:
            idx = window.rfind("\n")
        if idx < limit // 2:
            idx = window.rfind(" ")
        if idx < limit // 3:
            idx = limit  # hard cut
        raw_pieces.append(rest[:idx].rstrip())
        rest = rest[idx:].lstrip()
    if rest:
        raw_pieces.append(rest)

    # Re-balance code fences across pieces.
    out: list[str] = []
    open_at_start = False
    for piece in raw_pieces:
        prefix = "```\n" if open_at_start else ""
        fences = piece.count("```")
        ends_open = open_at_start ^ (fences % 2 == 1)
        suffix = "\n```" if ends_open else ""
        out.append(prefix + piece + suffix)
        open_at_start = ends_open
    return out


async def _tg_send_or_edit(
    http, method: str, params: dict, *, allow_markdown: bool = True
) -> dict:
    """sendMessage / editMessageText with parse_mode fallback.

    Tries Markdown first (if PARSE_MODE set and allow_markdown=True); on
    'can't parse entities' or any 400, retries as plain text.
    """
    if allow_markdown and PARSE_MODE:
        with_md = {**params, "parse_mode": PARSE_MODE}
        res = await tg(http, method, **with_md)
        if res.get("ok"):
            return res
        desc = (res.get("description") or "").lower()
        if "parse" in desc or "entities" in desc or "can't" in desc:
            log.debug("%s: markdown parse failed, retrying as plain text", method)
        else:
            return res  # other errors — surface as-is
    return await tg(http, method, **params)


async def send_message(http, chat_id: int, text: str, reply_to: int | None = None,
                       allow_markdown: bool = True) -> Optional[int]:
    """Send a (possibly long) message, splitting into chunks as needed.

    Returns the message_id of the FIRST sent chunk (useful as a streaming
    placeholder). Subsequent chunks are sent without reply_to.
    """
    chunks = split_into_chunks(text) if text else [""]
    first_mid: Optional[int] = None
    for i, chunk in enumerate(chunks):
        params = {"chat_id": chat_id, "text": chunk or "\u200b",
                  "disable_web_page_preview": "true"}
        if reply_to and i == 0:
            params["reply_to_message_id"] = reply_to
        res = await _tg_send_or_edit(http, "sendMessage", params, allow_markdown=allow_markdown)
        if not res.get("ok"):
            log.error("sendMessage failed: %s", res)
            return first_mid
        if first_mid is None:
            first_mid = res["result"]["message_id"]
    return first_mid


async def edit_message(http, chat_id: int, mid: int, text: str,
                       allow_markdown: bool = True) -> None:
    """Edit a single TG message. Truncates to one chunk; the streaming code
    in `handle_message` uses sync_chunks() to manage multi-message edits."""
    if not text.strip():
        return
    chunks = split_into_chunks(text)
    params = {"chat_id": chat_id, "message_id": mid, "text": chunks[0],
              "disable_web_page_preview": "true"}
    res = await _tg_send_or_edit(http, "editMessageText", params, allow_markdown=allow_markdown)
    if not res.get("ok") and "message is not modified" not in str(res):
        log.debug("editMessageText: %s", res.get("description"))


async def sync_chunks(
    http,
    chat_id: int,
    placeholders: list[int],
    text: str,
    *,
    allow_markdown: bool = True,
) -> list[int]:
    """Make the chain of TG messages match `text` (multi-message streaming).

    - placeholders[i] is edited to chunks[i].
    - If text grew into a new chunk, send a new message and append its id.
    - We never delete extra placeholders (TG ratelimits delete heavily).
    Returns the updated placeholder list.
    """
    chunks = split_into_chunks(text or "\u200b")
    for i, chunk in enumerate(chunks):
        if i < len(placeholders):
            params = {"chat_id": chat_id, "message_id": placeholders[i],
                      "text": chunk, "disable_web_page_preview": "true"}
            res = await _tg_send_or_edit(http, "editMessageText", params,
                                         allow_markdown=allow_markdown)
            if not res.get("ok") and "not modified" not in str(res):
                log.debug("sync edit[%d]: %s", i, res.get("description"))
        else:
            params = {"chat_id": chat_id, "text": chunk,
                      "disable_web_page_preview": "true"}
            res = await _tg_send_or_edit(http, "sendMessage", params,
                                         allow_markdown=allow_markdown)
            if res.get("ok"):
                placeholders.append(res["result"]["message_id"])
            else:
                log.error("sync send[%d]: %s", i, res)
                break
    return placeholders


async def send_typing(http, chat_id: int) -> None:
    await tg(http, "sendChatAction", chat_id=chat_id, action="typing")


def parse_sse_line(line: str) -> Optional[dict]:
    if not line.startswith("data: "):
        return None
    payload = line[6:].strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


async def handle_message(http: httpx.AsyncClient, msg: dict) -> None:
    user_id = msg["from"]["id"]
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if user_id not in ALLOWED:
        await send_message(http, chat_id, "⛔️ Access denied.")
        return

    # commands
    if text.startswith("/start"):
        await send_message(
            http, chat_id,
            "🌸 Кира — self-modifying AI agent.\n\n"
            "Просто напиши вопрос — отвечу.\n\n"
            "Команды:\n"
            "/new — новая сессия\n"
            "/status — здоровье сервиса\n"
            "/model <name> — сменить модель\n"
        )
        return
    if text.startswith("/new"):
        USER_SESSIONS.pop(user_id, None)
        await send_message(http, chat_id, "✨ Новая сессия.")
        return
    if text.startswith("/status"):
        try:
            r = await http.get(
                f"{KIRA_URL}/agent/health",
                headers={"Authorization": f"Bearer {KIRA_TOKEN}"} if KIRA_TOKEN else {},
                timeout=10,
            )
            data = r.json()
            await send_message(
                http, chat_id,
                f"📊 status: {data.get('status')}\n"
                f"uptime: {int(data.get('uptime_seconds', 0))}s\n"
                f"in-flight: {data.get('in_flight', 0)}\n"
                f"keys: {data.get('keys', {})}\n"
            )
        except Exception as e:
            await send_message(http, chat_id, f"❌ {e}")
        return

    sid = USER_SESSIONS.get(user_id)
    model = MODEL_DEFAULT

    # placeholder message we'll edit as tokens stream. send_message is used
    # without markdown for this single-line transient text.
    first_mid = await send_message(http, chat_id, "⏳ ...", reply_to=msg["message_id"],
                                    allow_markdown=False)
    if not first_mid:
        return
    placeholders: list[int] = [first_mid]

    body = {"prompt": text, "model": model}
    if sid:
        body["session_id"] = sid

    accumulated = ""
    last_edit = 0.0
    tools_used: list[str] = []

    async def maybe_edit(force: bool = False) -> None:
        nonlocal last_edit, placeholders
        now = time.monotonic()
        if not force and now - last_edit < 1.2:
            return
        last_edit = now
        display = accumulated or "⏳ ..."
        if tools_used:
            display = f"🔧 {', '.join(tools_used[-3:])}\n\n{display}"
        placeholders = await sync_chunks(http, chat_id, placeholders, display)

    try:
        headers = {"Accept": "text/event-stream"}
        if KIRA_TOKEN:
            headers["Authorization"] = f"Bearer {KIRA_TOKEN}"
        async with http.stream(
            "POST", f"{KIRA_URL}/agent", json=body, headers=headers, timeout=300
        ) as r:
            if r.status_code != 200:
                err = await r.aread()
                await edit_message(http, chat_id, placeholders[0],
                                   f"❌ HTTP {r.status_code}: {err[:300]!r}",
                                   allow_markdown=False)
                return
            async for line in r.aiter_lines():
                ev = parse_sse_line(line)
                if not ev:
                    continue
                t = ev.get("type")
                if t == "meta":
                    sid = ev.get("session_id")
                    if sid:
                        USER_SESSIONS[user_id] = sid
                elif t in ("text", "delta"):
                    accumulated += ev.get("delta") or ev.get("text") or ""
                    await maybe_edit()
                elif t in ("tool", "tool_use", "tool_call"):
                    name = ev.get("name") or ev.get("tool") or "?"
                    if name and name not in tools_used:
                        tools_used.append(name)
                    await send_typing(http, chat_id)
                    await maybe_edit()
                elif t == "tool_result":
                    # tool finished, refresh typing
                    await send_typing(http, chat_id)
                elif t == "error":
                    accumulated += f"\n❌ {ev.get('message')}"
                    await maybe_edit(force=True)
                elif t == "done":
                    break
        await maybe_edit(force=True)
    except Exception as e:
        log.exception("stream failed")
        await edit_message(http, chat_id, placeholders[0], f"❌ {e}",
                           allow_markdown=False)


async def main():
    log.info("Kira TG bot starting. Allowed users: %s", ALLOWED)
    log.info("Kira URL: %s (token=%s)", KIRA_URL, "set" if KIRA_TOKEN else "none")
    offset = 0
    async with httpx.AsyncClient() as http:
        # set bot commands (visible in TG menu)
        await tg(http, "setMyCommands", commands=json.dumps([
            {"command": "new", "description": "Новая сессия"},
            {"command": "status", "description": "Здоровье сервиса"},
            {"command": "start", "description": "Инфо"},
        ]))
        while True:
            try:
                r = await http.get(
                    f"{TG_API}/getUpdates",
                    params={"offset": offset, "timeout": 30, "allowed_updates": '["message"]'},
                    timeout=40,
                )
                data = r.json()
                if not data.get("ok"):
                    log.error("getUpdates: %s", data)
                    await asyncio.sleep(3)
                    continue
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    if "message" in upd:
                        asyncio.create_task(handle_message(http, upd["message"]))
            except Exception as e:
                log.error("loop error: %s", e)
                await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
