"""OpenRouter adapter — OpenAI-compatible chat.completions SSE.

OpenRouter (https://openrouter.ai) is a unified gateway to ~200 models
(Claude, GPT, Gemini, Llama, DeepSeek, Qwen, …) speaking the OpenAI wire
format. One adapter therefore unlocks the entire long tail and lets users
bring their own key.

What this adapter covers:

    canonical Message[]   →  OpenAI `messages`   (incl. tool turns)
    canonical ToolSpec[]  →  OpenAI `tools` (function-calling shape)
    OpenAI SSE deltas     →  canonical StreamEvent

Streaming protocol (`text/event-stream`):

    data: {"choices":[{"delta":{"content":"hello"}}]}
    data: {"choices":[{"delta":{"tool_calls":[{...}]}}]}
    data: {"usage":{"prompt_tokens":12,"completion_tokens":3}}    (last frame)
    data: [DONE]

Tool calls arrive in fragments indexed by `tool_calls[i].index`; the
function arguments stream as a JSON string built up across deltas. We
accumulate locally and flush a single canonical `tool_call` event when
the model finishes the function (signalled by `finish_reason='tool_calls'`
or by stream end).

This module deliberately depends only on httpx; no OpenAI SDK is needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from .base import LLMProvider, Message, StreamEvent, ToolCall, ToolSpec, Usage

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# Canonical → OpenAI conversion
# ---------------------------------------------------------------------------


def _content_to_openai(content: Any) -> Any:
    """Pass strings through; for content-part lists, drop Q-specific shapes.

    OpenAI accepts string-only content for plain turns or a list of parts
    `[{"type":"text","text":..}, {"type":"image_url",..}]`. Most of Kira's
    canonical messages carry plain strings — multimodal is a future patch.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [p for p in content if isinstance(p, dict)]
    return str(content) if content is not None else ""


def messages_to_openai(messages: list[Message]) -> list[dict]:
    """Convert canonical Message[] to OpenAI `messages`.

    Roles map 1:1. Assistant tool calls become `tool_calls=[{id,type,function}]`.
    Tool result turns become `role='tool'` with `tool_call_id`.
    """
    out: list[dict] = []
    for m in messages:
        if m.role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id or "",
                "content": _content_to_openai(m.content),
            })
            continue
        d: dict[str, Any] = {"role": m.role,
                              "content": _content_to_openai(m.content)}
        if m.role == "assistant" and m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in m.tool_calls
            ]
            # OpenAI rejects empty assistant content when tool_calls present
            # only for some models; null is the universally-safe form.
            if not d["content"]:
                d["content"] = None
        out.append(d)
    return out


