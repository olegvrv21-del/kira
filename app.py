import json
import os
import re
import struct
import time
import uuid
from typing import Any

_PROCESS_START = time.time()

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent_freeze
import agent_runtime
import agent_skills
import agent_store

# Controls whether internal error details are included in HTTP/SSE responses.
# Off by default (avoids stack-trace exposure; CodeQL py/stack-trace-exposure).
# Set KIRA_ERR_VERBOSE=1 in dev or when debugging upstream issues.
_ERR_VERBOSE = os.environ.get("KIRA_ERR_VERBOSE") == "1"

OMNI_URL = os.environ.get("OMNI_URL", "http://localhost:8128/v1")
OMNI_KEY = os.environ.get("OPENAI_API_KEY", "")
KIRO_API_KEY = os.environ.get("KIRO_API_KEY", "")
from agent_keys import key_pool  # noqa: E402

KIRO_Q_URL = "https://q.us-east-1.amazonaws.com/"
DEFAULT_MODEL = "q/claude-opus-4.7" if KIRO_API_KEY else "kr/claude-sonnet-4.5"
SYSTEM_PROMPT = "Ты — Кира, старший инженер-напарник. Отвечаешь по-русски, кратко и по делу, без эмодзи."


def _m(id, label, provider, tier, mult, desc, strengths):
    return {
        "id": id,
        "label": label,
        "provider": provider,
        "tier": tier,
        "multiplier": mult,
        "description": desc,
        "strengths": strengths,
    }


_KR_MODELS = [
    _m(
        "kr/claude-sonnet-4.5",
        "Claude Sonnet 4.5",
        "Kira (Anthropic)",
        "sonnet",
        1.0,
        "Сбалансированная модель для большинства задач.",
        ["Код", "Анализ", "Длинные диалоги"],
    ),
    _m(
        "kr/claude-haiku-4.5",
        "Claude Haiku 4.5",
        "Kira (Anthropic)",
        "haiku",
        0.2,
        "Быстрая и дешёвая модель для коротких запросов.",
        ["Скорость", "Низкая стоимость"],
    ),
]
_Q_MODELS = [
    _m(
        "q/claude-opus-4.7",
        "Claude Opus 4.7",
        "Kiro Q (Anthropic)",
        "opus",
        5.0,
        "Топовая модель для сложных задач и архитектуры.",
        ["Рассуждение", "Архитектура", "Большой контекст"],
    ),
    _m(
        "q/claude-opus-4.6",
        "Claude Opus 4.6",
        "Kiro Q (Anthropic)",
        "opus",
        5.0,
        "Предыдущее поколение Opus, стабильная.",
        ["Рассуждение", "Код"],
    ),
    _m(
        "q/claude-sonnet-4.6",
        "Claude Sonnet 4.6",
        "Kiro Q (Anthropic)",
        "sonnet",
        1.0,
        "Универсальная рабочая лошадка.",
        ["Код", "Универсальность"],
    ),
    _m(
        "q/claude-sonnet-4.5",
        "Claude Sonnet 4.5",
        "Kiro Q (Anthropic)",
        "sonnet",
        1.0,
        "Предыдущий Sonnet.",
        ["Стабильность"],
    ),
    _m(
        "q/claude-haiku-4.5",
        "Claude Haiku 4.5",
        "Kiro Q (Anthropic)",
        "haiku",
        0.2,
        "Быстрая и дешёвая.",
        ["Скорость", "Низкая стоимость"],
    ),
]
MODELS = (_Q_MODELS if KIRO_API_KEY else []) + _KR_MODELS
MODEL_IDS = {m["id"] for m in MODELS}

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app):  # noqa: ARG001
    try:
        ttl = int(os.environ.get("KIRA_SESSION_TTL_DAYS", "30"))
        gone = agent_store.cleanup_old_sessions(ttl)
        if gone:
            ws_root = os.path.join(os.path.dirname(__file__), "workspaces")
            for sid in gone:
                p = os.path.join(ws_root, sid)
                if os.path.isdir(p):
                    try:
                        _shutil.rmtree(p)
                    except Exception:
                        pass
            print(f"[agent_store] cleaned {len(gone)} old sessions")
    except Exception as e:
        print(f"[agent_store] cleanup failed: {e}")
    yield


app = FastAPI(lifespan=_lifespan)

# Optional auth + per-IP rate limiting (no-op unless env flags set).
import agent_auth
import llm

_auth_status = agent_auth.install(app)

# Static asset directory (JS/CSS extracted from index.html).
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    messages: list
    model: str | None = None


class AgentRequest(BaseModel):
    prompt: str
    model: str | None = None
    session_id: str | None = None
    images: list[dict] | None = None  # [{"format":"png","data_base64":"..."}]
    # Optional per-request turn budget. Default = KIRA_MAX_TURNS env (25).
    # Clamped by KIRA_MAX_TURNS_HARD (100) in agent_runtime to keep a
    # runaway loop from spending unbounded credits.
    max_turns: int | None = None


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/models")
async def models():
    return {"default": DEFAULT_MODEL, "models": MODELS}


import secrets as _secrets

# Token used by the agent's `webchat_restart` tool to restart the service.
# Generated once at process start; agent reads it from /host/webchat/.restart_token.
_RESTART_TOKEN = _secrets.token_urlsafe(24)
try:
    _tok_path = os.path.join(os.path.dirname(__file__), ".restart_token")
    with open(_tok_path, "w") as _f:
        _f.write(_RESTART_TOKEN)
    os.chmod(_tok_path, 0o600)
except Exception as _e:
    print(f"[restart_token] write failed: {_e}")


