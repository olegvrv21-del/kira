"""Shared Q streaming helper: retry on 429/5xx, per-key concurrency cap.

Usage:
    async with QStream(api_key, body) as stream:
        async for et, payload in stream.frames():
            ...

The context manager:
  - Acquires a per-API-key semaphore (KIRA_Q_CONCURRENCY, default 3).
  - Retries the initial POST on 429/502/503/504 with exponential backoff + jitter.
  - Yields a synthetic ('_throttle', {'attempt': n, 'sleep': s, 'status': code})
    frame each time it backs off, so the agent can surface it in SSE.
  - Once the stream is open (200 OK), errors mid-stream are NOT retried (would
    duplicate billing); they propagate as exceptions.
"""
from __future__ import annotations

import asyncio
import os
import random
import struct
from typing import Any, AsyncIterator

import httpx

from agent_keys import key_pool

Q_URL = "https://q.us-east-1.amazonaws.com/?origin=KIRO_CLI"

_CONCURRENCY = int(os.environ.get("KIRA_Q_CONCURRENCY", "3"))
_MAX_RETRIES = int(os.environ.get("KIRA_Q_MAX_RETRIES", "6"))
_BASE_DELAY = float(os.environ.get("KIRA_Q_BASE_DELAY", "1.5"))   # seconds
_MAX_DELAY = float(os.environ.get("KIRA_Q_MAX_DELAY", "30"))

_SEMAPHORES: dict[str, asyncio.Semaphore] = {}
_SEM_LOCK = asyncio.Lock()

# Soft global throttle: if we just got 429, every other call for this key waits.
_COOLDOWN_UNTIL: dict[str, float] = {}


async def _get_sem(key: str) -> asyncio.Semaphore:
    async with _SEM_LOCK:
        s = _SEMAPHORES.get(key)
        if s is None:
            s = asyncio.Semaphore(_CONCURRENCY)
            _SEMAPHORES[key] = s
        return s


def _q_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/x-amz-json-1.0",
        "tokentype": "API_KEY",
        "X-Amz-Target": "AmazonCodeWhispererStreamingService.GenerateAssistantResponse",
        "User-Agent": "aws-sdk-rust/1.3.14 app/AmazonQ-For-CLI",
    }


def parse_frames(buf: bytearray):
    """AWS event-stream frame parser. Drains complete frames from buf in place."""
    import json
    while len(buf) >= 12:
        total_len = struct.unpack(">I", bytes(buf[0:4]))[0]
        if total_len <= 0 or total_len > 16 * 1024 * 1024:
            buf.clear()
            return
        if len(buf) < total_len:
            return
        frame = bytes(buf[:total_len])
        del buf[:total_len]
        headers_len = struct.unpack(">I", frame[4:8])[0]
        prelude_end = 12
        headers_end = prelude_end + headers_len
        payload = frame[headers_end:total_len - 4]
        # walk headers for :event-type
        et = None
        i = prelude_end
        while i < headers_end:
            name_len = frame[i]; i += 1
            name = frame[i:i + name_len].decode("utf-8", "replace"); i += name_len
            i += 1  # value type byte (string=7)
            vlen = struct.unpack(">H", frame[i:i + 2])[0]; i += 2
            val = frame[i:i + vlen].decode("utf-8", "replace"); i += vlen
            if name == ":event-type":
                et = val
        try:
            obj = json.loads(payload.decode("utf-8", "replace"))
        except Exception:
            obj = None
        yield et, obj


async def stream_q(api_key: str, body: dict, *, timeout: float = 300,
                   cancel_event: asyncio.Event | None = None) -> AsyncIterator[tuple]:
    """Yield (event_type, payload_obj) tuples. May yield ('_throttle', meta) on retry.

    Raises httpx.HTTPError on unrecoverable network failure, or RuntimeError
    on persistent 4xx/5xx after all retries.
    """
    sem = await _get_sem(api_key)
    async with sem:
        # Honour any active cooldown for this key
        cd = _COOLDOWN_UNTIL.get(api_key, 0)
        loop = asyncio.get_event_loop()
        wait = cd - loop.time()
        if wait > 0:
            yield ("_throttle", {"reason": "cooldown", "sleep": wait})
            await asyncio.sleep(wait)

        attempt = 0
        async with httpx.AsyncClient(timeout=timeout) as cx:
            while True:
                try:
                    async with cx.stream("POST", Q_URL,
                                         headers=_q_headers(api_key), json=body) as r:
                        # 401/403: try to rotate to a fallback key.
                        if r.status_code in (401, 403):
                            err_body = (await r.aread()).decode("utf-8", "replace")
                            new_key = key_pool.mark_bad(
                                api_key, reason=f"{r.status_code}: {err_body[:120]}")
                            if new_key and new_key != api_key:
                                yield ("_throttle", {
                                    "reason": f"key_rotated:{r.status_code}",
                                    "attempt": attempt + 1,
                                    "sleep": 0,
                                })
                                api_key = new_key
                                attempt += 1
                                if attempt > _MAX_RETRIES:
                                    raise RuntimeError(
                                        f"q {r.status_code} after key rotation: {err_body[:300]}")
                                continue
                            # No fallback or same key returned — surface error.
                            raise RuntimeError(f"q {r.status_code}: {err_body[:400]}")
                        # Decide if this status is retriable.
                        retriable = r.status_code in (429, 500, 502, 503, 504)
                        err: str | None = None
                        if r.status_code == 400:
                            err = (await r.aread()).decode("utf-8", "replace")
                            # Bedrock returns ThrottlingException /
                            # INSUFFICIENT_MODEL_CAPACITY with HTTP 400. Retry.
                            if ("ThrottlingException" in err
                                    or "INSUFFICIENT_MODEL_CAPACITY" in err
                                    or "ServiceUnavailable" in err):
                                retriable = True
                            else:
                                raise RuntimeError(f"q 400: {err[:400]}")
                        if retriable:
                            if err is None:
                                err = (await r.aread()).decode("utf-8", "replace")
                            attempt += 1
                            if attempt > _MAX_RETRIES:
                                raise RuntimeError(
                                    f"q {r.status_code} after {attempt} retries: {err[:300]}")
                            sleep = min(_MAX_DELAY,
                                        _BASE_DELAY * (2 ** (attempt - 1))) * (0.7 + 0.6 * random.random())
                            # arm cooldown for sibling calls
                            _COOLDOWN_UNTIL[api_key] = max(
                                _COOLDOWN_UNTIL.get(api_key, 0),
                                loop.time() + sleep)
                            yield ("_throttle", {
                                "reason": str(r.status_code),
                                "attempt": attempt,
                                "sleep": round(sleep, 2),
                            })
                            await asyncio.sleep(sleep)
                            continue
                        if r.status_code >= 400:
                            err = (await r.aread()).decode("utf-8", "replace")
                            raise RuntimeError(f"q {r.status_code}: {err[:400]}")
                        # success: drain frames
                        buf = bytearray()
                        async for chunk in r.aiter_bytes():
                            if cancel_event is not None and cancel_event.is_set():
                                yield ("_cancelled", {})
                                return
                            buf.extend(chunk)
                            for et, payload in parse_frames(buf):
                                yield (et, payload)
                    return
                except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
                    attempt += 1
                    if attempt > _MAX_RETRIES:
                        raise
                    sleep = min(_MAX_DELAY, _BASE_DELAY * (2 ** (attempt - 1)))
                    yield ("_throttle", {"reason": f"net:{type(e).__name__}",
                                          "attempt": attempt,
                                          "sleep": round(sleep, 2)})
                    await asyncio.sleep(sleep)