def toolspecs_to_openai(tools: list[ToolSpec]) -> list[dict]:
    """Convert ToolSpec → OpenAI `tools` (function-calling shape)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


# ---------------------------------------------------------------------------
# OpenAI stream delta → canonical StreamEvent
# ---------------------------------------------------------------------------


class _ToolCallAccumulator:
    """OpenAI streams each tool call's arguments as a string fragment per delta,
    keyed by `index`. We buffer the fragments and emit a finished ToolCall when
    the stream signals completion (`finish_reason='tool_calls'` or `done`).
    """

    def __init__(self) -> None:
        self.calls: dict[int, dict[str, Any]] = {}

    def absorb(self, deltas: list[dict]) -> None:
        for d in deltas:
            idx = d.get("index", 0)
            slot = self.calls.setdefault(
                idx, {"id": "", "name": "", "args": ""}
            )
            if d.get("id"):
                slot["id"] = d["id"]
            fn = d.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["args"] += fn["arguments"]

    def flush(self) -> list[ToolCall]:
        out: list[ToolCall] = []
        for _, slot in sorted(self.calls.items()):
            if not slot["name"]:
                continue
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["args"]}
            out.append(ToolCall(
                id=slot["id"] or f"call_{uuid.uuid4().hex[:8]}",
                name=slot["name"],
                arguments=args if isinstance(args, dict) else {"_value": args},
            ))
        self.calls.clear()
        return out


def _parse_sse(line: str) -> dict | None:
    """Parse a single SSE `data:` line. Returns None for keepalives / [DONE]."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OpenRouterProvider:
    """OpenAI-compatible LLM provider routed through openrouter.ai.

    Configure with the `OPENROUTER_API_KEY` env var (or pass `api_key=` to
    the constructor). Optional `OPENROUTER_BASE_URL` overrides the endpoint
    (useful for self-hosted compatible gateways).
    """

    name = "openrouter"
    # A representative sample — OpenRouter actually exposes ~200 models;
    # /agent/health surfaces this list and the UI just sends whatever model
    # string the user picks. We don't enforce a closed enum.
    supported_models = [
        "anthropic/claude-sonnet-4.5",
        "anthropic/claude-haiku-4.5",
        "openai/gpt-5",
        "openai/gpt-5-mini",
        "google/gemini-2.5-pro",
        "deepseek/deepseek-chat",
        "meta-llama/llama-3.3-70b-instruct",
        "qwen/qwen-2.5-72b-instruct",
    ]

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
        self.base_url = (base_url or os.environ.get("OPENROUTER_BASE_URL")
                         or OPENROUTER_BASE).rstrip("/")

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        # HTTP-Referer / X-Title are recommended by OpenRouter for analytics
        # and so models can be shown as called from Kira.
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.environ.get("KIRA_OPENROUTER_REFERER",
                                            "https://github.com/olegvrv21-del/kira"),
            "X-Title": os.environ.get("KIRA_OPENROUTER_TITLE", "Kira"),
        }

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        model: str,
        cancel: asyncio.Event | None = None,
        timeout: float = 300.0,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        import httpx  # local: keep llm.base dep-free

        body: dict[str, Any] = {
            "model": model,
            "messages": messages_to_openai(messages),
            "stream": True,
            "usage": {"include": True},  # opt-in to per-stream usage frame
        }
        if tools:
            body["tools"] = toolspecs_to_openai(tools)
            body["tool_choice"] = "auto"
        for k in ("temperature", "top_p", "max_tokens"):
            if extra and k in extra:
                body[k] = extra[k]

        acc = _ToolCallAccumulator()
        emitted_done = False

        try:
            headers = self._headers()
        except RuntimeError as e:
            yield StreamEvent(type="error", text=str(e))
            yield StreamEvent(type="done")
            return

        try:
            async with httpx.AsyncClient(timeout=timeout) as cx:
                async with cx.stream(
                    "POST", f"{self.base_url}/chat/completions",
                    headers=headers, json=body,
                ) as r:
                    if r.status_code >= 400:
                        err_body = await r.aread()
                        yield StreamEvent(
                            type="error",
                            text=f"openrouter HTTP {r.status_code}: {err_body[:400]!r}",
                        )
                        yield StreamEvent(type="done")
                        emitted_done = True
                        return

                    async for line in r.aiter_lines():
                        if cancel is not None and cancel.is_set():
                            yield StreamEvent(type="cancelled")
                            emitted_done = True
                            return
                        ev = _parse_sse(line)
                        if ev is None:
                            continue

                        # Usage frame (last chunk before [DONE] when usage.include=True)
                        if "usage" in ev and ev["usage"]:
                            u = ev["usage"]
                            yield StreamEvent(type="usage", usage=Usage(
                                input_tokens=int(u.get("prompt_tokens") or 0),
                                output_tokens=int(u.get("completion_tokens") or 0),
                                meta={k: v for k, v in u.items()
                                      if k not in ("prompt_tokens", "completion_tokens")},
                            ))

                        choices = ev.get("choices") or []
                        if not choices:
                            continue
                        ch = choices[0]
                        delta = ch.get("delta") or {}
                        if delta.get("content"):
                            yield StreamEvent(type="text", text=delta["content"])
                        tc_deltas = delta.get("tool_calls") or []
                        if tc_deltas:
                            acc.absorb(tc_deltas)

                        finish = ch.get("finish_reason")
                        if finish in ("tool_calls", "stop", "length",
                                      "content_filter"):
                            for tc in acc.flush():
                                yield StreamEvent(type="tool_call", tool=tc)
        except asyncio.CancelledError:
            yield StreamEvent(type="cancelled")
            emitted_done = True
            raise
        except Exception as e:
            yield StreamEvent(type="error",
                              text=f"openrouter stream failed: {type(e).__name__}: {e}")
        finally:
            # Final flush in case the upstream closed without a finish_reason.
            for tc in acc.flush():
                yield StreamEvent(type="tool_call", tool=tc)
            if not emitted_done:
                yield StreamEvent(type="done")

    async def health(self) -> dict[str, Any]:
        if not self.api_key:
            return {"name": self.name, "status": "no_key"}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as cx:
                r = await cx.get(f"{self.base_url}/auth/key",
                                 headers={"Authorization": f"Bearer {self.api_key}"})
            if r.status_code >= 400:
                return {"name": self.name, "status": "http_error",
                        "http_status": r.status_code}
            d = (r.json() or {}).get("data") or {}
            return {
                "name": self.name,
                "status": "ok",
                "label": d.get("label"),
                "models": self.supported_models,
            }
        except Exception as e:
            return {"name": self.name, "status": "error",
                    "error": type(e).__name__}

    async def usage(self) -> dict[str, Any]:
        """Map OpenRouter's /auth/key into the canonical usage shape used by
        the /usage HTTP route."""
        if not self.api_key:
            return {"supported": True, "status": "no_key",
                    "error": "OPENROUTER_API_KEY not set"}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as cx:
                r = await cx.get(f"{self.base_url}/auth/key",
                                 headers={"Authorization": f"Bearer {self.api_key}"})
            if r.status_code >= 400:
                return {"supported": True, "status": "http_error",
                        "http_status": r.status_code,
                        "error": r.text[:400]}
            d = (r.json() or {}).get("data") or {}
        except Exception as e:
            return {"supported": True, "status": "error",
                    "error": type(e).__name__}
        # OpenRouter reports `limit` (None = unlimited), `usage` (USD spent),
        # `is_free_tier`, `rate_limit`.
        limit = d.get("limit")
        used = d.get("usage") or 0.0
        return {
            "supported": True,
            "status": "ok",
            "plan": d.get("label") or "OpenRouter",
            "plan_type": "free" if d.get("is_free_tier") else "paid",
            "used": float(used),
            "limit": float(limit) if limit is not None else 0.0,
            "unit": "USD",
            "overage": 0.0, "overage_cap": 0.0, "overage_rate": 0.0,
            "overage_status": "",
            "reset_at": None,
        }


# Static protocol check: catches signature drift at import time.
_proto_check: LLMProvider = OpenRouterProvider()  # noqa: F841