@app.post("/admin/restart")
async def admin_restart(token: str = ""):
    if not token or not _secrets.compare_digest(token, _RESTART_TOKEN):
        raise HTTPException(status_code=403, detail="bad token")
    # Cancel every active agent session so their SSE streams finish cleanly.
    # Without this, the restart kills mid-stream connections → 'network error'.
    cancelled = []
    try:
        for sid in list(agent_runtime._CANCEL_EVENTS.keys()):
            if agent_runtime.request_cancel(sid):
                cancelled.append(sid)
    except Exception as e:
        print(f"[admin/restart] cancel sweep failed: {e}")
    # Schedule a delayed restart so we can return 200 first and let the SSE
    # cancellation events propagate to the client.
    import asyncio
    import subprocess

    async def _later():
        await asyncio.sleep(1.5)
        subprocess.Popen(["sudo", "systemctl", "restart", "webchat"])

    asyncio.create_task(_later())
    return {"ok": True, "scheduled": True, "cancelled": cancelled}


@app.get("/skills")
async def skills_list():
    return {"skills": agent_skills.list_skills()}


@app.get("/agent/hooks")
async def hooks_endpoint():
    import agent_hooks

    return {"status": agent_hooks.hooks_status(), "hooks": agent_hooks.list_hooks()}


@app.get("/agent/memory")
async def memory_status():
    import agent_memory

    return agent_memory.memory.status()


@app.get("/agent/memory/search")
async def memory_search_endpoint(q: str, k: int = 5):
    import agent_memory

    return {"query": q, "hits": agent_memory.memory.search(q, k=k)}


@app.get("/agent/keys")
async def keys_endpoint():
    return key_pool.status()


@app.post("/agent/keys/reload")
async def keys_reload():
    key_pool.reload()
    return {"ok": True, **key_pool.status()}


@app.get("/agent/metrics")
async def metrics_endpoint(window: float | None = None):
    return agent_store.compute_metrics(sid=None, window_seconds=window)


@app.get("/agent/metrics/{sid}")
async def metrics_sid(sid: str, window: float | None = None):
    return agent_store.compute_metrics(sid=sid, window_seconds=window)


@app.get("/agent/auth_status")
async def auth_status():
    return {"install": _auth_status, "runtime": agent_auth.snapshot()}


@app.get("/agent/self")
async def agent_self_endpoint():
    """Self-introspection snapshot. Cheap, no secrets. Safe to expose to UI."""
    import agent_self

    return agent_self.status(start_ts=_PROCESS_START)


@app.get("/agent/prod/{what}")
async def agent_prod_endpoint(what: str, lines: int = 50, grep: str | None = None, unit: str = "webchat", n: int = 10, ref: str = "HEAD~1"):
    """Read-only host observation. Whitelisted commands only."""
    import agent_prod

    fns = {
        "uptime": lambda: agent_prod.uptime(),
        "df": lambda: agent_prod.df(),
        "systemctl_status": lambda: agent_prod.systemctl_status(unit),
        "journalctl": lambda: agent_prod.journalctl(lines=lines, grep=grep),
        "git_log": lambda: agent_prod.git_log(n=n),
        "git_diff": lambda: agent_prod.git_diff(ref=ref),
    }
    fn = fns.get(what)
    if fn is None:
        raise HTTPException(status_code=404, detail=f"unknown what={what!r}; allowed: {list(fns)}")
    return fn()


class _PROpen(BaseModel):
    branch: str
    title: str
    body: str = ""
    files: dict[str, str]
    base: str = "main"


@app.post("/agent/pr/open")
async def agent_pr_open(req: _PROpen):
    """Open a GitHub PR with files Kira authored. Branch must be `kira/*`."""
    import agent_pr

    return agent_pr.open_pr(
        branch=req.branch,
        title=req.title,
        body=req.body,
        files=req.files,
        base=req.base,
    )


