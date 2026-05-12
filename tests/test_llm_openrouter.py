"""Tests for llm/openrouter_provider.py — OpenAI-compatible SSE adapter.

We mock httpx at the transport layer (httpx.MockTransport) so the adapter
exercises the real SSE parser, header logic, retry handling, etc. The
fixture builds a fake `chat.completions` endpoint that streams a scripted
sequence of frames back.
"""
from __future__ import annotations

import asyncio
import json
from typing import Iterable

import httpx
import pytest

from llm.openrouter_provider import (
    OpenRouterProvider,
    _ToolCallAccumulator,
    _parse_sse,
    messages_to_openai,
    toolspecs_to_openai,
)
from llm.base import Message, StreamEvent, ToolCall, ToolSpec


# ---------- pure conversion -------------------------------------------------


def test_messages_to_openai_plain_text():
    out = messages_to_openai([
        Message(role="system", content="sys"),
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
    ])
    assert out == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_messages_to_openai_tool_call_roundtrip():
    msgs = [
        Message(role="user", content="read foo"),
        Message(role="assistant", content="", tool_calls=[
            ToolCall(id="t1", name="fs_read", arguments={"path": "/foo"}),
        ]),
        Message(role="tool", tool_call_id="t1", content="contents of foo"),
    ]
    out = messages_to_openai(msgs)
    # Assistant turn carries tool_calls and null content (OpenAI requirement)
    assert out[1]["role"] == "assistant"
    assert out[1]["content"] is None
    assert out[1]["tool_calls"][0]["function"]["name"] == "fs_read"
    assert json.loads(out[1]["tool_calls"][0]["function"]["arguments"]) == {"path": "/foo"}
    # Tool result turn
    assert out[2] == {"role": "tool", "tool_call_id": "t1",
                       "content": "contents of foo"}


def test_toolspecs_to_openai():
    out = toolspecs_to_openai([
        ToolSpec("fs_read", "read a file",
                 {"type": "object", "properties": {"path": {"type": "string"}}}),
    ])
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "fs_read"
    assert out[0]["function"]["parameters"]["properties"]["path"]["type"] == "string"


def test_toolspecs_empty_parameters_default():
    out = toolspecs_to_openai([ToolSpec("noop", "", {})])
    assert out[0]["function"]["parameters"] == {"type": "object", "properties": {}}


# ---------- SSE parsing -----------------------------------------------------


def test_parse_sse_keepalive_and_done():
    assert _parse_sse(": ping") is None
    assert _parse_sse("") is None
    assert _parse_sse("data: [DONE]") is None
    assert _parse_sse("data: {\"x\":1}") == {"x": 1}


def test_parse_sse_invalid_json():
    assert _parse_sse("data: not json") is None


# ---------- tool-call accumulator ------------------------------------------


def test_tool_call_accumulator_assembles_fragments():
    acc = _ToolCallAccumulator()
    acc.absorb([{"index": 0, "id": "t1",
                 "function": {"name": "fs_read", "arguments": "{\"pa"}}])
    acc.absorb([{"index": 0, "function": {"arguments": "th\":\"/foo\"}"}}])
    out = acc.flush()
    assert len(out) == 1
    assert out[0].name == "fs_read"
    assert out[0].arguments == {"path": "/foo"}


def test_tool_call_accumulator_handles_bad_json():
    acc = _ToolCallAccumulator()
    acc.absorb([{"index": 0, "function": {"name": "x", "arguments": "{broken"}}])
    out = acc.flush()
    assert out[0].arguments == {"_raw": "{broken"}


def test_tool_call_accumulator_multiple_indexed_calls():
    acc = _ToolCallAccumulator()
    acc.absorb([
        {"index": 0, "id": "a", "function": {"name": "f1", "arguments": "{}"}},
        {"index": 1, "id": "b", "function": {"name": "f2", "arguments": "{}"}},
    ])
    out = acc.flush()
    assert [t.name for t in out] == ["f1", "f2"]


# ---------- streaming end-to-end via httpx.MockTransport -------------------


