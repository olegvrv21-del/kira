"""Tests for llm.mock_provider."""

from __future__ import annotations

import asyncio

import pytest

from llm import MockProvider, get_provider
from llm.base import Message, ToolSpec


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _collect(it):
    out = []
    async for ev in it:
        out.append(ev)
    return out


@pytest.mark.asyncio
async def test_text_script():
    p = MockProvider([{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}])
    events = await _collect(p.stream([Message(role="user", content="hi")], [], model="mock-1"))
    assert [e.type for e in events] == ["text", "text", "done"]
    assert "".join(e.text or "" for e in events) == "hello world"


@pytest.mark.asyncio
async def test_tool_call_script():
    p = MockProvider(
        [
            {"type": "text", "text": "thinking… "},
            {"type": "tool_call", "id": "t1", "name": "fs_read", "args": {"path": "/x"}},
            {"type": "text", "text": "done"},
        ]
    )
    events = await _collect(p.stream([], [], model="mock-1"))
    types = [e.type for e in events]
    assert types == ["text", "tool_call", "text", "done"]
    tc = next(e for e in events if e.type == "tool_call").tool
    assert tc.name == "fs_read"
    assert tc.arguments == {"path": "/x"}


@pytest.mark.asyncio
async def test_throttle_usage_error():
    p = MockProvider(
        [
            {"type": "throttle", "meta": {"sleep": 0.0, "reason": "429"}},
            {"type": "text", "text": "ok"},
            {"type": "usage", "input_tokens": 7, "output_tokens": 3},
            {"type": "error", "message": "transient"},
        ]
    )
    events = await _collect(p.stream([], [], model="mock-1"))
    assert events[0].type == "throttle"
    assert events[0].meta == {"sleep": 0.0, "reason": "429"}
    assert events[2].type == "usage"
    assert events[2].usage.input_tokens == 7
    assert events[3].type == "error"
    assert events[-1].type == "done"


@pytest.mark.asyncio
async def test_cancellation():
    cancel = asyncio.Event()
    cancel.set()
    p = MockProvider([{"type": "text", "text": "never"}], delay=0.0)
    events = await _collect(p.stream([], [], model="mock-1", cancel=cancel))
    assert events == [e for e in events if e.type == "cancelled"]
    assert events[-1].type == "cancelled"


@pytest.mark.asyncio
async def test_records_calls():
    p = MockProvider([{"type": "text", "text": "hi"}])
    tools = [ToolSpec(name="fs_read", description="", parameters={})]
    msgs = [Message(role="user", content="hello")]
    await _collect(p.stream(msgs, tools, model="mock-1", extra={"trace": "abc"}))
    assert len(p.calls) == 1
    assert p.calls[0]["model"] == "mock-1"
    assert p.calls[0]["tools"] == ["fs_read"]
    assert p.calls[0]["extra"] == {"trace": "abc"}


@pytest.mark.asyncio
async def test_callable_script_sees_messages():
    def script(msgs):
        last = msgs[-1].content if msgs else ""
        return [{"type": "text", "text": f"echo: {last}"}]

    p = MockProvider(script)
    events = await _collect(p.stream([Message(role="user", content="ping")], [], model="mock-1"))
    assert events[0].text == "echo: ping"


@pytest.mark.asyncio
async def test_raise_step_propagates():
    p = MockProvider([{"type": "raise", "message": "boom"}])
    with pytest.raises(RuntimeError, match="boom"):
        await _collect(p.stream([], [], model="mock-1"))


@pytest.mark.asyncio
async def test_health():
    p = MockProvider()
    h = await p.health()
    assert h["name"] == "mock"
    assert h["status"] == "ok"


@pytest.mark.asyncio
async def test_get_provider_factory():
    p = get_provider("mock")
    assert p.name == "mock"


def test_get_provider_unknown_raises():
    with pytest.raises(KeyError):
        get_provider("nonsense-xyz")


def test_get_provider_anthropic_stub_raises():
    with pytest.raises(NotImplementedError, match="Anthropic"):
        get_provider("anthropic")


def test_get_provider_openai_stub_raises():
    with pytest.raises(NotImplementedError, match="OpenAI"):
        get_provider("openai")


def test_available_lists_providers():
    from llm import available

    names = available()
    assert "amazon-q" in names
    assert "mock" in names
    assert "anthropic" in names
    assert "openai" in names