@app.get("/agent/health")
async def agent_health():
    """Aggregate health snapshot.

    Combines: process uptime, in-flight session count, key-pool status,
    today/month credits + simple linear forecast, last 24h tool error
    rate, and a single 'status' field (ok|degraded|critical) for
    Telegram/monitoring alerts.
    """
    import datetime as _dt

    now = time.time()
    uptime = now - _PROCESS_START

    # in-flight = count of registered cancel events
    try:
        in_flight = len(agent_runtime._CANCEL_EVENTS)
        in_flight_sids = list(agent_runtime._CANCEL_EVENTS.keys())[:20]
    except Exception:
        in_flight = 0
        in_flight_sids = []

    # keys
    try:
        keys = key_pool.status()
    except Exception as e:
        keys = {"error": type(e).__name__ + ": " + str(e)[:200]}

    # credits + forecast
    try:
        day_credits = float(agent_store.get_today_credits())
        month_credits = float(agent_store.get_month_credits())
    except Exception:
        day_credits = 0.0
        month_credits = 0.0
    utc_now = _dt.datetime.now(_dt.timezone.utc)
    seconds_into_day = utc_now.hour * 3600 + utc_now.minute * 60 + utc_now.second
    seconds_in_day = 86400
    fraction_done = max(seconds_into_day, 1) / seconds_in_day
    day_forecast = day_credits / fraction_done if fraction_done > 0 else day_credits
    # month: days passed / days in month
    if utc_now.month == 12:
        next_month = utc_now.replace(year=utc_now.year + 1, month=1, day=1)
    else:
        next_month = utc_now.replace(month=utc_now.month + 1, day=1)
    days_in_month = (next_month - utc_now.replace(day=1)).days
    days_passed = max((utc_now - utc_now.replace(day=1)).total_seconds() / 86400, 0.01)
    month_forecast = (month_credits / days_passed) * days_in_month

    # 24h tool error rate
    try:
        m24 = agent_store.compute_metrics(sid=None, window_seconds=86400)
        success_rate = m24.get("success_rate")
        total_24h = int(m24.get("total") or 0)
        fail_24h = int(m24.get("fail") or 0)
        hook_denies_24h = int(m24.get("hook_denies") or 0)
        top_errors = m24.get("top_errors", [])
    except Exception:
        success_rate, total_24h, fail_24h, hook_denies_24h, top_errors = None, 0, 0, 0, []

    # status classification
    status = "ok"
    reasons: list[str] = []
    pool_size = int(keys.get("pool_size") or 1) if isinstance(keys, dict) else 1
    banned = 0
    for k in (keys.get("keys") if isinstance(keys, dict) else []) or []:
        if k.get("banned"):
            banned += 1
    if banned >= pool_size:
        status = "critical"
        reasons.append("all api keys banned")
    elif banned > 0:
        status = "degraded"
        reasons.append(f"{banned}/{pool_size} api keys banned")
    if KIRA_MONTHLY_LIMIT > 0 and month_forecast > KIRA_MONTHLY_LIMIT:
        if status == "ok":
            status = "degraded"
        reasons.append(f"month forecast {month_forecast:.1f} exceeds limit {KIRA_MONTHLY_LIMIT:.0f}")
    if KIRA_MONTHLY_LIMIT > 0 and month_credits >= KIRA_MONTHLY_LIMIT * 0.95:
        status = "critical"
        reasons.append("monthly budget >=95% used")
    if success_rate is not None and total_24h >= 20 and success_rate < 0.5:
        if status == "ok":
            status = "degraded"
        reasons.append(f"24h tool success_rate {success_rate:.0%} below 50%")

    return {
        "ok": True,
        "status": status,
        "reasons": reasons,
        "uptime_seconds": round(uptime, 1),
        "started_at": _dt.datetime.fromtimestamp(_PROCESS_START, tz=_dt.timezone.utc).isoformat(),
        "in_flight": in_flight,
        "in_flight_sids": in_flight_sids,
        "keys": keys,
        "credits": {
            "day": round(day_credits, 4),
            "day_limit": KIRA_DAILY_LIMIT,
            "day_forecast": round(day_forecast, 2),
            "day_fraction_done": round(fraction_done, 3),
            "month": round(month_credits, 4),
            "month_limit": KIRA_MONTHLY_LIMIT,
            "month_forecast": round(month_forecast, 2),
            "month_days_passed": round(days_passed, 2),
            "month_days_total": days_in_month,
        },
        "tools_24h": {
            "total": total_24h,
            "fail": fail_24h,
            "success_rate": (round(success_rate, 4) if success_rate is not None else None),
            "hook_denies": hook_denies_24h,
            "top_errors": top_errors,
        },
    }


@app.get("/agent/coverage")
async def coverage_endpoint():
    import agent_coverage

    return agent_coverage.status()


@app.get("/agent/coverage/file")
async def coverage_file(path: str):
    import agent_coverage

    return agent_coverage.file_detail(path)


@app.post("/agent/coverage/run")
async def coverage_run(timeout: int = 120):
    import agent_coverage

    # capped to 5 minutes to avoid abuse
    return agent_coverage.run(timeout=min(max(timeout, 5), 300))


@app.get("/skills/{name}")
async def skill_get(name: str):
    body = agent_skills.load_skill(name)
    if body is None:
        raise HTTPException(status_code=404)
    return {"name": name, "body": body}


@app.post("/skills")
async def skill_create(body: dict):
    """Create a new skill file from the web UI.

    Body: {"name": "...", "description": "...", "body": "..."}
    Returns 200 {"ok": true, "file": "..."} or 400 {"ok": false, "error": "..."}.
    """
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid body"}, status_code=400)
    res = agent_skills.create_skill(
        name=str(body.get("name") or ""),
        description=str(body.get("description") or ""),
        body=str(body.get("body") or ""),
    )
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)
    return res


@app.get("/agent/plan/{sid}")
async def agent_plan_get(sid: str, request: Request):
    uid = agent_auth.current_user_id(request)
    owner = agent_store.session_owner(sid)
    if owner is not None and owner != uid:
        raise HTTPException(status_code=403, detail="forbidden")
    p = agent_store.get_meta(sid, "plan", {"items": []})
    return p if isinstance(p, dict) else {"items": []}


@app.get("/agent/actions")
async def agent_actions(request: Request, sid: str | None = None, limit: int = 200):
    uid = agent_auth.current_user_id(request)
    if sid:
        owner = agent_store.session_owner(sid)
        if owner is not None and owner != uid:
            raise HTTPException(status_code=403, detail="forbidden")
    return {"actions": agent_store.list_actions(sid=sid, limit=limit)}


@app.get("/agent/actions/{aid}")
async def agent_action_get(aid: int):
    a = agent_store.get_action(aid)
    if not a:
        raise HTTPException(status_code=404)
    return a


@app.post("/agent/actions/{aid}/rollback")
async def agent_action_rollback(aid: int):
    import shutil

    a = agent_store.get_action(aid)
    if not a:
        raise HTTPException(status_code=404, detail="action not found")
    bak = a.get("backup")
    f = a.get("file")
    if not bak or not f:
        raise HTTPException(status_code=400, detail="no backup recorded")
    if not os.path.exists(bak):
        raise HTTPException(status_code=404, detail=f"backup missing: {bak}")
    # Save current as a fresh backup then restore
    if os.path.exists(f):
        ts = int(__import__("time").time())
        shutil.copy2(f, f + f".pre_rollback.{ts}")
    shutil.copy2(bak, f)
    try:
        agent_store.log_action(a.get("sid") or "", "_rollback", {"action_id": aid, "file": f, "from": bak}, ok=True)
    except Exception:
        pass
    return {"ok": True, "restored": f, "from": bak}


@app.get("/", response_class=HTMLResponse)
async def root():
    with open(os.path.join(os.path.dirname(__file__), "index.html"), "r", encoding="utf-8") as f:
        return f.read()


