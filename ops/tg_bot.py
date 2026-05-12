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
  KIRA_TG_MODEL          - default model (default: claude-haiku-4.5)
  KIRA_TG_CHUNK_LEN      - chars per TG message (default: 3900; TG hard cap=4096)
  KIRA_TG_PARSE_MODE     - 'Markdown' (default), 'MarkdownV2', or '' (off)
  KIRA_TG_MAX_IMAGE_MB   - reject images larger than this (default: 8)
  KIRA_TG_MAX_AUDIO_SEC  - reject voice longer than this (default: 300)
  KIRA_TG_WHISPER        - 'faster-whisper' | 'groq' | '' (default: '' = disabled)
  KIRA_TG_WHISPER_MODEL  - faster-whisper model size (default: 'tiny')
  KIRA_TG_GROQ_API_KEY   - Groq API key (when KIRA_TG_WHISPER=groq)
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
# When set, the bot derives a per-TG-user bearer token using HMAC so each
# Telegram user gets their own Kira session, quotas and history. The matching
# secret must live on the Kira server as KIRA_TG_DERIVE_SECRET.
TG_DERIVE_SECRET = os.environ.get("KIRA_TG_DERIVE_SECRET", "").strip()
MODEL_DEFAULT = os.environ.get("KIRA_TG_MODEL", "claude-haiku-4.5")
# Per TG message; hard cap is 4096. Leave headroom for the tools-used header.
CHUNK_LEN = int(os.environ.get("KIRA_TG_CHUNK_LEN", "3900"))
# Legacy 'Markdown' is more forgiving than MarkdownV2 (no need to escape
# every '.', '!', '-'). We try parse_mode first, fall back to plain text on 400.
PARSE_MODE = os.environ.get("KIRA_TG_PARSE_MODE", "Markdown")
MAX_IMAGE_MB = float(os.environ.get("KIRA_TG_MAX_IMAGE_MB", "8"))
MAX_AUDIO_SEC = int(os.environ.get("KIRA_TG_MAX_AUDIO_SEC", "300"))
WHISPER_BACKEND = os.environ.get("KIRA_TG_WHISPER", "").strip()
WHISPER_MODEL = os.environ.get("KIRA_TG_WHISPER_MODEL", "tiny")
GROQ_API_KEY = os.environ.get("KIRA_TG_GROQ_API_KEY", "")
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# user_id -> session_id (one persistent kira session per user)
USER_SESSIONS: dict[int, str] = {}
# Cache of derived per-user bearer tokens. Deterministic, so a cold start
# yields the same tokens — but we cache to skip HMAC on every message.
_USER_TOKENS: dict[int, str] = {}


def token_for(user_id: int) -> str:
    """Return the bearer token to use when talking to Kira on behalf of .

    Falls back to the shared KIRA_AUTH_TOKEN when no derive secret is set so
    a single-user deployment keeps working unchanged.
    """
    if not TG_DERIVE_SECRET:
        return KIRA_TOKEN
    tok = _USER_TOKENS.get(user_id)
    if tok:
        return tok
    import hmac, hashlib
    tag = hmac.new(TG_DERIVE_SECRET.encode("utf-8"),
                   str(user_id).encode("utf-8"),
                   hashlib.sha256).hexdigest()[:16]
    tok = f"ktk_tg_{user_id}_{tag}"
    _USER_TOKENS[user_id] = tok
    return tok


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


# ---------------------------------------------------------------------------
# Media: photo + voice download/transcription
# ---------------------------------------------------------------------------


