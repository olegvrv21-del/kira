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
  KIRA_TG_MAX_LEN       - max Telegram message length (default: 4000)
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
MAX_LEN = int(os.environ.get("KIRA_TG_MAX_LEN", "4000"))
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# user_id -> session_id (one persistent kira session per user)
USER_SESSIONS: dict[int, str] = {}


async def tg(http: httpx.AsyncClient, method: str, **params) -> dict:
    r = await http.post(f"{TG_API}/{method}", data=params, timeout=30)
    return r.json()


async def send_message(http, chat_id: int, text: str, reply_to: int | None = None) -> Optional[int]:
    params = {"chat_id": chat_id, "text": text[:MAX_LEN], "disable_web_page_preview": "true"}
    if reply_to:
        params["reply_to_message_id"] = reply_to
    res = await tg(http, "sendMessage", **params)
    if not res.get("ok"):
        log.error("sendMessage failed: %s", res)
        return None
    return res["result"]["message_id"]


async def edit_message(http, chat_id: int, mid: int, text: str) -> None:
    if not text.strip():
        return
    res = await tg(http, "editMessageText",
                   chat_id=chat_id, message_id=mid, text=text[:MAX_LEN],
                   disable_web_page_preview="true")
    if not res.get("ok") and "message is not modified" not in str(res):
        # ignore noisy errors; log once
        log.debug("editMessageText: %s", res.get("description"))


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

    # placeholder message we'll edit as tokens stream
    placeholder = await send_message(http, chat_id, "⏳ ...", reply_to=msg["message_id"])
    if not placeholder:
        return

    body = {"prompt": text, "model": model}
    if sid:
        body["session_id"] = sid

    accumulated = ""
    last_edit = 0.0
    tools_used: list[str] = []

    async def maybe_edit(force: bool = False) -> None:
        nonlocal last_edit
        now = time.monotonic()
        if not force and now - last_edit < 1.2:
            return
        last_edit = now
        display = accumulated or "⏳ ..."
        if tools_used:
            display = f"🔧 {', '.join(tools_used[-3:])}\n\n{display}"
        await edit_message(http, chat_id, placeholder, display)

    try:
        headers = {"Accept": "text/event-stream"}
        if KIRA_TOKEN:
            headers["Authorization"] = f"Bearer {KIRA_TOKEN}"
        async with http.stream(
            "POST", f"{KIRA_URL}/agent", json=body, headers=headers, timeout=300
        ) as r:
            if r.status_code != 200:
                err = await r.aread()
                await edit_message(http, chat_id, placeholder, f"❌ HTTP {r.status_code}: {err[:300]!r}")
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
        await edit_message(http, chat_id, placeholder, f"❌ {e}")


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