def _ensure_system(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not msgs or msgs[0].get("role") != "system":
        return [{"role": "system", "content": SYSTEM_PROMPT}] + msgs
    return msgs


def _sse(data: dict | str) -> bytes:
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=False)
    return ("data: " + data + "\n\n").encode("utf-8")


def _chunk(model: str, delta: str | None = None, finish: str | None = None) -> bytes:
    obj = {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": delta} if delta is not None else {},
                "finish_reason": finish,
            }
        ],
    }
    return _sse(obj)


def _extract_text_and_images(content: Any) -> tuple[str, list[dict]]:
    """OpenAI-style content -> (text, [image_block,...]) for Q API."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return "", []
    texts: list[str] = []
    images: list[dict] = []
    for p in content:
        t = p.get("type") if isinstance(p, dict) else None
        if t == "text":
            texts.append(p.get("text", ""))
        elif t == "image_url":
            url = (p.get("image_url") or {}).get("url", "")
            # expect data URL: data:image/png;base64,XXXX
            if url.startswith("data:"):
                try:
                    head, b64 = url.split(",", 1)
                    mime = head[5:].split(";", 1)[0]  # 'image/png'
                    fmt = mime.split("/", 1)[-1].lower()
                    if fmt == "jpg":
                        fmt = "jpeg"
                    if fmt not in ("png", "jpeg", "gif", "webp"):
                        fmt = "png"
                    images.append({"format": fmt, "source": {"bytes": b64}})
                except Exception:
                    pass
    return "\n".join(texts), images


def _convert_messages_to_q(msgs: list[dict[str, Any]]) -> tuple[dict, list, list[dict]]:
    """OpenAI msgs -> (currentMessage, history, images_for_current). Images on previous
    user turns are dropped to keep history light; only the last user turn keeps images.
    """
    sys_text = ""
    pairs: list[tuple[str, str]] = []
    cur_user = None
    last_images: list[dict] = []
    for m in msgs:
        role = m.get("role")
        c = m.get("content")
        text, imgs = _extract_text_and_images(c) if c is not None else ("", [])
        if role == "system":
            sys_text += text + "\n"
        elif role == "user":
            cur_user = text
            last_images = imgs  # overwrite — only the last user turn carries images
        elif role == "assistant" and cur_user is not None:
            pairs.append((cur_user, text))
            cur_user = None
            last_images = []  # images consumed by previous turn, drop them
    if cur_user is None:
        cur_user = ""
    history: list[dict] = []
    for u, a in pairs:
        history.append({"userInputMessage": {"content": u, "origin": "AI_EDITOR"}})
        history.append({"assistantResponseMessage": {"content": a}})
    last_user = cur_user
    if sys_text.strip():
        last_user = sys_text.strip() + "\n\n" + last_user
    current = {
        "userInputMessage": {
            "content": last_user,
            "modelId": None,
            "origin": "AI_EDITOR",
        }
    }
    return current, history, last_images


def _parse_eventstream(buf: bytearray):
    """Yield (headers_bytes, payload_bytes) from AWS vnd.amazon.eventstream buffer.
    Mutates buf to leave only unconsumed tail."""
    out = []
    while len(buf) >= 12:
        total_len, headers_len = struct.unpack(">II", bytes(buf[:8]))
        if total_len < 16 or total_len > 16 * 1024 * 1024:
            buf.clear()
            return out
        if len(buf) < total_len:
            return out
        msg = bytes(buf[:total_len])
        del buf[:total_len]
        headers = msg[12 : 12 + headers_len]
        payload = msg[12 + headers_len : total_len - 4]
        out.append((headers, payload))
    return out


def _es_event_type(headers: bytes) -> str:
    # parse just enough to find :event-type
    i = 0
    et = ""
    while i < len(headers):
        nlen = headers[i]
        i += 1
        name = headers[i : i + nlen].decode("utf-8", "replace")
        i += nlen
        htype = headers[i]
        i += 1
        if htype == 7:  # string
            vlen = struct.unpack(">H", headers[i : i + 2])[0]
            i += 2
            val = headers[i : i + vlen].decode("utf-8", "replace")
            i += vlen
            if name == ":event-type":
                et = val
        else:
            # skip — types we don't handle here
            break
    return et


async def stream_q(model_id: str, msgs: list):
    if not KIRO_API_KEY:
        yield _sse({"error": "KIRO_API_KEY не настроен"})
        return
    current, history, images = _convert_messages_to_q(msgs)
    current["userInputMessage"]["modelId"] = model_id
    if images:
        current["userInputMessage"]["images"] = images
    body = {
        "conversationState": {
            "chatTriggerType": "MANUAL",
            "conversationId": str(uuid.uuid4()),
            "currentMessage": current,
            "history": history,
        }
    }
    headers = {
        "Authorization": f"Bearer {key_pool.current() or KIRO_API_KEY}",
        "Content-Type": "application/x-amz-json-1.0",
        "X-Amz-Target": "AmazonCodeWhispererStreamingService.GenerateAssistantResponse",
        "tokentype": "API_KEY",
        "User-Agent": "aws-sdk-rust/1.3.14 app/AmazonQ-For-CLI",
        "Accept": "*/*",
    }
    label = f"q/{model_id}"
    try:
        async with httpx.AsyncClient(timeout=300) as cx:
            async with cx.stream("POST", KIRO_Q_URL, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    err = (await resp.aread()).decode("utf-8", "replace")
                    # Surface the *full* upstream body so blind 400s (e.g.
                    # ValidationException) reach the user/logs instead of
                    # dying silently. Cap is generous; the JSON typically
                    # holds a single `message` field.
                    yield _sse(
                        {
                            "error": f"q {resp.status_code}: {err[:600]}",
                            "status": resp.status_code,
                            "body": err[:4000],
                        }
                    )
                    return
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    for hdrs, payload in _parse_eventstream(buf):
                        et = _es_event_type(hdrs)
                        if et == "assistantResponseEvent":
                            try:
                                obj = json.loads(payload.decode("utf-8", "replace"))
                                txt = obj.get("content")
                                if txt:
                                    yield _chunk(label, delta=txt)
                            except Exception:
                                pass
                        elif et in (
                            "toolUseEvent",
                            "codeReferenceEvent",
                            "messageMetadataEvent",
                            "initial-response",
                            "",
                            "followupPromptEvent",
                        ):
                            continue
        yield _chunk(label, finish="stop")
        yield b"data: [DONE]\n\n"
    except Exception as e:
        print(f"[stream_q] {type(e).__name__}: {e}")
        msg = f"q upstream: {e!s}" if _ERR_VERBOSE else f"q upstream: {type(e).__name__}"
        yield _sse({"error": msg})


async def stream_omni(model: str, msgs: list):
    async with httpx.AsyncClient(timeout=300) as cx:
        try:
            async with cx.stream(
                "POST",
                f"{OMNI_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {OMNI_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "messages": msgs, "stream": True},
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    err = body.decode("utf-8", errors="replace")[:600]
                    yield _sse({"error": err})
                    return
                async for line in resp.aiter_lines():
                    if line:
                        yield (line + "\n").encode("utf-8")
        except Exception as e:
            print(f"[stream_omni] {type(e).__name__}: {e}")
            msg = f"upstream: {e!s}" if _ERR_VERBOSE else f"upstream: {type(e).__name__}"
            yield _sse({"error": msg})


@app.post("/chat")
async def chat(req: ChatRequest):
    if agent_freeze.is_frozen():
        info = agent_freeze.freeze_info()
        return JSONResponse({"error": "frozen", "reason": info.get("reason")}, status_code=503)
    model = req.model or DEFAULT_MODEL
    if model not in MODEL_IDS:
        return JSONResponse({"error": f"unknown model: {model}"}, status_code=400)

    msgs = _ensure_system(list(req.messages))
    if model.startswith("q/"):
        return StreamingResponse(stream_q(model[2:], msgs), media_type="text/event-stream")
    return StreamingResponse(stream_omni(model, msgs), media_type="text/event-stream")


def _extract_bearer(request: Request) -> str | None:
    """Pull the raw bearer token from Authorization header. Returns None if absent."""
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h or not h.lower().startswith("bearer "):
        return None
    return h[7:].strip() or None


@app.get("/agent/freeze")
async def agent_freeze_status():
    """Public read-only snapshot. Tells callers if /agent is currently disabled."""
    return agent_freeze.freeze_info()


@app.post("/agent/freeze")
async def agent_freeze_set(request: Request, body: dict | None = None):
    """Master-token only. Body: {"reason": "..."}"""
    tok = _extract_bearer(request)
    if not agent_freeze.is_master_token(tok):
        return JSONResponse({"error": "master token required"}, status_code=403)
    reason = ""
    if isinstance(body, dict):
        reason = str(body.get("reason") or "")
    return agent_freeze.freeze(reason)


@app.post("/agent/unfreeze")
async def agent_unfreeze(request: Request):
    """Master-token only. Removes the freeze flag."""
    tok = _extract_bearer(request)
    if not agent_freeze.is_master_token(tok):
        return JSONResponse({"error": "master token required"}, status_code=403)
    return agent_freeze.unfreeze()

# ---------- Self-improvement: propose-only mode ----------
# Kira can be asked to look back at recent answers, score them, and write a
# Markdown proposal for tweaking her system prompt. The proposal lands in
# ~/notebook/proposals/ and Oleg decides whether to open a PR. Nothing is
# auto-applied. Master-token gated because it costs LLM calls.
import agent_self_improve  # noqa: E402


@app.get("/agent/proposals")
async def proposals_list(request: Request):
    """List existing improvement proposals. Master-token required."""
    tok = _extract_bearer(request)
    if not agent_freeze.is_master_token(tok):
        return JSONResponse({"error": "master token required"}, status_code=403)
    return {"proposals": agent_self_improve.list_proposals()}


@app.post("/agent/propose_improvement")
async def proposals_create(request: Request, body: dict | None = None):
    """Run scoring on recent sessions, then ask LLM to propose a prompt tweak.

    Body: {"n": 10, "threshold": 7.0}  (all optional)
    Master-token gated.
    """
    tok = _extract_bearer(request)
    if not agent_freeze.is_master_token(tok):
        return JSONResponse({"error": "master token required"}, status_code=403)
    body = body or {}
    n = int(body.get("n") or 10)
    threshold = float(body.get("threshold") or 7.0)
    n = max(1, min(50, n))

    # Gather recent user->assistant pairs across recent sessions.
    sessions = agent_store.list_sessions(limit=20)
    pairs: list[dict] = []
    for sess in sessions:
        sid = sess.get("sid") if isinstance(sess, dict) else None
        if not sid:
            continue
        msgs = agent_store.load_history(sid) or []
        last_user = None
        for m in msgs:
            role = m.get("role")
            content = m.get("content") or ""
            if role == "user":
                last_user = content if isinstance(content, str) else str(content)
            elif role == "assistant" and last_user:
                txt = content if isinstance(content, str) else str(content)
                if txt.strip():
                    pairs.append({"user": last_user, "assistant": txt})
                last_user = None
        if len(pairs) >= n:
            break
    pairs = pairs[:n]
    if not pairs:
        return {"ok": False, "error": "no recent assistant messages to evaluate"}

    # Score each pair, keep the low scorers.
    api_key = agent_keys.key_pool.current() or ""
    scored = []
    for p in pairs:
        s = await agent_self_improve.score_answer(
            api_key, p["user"], p["assistant"]
        )
        scored.append({**p, "score": s})
    weak = [s for s in scored if (s["score"].get("overall") or 0) < threshold]
    if not weak:
        return {
            "ok": True,
            "saved": None,
            "reason": "no samples below threshold",
            "scored_count": len(scored),
            "threshold": threshold,
        }

    # Build proposal.
    try:
        from pathlib import Path as _P
        current_prompt = (_P(__file__).parent / "agent_system_prompt.txt").read_text("utf-8")
    except Exception:
        current_prompt = ""
    prop = await agent_self_improve.propose_revision(
        api_key, weak, current_prompt
    )
    md = prop.get("markdown") or ""
    if not md.strip():
        return {
            "ok": False,
            "error": "proposer returned empty",
            "raw": prop.get("raw", "")[:300],
        }

    header = (
        f"<!-- generated {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime())} -->\n"
        f"<!-- scored {len(scored)} samples, {len(weak)} below threshold {threshold} -->\n\n"
    )
    saved = agent_self_improve.save_proposal(header + md, slug="prompt-tweak")
    return {
        "ok": True,
        "saved": saved,
        "scored_count": len(scored),
        "weak_count": len(weak),
        "threshold": threshold,
    }


import shutil as _shutil

agent_store.init()

KIRA_SESSION_TTL_DAYS = int(os.environ.get("KIRA_SESSION_TTL_DAYS", "30"))


# Cost limits (override via env / systemd unit).
KIRA_SESSION_LIMIT = float(os.environ.get("KIRA_SESSION_LIMIT", "5"))
KIRA_DAILY_LIMIT = float(os.environ.get("KIRA_DAILY_LIMIT", "30"))
KIRA_MONTHLY_LIMIT = float(os.environ.get("KIRA_MONTHLY_LIMIT", "800"))


def _cost_limit_check(sid: str, current_turn_credits: float):
    sess_total = agent_store.get_session_credits(sid) + current_turn_credits
    day_total = agent_store.get_today_credits() + current_turn_credits
    month_total = agent_store.get_month_credits() + current_turn_credits
    if KIRA_SESSION_LIMIT > 0 and sess_total >= KIRA_SESSION_LIMIT:
        return f"Лимит сессии исчерпан: {sess_total:.2f} / {KIRA_SESSION_LIMIT:.2f} credits. Начните новую сессию."
    if KIRA_DAILY_LIMIT > 0 and day_total >= KIRA_DAILY_LIMIT:
        return f"Дневной лимит исчерпан: {day_total:.2f} / {KIRA_DAILY_LIMIT:.2f} credits. Попробуйте завтра."
    if KIRA_MONTHLY_LIMIT > 0 and month_total >= KIRA_MONTHLY_LIMIT:
        return f"Месячный лимит исчерпан: {month_total:.2f} / {KIRA_MONTHLY_LIMIT:.2f} credits."
    return None


agent_runtime._cost_limit_exceeded = _cost_limit_check

# In-memory cache so we don't re-read SQLite on every turn of an active session.
# Key is (user_id, sid) so two users with the same sid don't collide.
#
# Bounded LRU: an unbounded dict was a slow memory leak in prod — every new
# session (TTL=30d) accumulated its full history (incl. base64 images) in
# RAM forever. The cache only saves a SQLite roundtrip per turn; on eviction
# we fall back to load_history(), which is cheap. Tune via KIRA_SESSION_CACHE_MAX.
from collections import OrderedDict
import threading as _threading

_SESSION_CACHE_MAX = int(os.environ.get("KIRA_SESSION_CACHE_MAX", "128"))


class _SessionCache:
    """Tiny thread-safe LRU. dict-like surface so callers stay simple."""

    def __init__(self, maxsize: int):
        self.maxsize = max(1, maxsize)
        self._d: "OrderedDict[Any, list[dict]]" = OrderedDict()
        self._lock = _threading.Lock()

    def get(self, key, default=None):
        with self._lock:
            v = self._d.get(key, default)
            if v is not default and key in self._d:
                self._d.move_to_end(key)
            return v

    def __setitem__(self, key, value) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def pop(self, key, default=None):
        with self._lock:
            return self._d.pop(key, default)

    def __contains__(self, key) -> bool:
        with self._lock:
            return key in self._d

    def __len__(self) -> int:
        with self._lock:
            return len(self._d)


_AGENT_SESSIONS: _SessionCache = _SessionCache(_SESSION_CACHE_MAX)


@app.post("/agent")
async def agent_endpoint(req: AgentRequest, request: Request):
    if agent_freeze.is_frozen():
        info = agent_freeze.freeze_info()
        return JSONResponse({"error": "frozen", "reason": info.get("reason")}, status_code=503)
    if not KIRO_API_KEY:
        return JSONResponse({"error": "KIRO_API_KEY not set"}, status_code=400)
    user_id = agent_auth.current_user_id(request)
    model = req.model or "claude-opus-4.7"
    if model.startswith("q/"):
        model = model[2:]
    sid = req.session_id or uuid.uuid4().hex[:12]
    # Ownership check: if caller supplied an existing sid, it must be theirs
    # (or legacy / NULL). Reject foreign sids early.
    if req.session_id:
        existing_owner = agent_store.session_owner(sid)
        if existing_owner is not None and existing_owner != user_id:
            return JSONResponse({"error": "forbidden"}, status_code=403)
    cache_key = (user_id, sid)
    hist = _AGENT_SESSIONS.get(cache_key)
    if hist is None:
        hist = agent_store.load_history(sid, owner_id=user_id)
        if hist is not None:
            _AGENT_SESSIONS[cache_key] = hist

    # Pre-flight cost check so we don't even open the stream.
    pre_err = _cost_limit_check(sid, 0.0)
    if pre_err:

        async def err_gen():
            yield ("data: " + json.dumps({"type": "meta", "session_id": sid, "model": model}) + "\n\n").encode()
            yield ("data: " + json.dumps({"type": "error", "message": pre_err}) + "\n\n").encode()
            yield ("data: " + json.dumps({"type": "done"}) + "\n\n").encode()

        return StreamingResponse(err_gen(), media_type="text/event-stream")

    async def gen():
        nonlocal hist
        if hist is None:
            hist = []
            _AGENT_SESSIONS[cache_key] = hist
        baseline = agent_store.get_session_credits(sid, owner_id=user_id)
        agent_images = None
        if req.images:
            agent_images = []
            for im in req.images:
                fmt = (im.get("format") or "png").lower()
                b64 = im.get("data_base64") or im.get("data") or ""
                if not b64:
                    continue
                agent_images.append({"format": fmt, "source": {"bytes": b64}})
        async for ev in agent_runtime.run_agent(
            key_pool.current() or KIRO_API_KEY, req.prompt, model, session_id=sid, history=hist, images=agent_images,
            max_turns=req.max_turns,
        ):
            try:
                if ev.startswith(b"data: "):
                    obj = json.loads(ev[6:].decode("utf-8", "replace").strip())
                    if obj.get("type") == "stats" and "credits" in obj:
                        last_credits = float(obj["credits"])
                        # persist immediately so /agent/limits sees fresh value
                        try:
                            agent_store.record_credits(sid, baseline + last_credits, owner_id=user_id)
                        except Exception as e:
                            print(f"[agent_store] credit update failed: {e}")
            except Exception:
                pass
            yield ev
        try:
            title = agent_store.derive_title(hist)
            agent_store.save_session(sid, hist, model, title, owner_id=user_id)
        except Exception as e:
            print(f"[agent_store] save failed: {e}")

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/agent/stop/{sid}")
async def agent_stop(sid: str):
    ok = agent_runtime.request_cancel(sid)
    return JSONResponse({"ok": ok})


@app.get("/agent/sessions")
async def agent_sessions_list(request: Request):
    uid = agent_auth.current_user_id(request)
    return JSONResponse({"sessions": agent_store.list_sessions(limit=200, owner_id=uid)})


@app.get("/agent/limits")
async def agent_limits(request: Request, session_id: str | None = None):
    uid = agent_auth.current_user_id(request)
    sess = agent_store.get_session_credits(session_id, owner_id=uid) if session_id else 0.0
    # When auth is on (uid != 'anon'), show this user's own daily/monthly
    # usage; the global counters stay available as overall_*.
    user_day = agent_store.get_user_today_credits(uid)
    user_month = agent_store.get_user_month_credits(uid)
    return JSONResponse(
        {
            "session_credits": round(sess, 4),
            "session_limit": KIRA_SESSION_LIMIT,
            "day_credits": round(user_day, 4),
            "day_limit": KIRA_DAILY_LIMIT,
            "month_credits": round(user_month, 4),
            "month_limit": KIRA_MONTHLY_LIMIT,
            "overall_day_credits": round(agent_store.get_today_credits(), 4),
            "overall_month_credits": round(agent_store.get_month_credits(), 4),
            "user_id": uid,
        }
    )


@app.get("/agent/sessions/{sid}")
async def agent_session_get(sid: str, request: Request):
    uid = agent_auth.current_user_id(request)
    owner = agent_store.session_owner(sid)
    if owner is not None and owner != uid:
        raise HTTPException(status_code=404)
    hist = _AGENT_SESSIONS.get((uid, sid)) or agent_store.load_history(sid, owner_id=uid)
    if hist is None:
        raise HTTPException(status_code=404)
    # Look up the saved model id (so the UI can pin it to the session).
    try:
        sess_model = agent_store.get_session_model(sid)
    except Exception:
        sess_model = None
    transcript = []
    # index tool results by toolUseId so we can attach them when we hit the call
    tool_results: dict[str, dict] = {}
    for m in hist:
        ctx = (m.get("userInputMessage") or {}).get("userInputMessageContext") or {}
        for tr in ctx.get("toolResults") or []:
            tid = tr.get("toolUseId")
            if not tid:
                continue
            parts = tr.get("content") or []
            text = ""
            for p in parts:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    text += p["text"]
            tool_results[tid] = {"status": tr.get("status", "success"), "output": text}

    # Map tool_use_id -> action row (id, has_diff, backup) for this session.
    actions_for_sid = agent_store.list_actions(sid=sid, limit=2000)
    tu_to_action: dict[str, dict] = {}
    for a in actions_for_sid:
        tuid = a.get("tool_use_id")
        if tuid:
            tu_to_action[tuid] = a

    for m in hist:
        if "userInputMessage" in m:
            ctx = m["userInputMessage"].get("userInputMessageContext", {}) or {}
            if ctx.get("toolResults"):
                continue
            txt = agent_store.extract_user_text(m["userInputMessage"].get("content", ""))
            if txt:
                transcript.append({"role": "user", "text": txt})
        elif "assistantResponseMessage" in m:
            arm = m["assistantResponseMessage"]
            txt = arm.get("content", "")
            if txt:
                transcript.append({"role": "assistant", "text": txt})
            for tu in arm.get("toolUses") or []:
                tid = tu.get("toolUseId")
                res = tool_results.get(tid, {})
                entry = {
                    "role": "tool",
                    "id": tid,
                    "name": tu.get("name", ""),
                    "input": tu.get("input", {}),
                    "status": res.get("status"),
                    "output": res.get("output"),
                }
                act = tu_to_action.get(tid)
                if act:
                    entry["action_id"] = act["id"]
                    if act.get("backup"):
                        entry["backup"] = act["backup"]
                # Special-case: split subagent blocks back into structured items
                if entry["name"] == "use_subagent" and isinstance(entry.get("output"), str):
                    blocks = re.split(
                        r"^=== Subagent #(\d+) \[(success|error)\] ===\s*\n", entry["output"], flags=re.MULTILINE
                    )
                    # blocks: ['', '1', 'success', '<rest1>', '2', 'success', '<rest2>', ...]
                    items = []
                    for i in range(1, len(blocks), 3):
                        idx = int(blocks[i]) - 1
                        status = blocks[i + 1]
                        body = blocks[i + 2].strip()
                        q = ""
                        if body.startswith("query:"):
                            line, _, rest = body.partition("\n")
                            q = line[len("query:") :].strip()
                            body = rest.strip()
                        items.append({"index": idx, "status": status, "query": q, "preview": body[:300]})
                    if items:
                        entry["subagents"] = items
                transcript.append(entry)
    plan = agent_store.get_meta(sid, "plan", {"items": []})
    if not isinstance(plan, dict):
        plan = {"items": []}
    return JSONResponse({"sid": sid, "transcript": transcript, "model": sess_model, "plan": plan})


class RenameRequest(BaseModel):
    title: str


@app.post("/agent/sessions/{sid}/rename")
async def agent_session_rename(sid: str, req: RenameRequest, request: Request):
    uid = agent_auth.current_user_id(request)
    ok = agent_store.rename_session(sid, req.title.strip()[:200], owner_id=uid)
    return JSONResponse({"ok": ok})


@app.delete("/agent/sessions/{sid}")
async def agent_session_delete(sid: str, request: Request):
    uid = agent_auth.current_user_id(request)
    _AGENT_SESSIONS.pop((uid, sid), None)
    # legacy single-key entries (pre multi-user) also cleaned up
    _AGENT_SESSIONS.pop(sid, None)
    ok = agent_store.delete_session(sid, owner_id=uid)
    if not ok:
        return JSONResponse({"ok": False}, status_code=403 if agent_store.session_owner(sid) else 200)
    import os
    import shutil

    base = os.path.realpath(os.path.join(os.path.dirname(__file__), "workspaces", sid))
    root = os.path.realpath(os.path.join(os.path.dirname(__file__), "workspaces"))
    if base.startswith(root + os.sep) and os.path.isdir(base):
        try:
            shutil.rmtree(base)
        except Exception as e:
            print(f"[sessions] rmtree failed: {e}")
    return JSONResponse({"ok": ok})


_SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WORKSPACES_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "workspaces"))


def _safe_workspace(sid: str) -> str:
    if not _SID_RE.match(sid or ""):
        raise HTTPException(status_code=400, detail="invalid sid")
    base = os.path.realpath(os.path.join(_WORKSPACES_ROOT, sid))
    if not (base == _WORKSPACES_ROOT or base.startswith(_WORKSPACES_ROOT + os.sep)):
        raise HTTPException(status_code=400, detail="invalid sid")
    return base


@app.get("/agent/file/{session_id}/{path:path}")
async def agent_file(session_id: str, path: str, request: Request):
    uid = agent_auth.current_user_id(request)
    owner = agent_store.session_owner(session_id)
    if owner is not None and owner != uid:
        raise HTTPException(status_code=403, detail="forbidden")
    base = _safe_workspace(session_id)
    target = os.path.realpath(os.path.join(base, path))
    if not (target == base or target.startswith(base + os.sep)):
        raise HTTPException(status_code=400, detail="invalid path")
    if not os.path.isfile(target):
        raise HTTPException(status_code=404)
    return FileResponse(target)


@app.post("/agent/upload/{sid}")
async def agent_upload(sid: str, request: Request, files: list[UploadFile] = File(...)):
    uid = agent_auth.current_user_id(request)
    owner = agent_store.session_owner(sid)
    if owner is not None and owner != uid:
        raise HTTPException(status_code=403, detail="forbidden")
    base = _safe_workspace(sid)
    os.makedirs(base, exist_ok=True)
    saved = []
    for f in files:
        name = re.sub(r"[^\w.\-]+", "_", f.filename or "file").strip("_") or "file"
        # avoid clobber
        target = os.path.join(base, name)
        i = 1
        while os.path.exists(target):
            stem, ext = os.path.splitext(name)
            target = os.path.join(base, f"{stem}_{i}{ext}")
            i += 1
        with open(target, "wb") as out:
            while chunk := await f.read(1 << 16):
                out.write(chunk)
        saved.append({"name": os.path.basename(target), "size": os.path.getsize(target)})
    return JSONResponse({"ok": True, "saved": saved, "sid": sid})


@app.post("/agent/reset")
async def agent_reset(req: AgentRequest, request: Request):
    uid = agent_auth.current_user_id(request)
    if req.session_id:
        _AGENT_SESSIONS.pop((uid, req.session_id), None)
        _AGENT_SESSIONS.pop(req.session_id, None)
    return JSONResponse({"ok": True})


@app.get("/usage")
async def usage():
    """Provider-routed usage endpoint.

    Delegates to the active LLM provider's usage() so /usage works for any
    backend that exposes account-level limits. Q returns the live usage
    breakdown; providers without a usage API surface {"supported": False}.
    """
    try:
        provider = llm.get_provider()
    except Exception as e:
        return JSONResponse({"error": f"provider unavailable: {type(e).__name__}"},
                            status_code=500)
    try:
        data = await provider.usage()
    except Exception as e:
        print(f"[usage] {provider.name} failed: {type(e).__name__}: {e}")
        return JSONResponse({"error": type(e).__name__}, status_code=500)
    if not isinstance(data, dict):
        return JSONResponse({"error": "provider returned non-dict"}, status_code=500)
    data.setdefault("provider", provider.name)
    # Preserve the prior contract for clients: when Q is the provider and the
    # call succeeded, top-level fields (plan, used, limit, ...) sit alongside
    # `supported` / `status`. Unsupported providers get HTTP 200 with the
    # `supported: False` flag so the UI can render a friendly note.
    if data.get("status") == "no_key":
        return JSONResponse(data, status_code=400)
    if data.get("status") == "http_error":
        return JSONResponse(data, status_code=data.get("http_status", 502))
    return data