async def tg_get_file_url(http, file_id: str) -> Optional[str]:
    """Resolve a Telegram file_id to a downloadable URL."""
    res = await tg(http, "getFile", file_id=file_id)
    if not res.get("ok"):
        log.error("getFile failed: %s", res)
        return None
    path = res["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"


async def download_bytes(http, url: str, max_bytes: int) -> Optional[bytes]:
    r = await http.get(url, timeout=60)
    if r.status_code != 200:
        log.warning("download %s: HTTP %s", url, r.status_code)
        return None
    if len(r.content) > max_bytes:
        return None
    return r.content


def detect_image_format(blob: bytes) -> str:
    """Heuristic file-type detection so Kira's /agent gets the right `format`."""
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if blob[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "webp"
    return "png"  # safe default


async def extract_image(http, msg: dict) -> Optional[dict]:
    """Pull the largest available photo and return Kira's image dict.

    Returns {format, data_base64} matching what app.py /agent expects, or
    None if no usable image is attached / too large.
    """
    import base64

    photos = msg.get("photo") or []
    file_id = None
    if photos:
        # TG sends multiple resolutions; pick the largest.
        file_id = max(photos, key=lambda p: p.get("file_size", 0)).get("file_id")
    elif (doc := msg.get("document")):
        mime = (doc.get("mime_type") or "").lower()
        if mime.startswith("image/"):
            file_id = doc.get("file_id")
    if not file_id:
        return None
    url = await tg_get_file_url(http, file_id)
    if not url:
        return None
    blob = await download_bytes(http, url, int(MAX_IMAGE_MB * 1024 * 1024))
    if blob is None:
        return None
    return {
        "format": detect_image_format(blob),
        "data_base64": base64.b64encode(blob).decode("ascii"),
    }


_whisper_model = None  # lazy-loaded faster-whisper model singleton


async def _transcribe_local(blob: bytes) -> Optional[str]:
    """Run faster-whisper on an audio blob (Opus/OGG from TG voice messages)."""
    global _whisper_model
    import tempfile

    try:
        if _whisper_model is None:
            from faster_whisper import WhisperModel  # type: ignore

            log.info("loading faster-whisper model: %s", WHISPER_MODEL)
            _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    except ImportError:
        log.error("faster-whisper not installed; set KIRA_TG_WHISPER='' or pip install faster-whisper")
        return None

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(blob)
        path = f.name
    try:
        loop = asyncio.get_running_loop()
        segments, _info = await loop.run_in_executor(
            None, lambda: _whisper_model.transcribe(path, beam_size=1)
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text or None
    except Exception as e:
        log.exception("local transcription failed: %s", e)
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def _transcribe_groq(http, blob: bytes) -> Optional[str]:
    """Groq Whisper API (free tier). Audio sent as multipart/form-data."""
    if not GROQ_API_KEY:
        log.error("KIRA_TG_GROQ_API_KEY not set")
        return None
    try:
        files = {"file": ("voice.ogg", blob, "audio/ogg")}
        data = {"model": "whisper-large-v3-turbo"}
        r = await http.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files=files, data=data, timeout=60,
        )
        if r.status_code != 200:
            log.warning("groq transcribe: HTTP %s: %s", r.status_code, r.text[:200])
            return None
        return (r.json().get("text") or "").strip() or None
    except Exception as e:
        log.exception("groq transcribe failed: %s", e)
        return None


async def extract_voice_text(http, msg: dict) -> Optional[str]:
    """Download voice/audio from TG and return transcribed text.

    Returns None if transcription is disabled, audio too long, or backend
    failed. The caller surfaces this back to the user.
    """
    voice = msg.get("voice") or msg.get("audio")
    if not voice:
        return None
    if not WHISPER_BACKEND:
        return None
    dur = int(voice.get("duration", 0))
    if dur > MAX_AUDIO_SEC:
        return f"[voice too long: {dur}s > {MAX_AUDIO_SEC}s]"
    url = await tg_get_file_url(http, voice["file_id"])
    if not url:
        return None
    blob = await download_bytes(http, url, 25 * 1024 * 1024)  # TG voice cap is 1MB-ish anyway
    if blob is None:
        return None
    if WHISPER_BACKEND == "groq":
        return await _transcribe_groq(http, blob)
    if WHISPER_BACKEND == "faster-whisper":
        return await _transcribe_local(blob)
    log.warning("unknown KIRA_TG_WHISPER backend: %r", WHISPER_BACKEND)
    return None


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
    # Text can come from .text (regular), .caption (photo with caption),
    # or transcribed voice. Photos may arrive caption-less.
    text = (msg.get("text") or msg.get("caption") or "").strip()

    if user_id not in ALLOWED:
        await send_message(http, chat_id, "⛔️ Access denied.", allow_markdown=False)
        return

    # Voice / audio → transcribe, prepend to text (or use as text if empty).
    if msg.get("voice") or msg.get("audio"):
        await send_typing(http, chat_id)
        transcript = await extract_voice_text(http, msg)
        if transcript is None:
            if not WHISPER_BACKEND:
                await send_message(http, chat_id,
                    "🎤 Voice transcription is disabled. Set KIRA_TG_WHISPER on the bot.",
                    allow_markdown=False)
                return
            await send_message(http, chat_id, "❌ Failed to transcribe voice.",
                               allow_markdown=False)
            return
        if transcript.startswith("[voice too long"):
            await send_message(http, chat_id, f"❌ {transcript}", allow_markdown=False)
            return
        # Show what we heard, then continue as if the user had typed it.
        await send_message(http, chat_id, f"🎤 _{transcript}_",
                           reply_to=msg["message_id"])
        text = (text + "\n" + transcript).strip() if text else transcript

    # Photo / image document → attach as multimodal input.
    image_attachment: Optional[dict] = None
    if msg.get("photo") or (msg.get("document") or {}).get("mime_type", "").startswith("image/"):
        image_attachment = await extract_image(http, msg)
        if image_attachment is None:
            await send_message(http, chat_id,
                f"❌ Image rejected (too large > {MAX_IMAGE_MB}MB or unreadable).",
                allow_markdown=False)
            return
        if not text:
            text = "What's in this image?"

    if not text:
        return

    # commands
    if text.startswith("/start"):
        whisper_line = "⚫ Voice transcription: off" if not WHISPER_BACKEND \
            else f"✅ Voice transcription: {WHISPER_BACKEND}"
        await send_message(
            http, chat_id,
            "🌸 *Кира* — self-modifying AI agent.\n\n"
            "Напиши, пришли фото или голосовое — отвечу.\n\n"
            "📷 Фото: опишу что на картинке (caption опционален).\n"
            f"🎤 Голос: распознаю речь. {whisper_line}\n\n"
            "Команды:\n"
            "/new — новая сессия\n"
            "/status — здоровье сервиса\n"
        )
        return
    if text.startswith("/new"):
        USER_SESSIONS.pop(user_id, None)
        await send_message(http, chat_id, "✨ Новая сессия.")
        return
    if text.startswith("/status"):
        try:
            user_tok = token_for(user_id)
            r = await http.get(
                f"{KIRA_URL}/agent/health",
                headers={"Authorization": f"Bearer {user_tok}"} if user_tok else {},
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
    if image_attachment is not None:
        body["images"] = [image_attachment]

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
        user_tok = token_for(user_id)
        if user_tok:
            headers["Authorization"] = f"Bearer {user_tok}"
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
    log.info("Kira URL: %s (token=%s, per-user-derived=%s)",
             KIRA_URL,
             "set" if KIRA_TOKEN else "none",
             "yes" if TG_DERIVE_SECRET else "no")
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