def _sse(*frames: dict | str) -> bytes:
    """Encode a list of frames as SSE bytes."""
    lines = []
    for f in frames:
        if isinstance(f, str):
            lines.append(f"data: {f}")
        else:
            lines.append(f"data: {json.dumps(f)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def _install_transport(monkeypatch, response_bytes: bytes, status: int = 200):
    """Patch httpx.AsyncClient so it routes through a MockTransport returning
    the scripted SSE bytes."""

    def _handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=response_bytes,
                              headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(_handler)
    real = httpx.AsyncClient

    def _factory(*a, **kw):
        kw["transport"] = transport
        return real(*a, **kw)

    monkeypatch.setattr("httpx.AsyncClient", _factory)


def _run(stream_iter):
    async def go():
        out = []
        async for ev in stream_iter:
            out.append(ev)
        return out
    return asyncio.run(go())


def test_stream_text_then_done(monkeypatch):
    payload = _sse(
        {"choices": [{"delta": {"content": "hel"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "lo"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        "[DONE]",
    )
    _install_transport(monkeypatch, payload)
    p = OpenRouterProvider(api_key="or_test_key")
    events = _run(p.stream([Message(role="user", content="hi")],
                            [], model="openai/gpt-5"))
    types = [e.type for e in events]
    assert types[-1] == "done"
    text = "".join(e.text or "" for e in events if e.type == "text")
    assert text == "hello"
    usages = [e for e in events if e.type == "usage"]
    assert usages and usages[0].usage.input_tokens == 5


def test_stream_tool_call(monkeypatch):
    payload = _sse(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_x",
             "function": {"name": "fs_read", "arguments": "{\"pa"}}]},
                       "finish_reason": None}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "th\":\"/x\"}"}}]},
                       "finish_reason": "tool_calls"}]},
        "[DONE]",
    )
    _install_transport(monkeypatch, payload)
    p = OpenRouterProvider(api_key="or_test_key")
    events = _run(p.stream([Message(role="user", content="read x")],
                            [ToolSpec("fs_read", "", {})], model="openai/gpt-5"))
    tools = [e for e in events if e.type == "tool_call"]
    assert len(tools) == 1
    assert tools[0].tool.name == "fs_read"
    assert tools[0].tool.arguments == {"path": "/x"}
    assert events[-1].type == "done"


def test_stream_http_error_yields_error_and_done(monkeypatch):
    _install_transport(monkeypatch, b'{"error":"nope"}', status=401)
    p = OpenRouterProvider(api_key="or_test_key")
    events = _run(p.stream([Message(role="user", content="hi")],
                            [], model="openai/gpt-5"))
    assert any(e.type == "error" and "401" in (e.text or "") for e in events)
    assert events[-1].type == "done"


def test_stream_without_api_key_errors_cleanly(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    p = OpenRouterProvider(api_key="")
    events = _run(p.stream([Message(role="user", content="hi")],
                            [], model="openai/gpt-5"))
    assert events[0].type == "error"
    assert "OPENROUTER_API_KEY" in (events[0].text or "")
    assert events[-1].type == "done"


def test_stream_cancel_mid_stream(monkeypatch):
    payload = _sse(
        {"choices": [{"delta": {"content": "a"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "b"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "c"}, "finish_reason": "stop"}]},
        "[DONE]",
    )
    _install_transport(monkeypatch, payload)

    cancel = asyncio.Event()
    cancel.set()  # cancelled before first chunk
    p = OpenRouterProvider(api_key="or_test_key")
    events = _run(p.stream([Message(role="user", content="hi")],
                            [], model="openai/gpt-5", cancel=cancel))
    types = [e.type for e in events]
    assert "cancelled" in types


# ---------- health & usage --------------------------------------------------


def test_usage_no_key():
    p = OpenRouterProvider(api_key="")
    out = asyncio.run(p.usage())
    assert out["status"] == "no_key"


def test_usage_ok(monkeypatch):
    def _handler(req):
        return httpx.Response(200, json={"data": {
            "label": "My OR Key", "usage": 0.42, "limit": 10.0,
            "is_free_tier": False,
        }})
    transport = httpx.MockTransport(_handler)
    real = httpx.AsyncClient
    monkeypatch.setattr("httpx.AsyncClient",
                        lambda *a, **kw: real(*a, transport=transport, **kw))
    p = OpenRouterProvider(api_key="or_test_key")
    out = asyncio.run(p.usage())
    assert out["supported"] is True
    assert out["status"] == "ok"
    assert out["used"] == 0.42
    assert out["limit"] == 10.0
    assert out["unit"] == "USD"


def test_health_no_key():
    p = OpenRouterProvider(api_key="")
    out = asyncio.run(p.health())
    assert out["status"] == "no_key"


# ---------- registry integration -------------------------------------------


def test_registered_in_default_registry():
    from llm import available, get_provider
    assert "openrouter" in available()
    p = get_provider("openrouter")
    assert p.name == "openrouter"
