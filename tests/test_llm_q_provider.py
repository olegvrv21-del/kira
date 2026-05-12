"""Tests for llm.q_provider — conversion + stream() against a mocked q_client."""

from __future__ import annotations

import asyncio

import pytest

from llm.base import Message, ToolCall, ToolSpec
from llm.q_provider import QProvider, _ToolAccumulator, messages_to_q_body


async def _collect(it):
    out = []
    async for ev in it:
        out.append(ev)
    return out


# ---------------------------------------------------------------------------
# messages_to_q_body
# ---------------------------------------------------------------------------


class TestMessagesToBody:
    def test_minimal_user(self):
        body = messages_to_q_body(
            [Message(role="user", content="hi")],
            [],
            model="claude-haiku-4.5",
        )
        cs = body["conversationState"]
        assert cs["chatTriggerType"] == "MANUAL"
        assert cs["agentTaskType"] == "vibe"
        assert "conversationId" in cs
        assert cs["currentMessage"]["userInputMessage"]["content"] == "hi"
        assert cs["history"] == []

    def test_system_becomes_history_user(self):
        body = messages_to_q_body(
            [Message(role="system", content="sys"), Message(role="user", content="u")],
            [],
            model="m",
        )
        hist = body["conversationState"]["history"]
        assert len(hist) == 1
        assert hist[0]["userInputMessage"]["content"] == "sys"

    def test_assistant_with_tool_calls(self):
        msgs = [
            Message(role="user", content="q1"),
            Message(
                role="assistant",
                content="thinking",
                tool_calls=[ToolCall(id="t1", name="fs_read", arguments={"p": "x"})],
            ),
            Message(role="tool", content="file contents", tool_call_id="t1"),
            Message(role="user", content=""),  # follow-up carrying tool results
        ]
        body = messages_to_q_body(msgs, [], model="m")
        hist = body["conversationState"]["history"]
        # history: user q1, assistant w/ toolUses
        assert hist[0]["userInputMessage"]["content"] == "q1"
        arm = hist[1]["assistantResponseMessage"]
        assert arm["toolUses"][0]["toolUseId"] == "t1"
        assert arm["toolUses"][0]["name"] == "fs_read"
        # currentMessage carries the toolResults
        cur = body["conversationState"]["currentMessage"]["userInputMessage"]
        results = cur["userInputMessageContext"]["toolResults"]
        assert results[0]["toolUseId"] == "t1"
        assert results[0]["content"][0]["text"] == "file contents"

    def test_tool_specs_in_context(self):
        body = messages_to_q_body(
            [Message(role="user", content="hi")],
            [ToolSpec(name="grep", description="d", parameters={"type": "object"})],
            model="m",
        )
        tools = body["conversationState"]["currentMessage"]["userInputMessage"][
            "userInputMessageContext"
        ]["tools"]
        assert tools[0]["toolSpec"]["name"] == "grep"
        assert tools[0]["toolSpec"]["inputSchema"]["json"] == {"type": "object"}

    def test_multimodal_content_text_only(self):
        msg = Message(
            role="user",
            content=[
                {"type": "text", "text": "see this: "},
                {"type": "image", "data": "b64..."},
                {"type": "text", "text": "end"},
            ],
        )
        body = messages_to_q_body([msg], [], model="m")
        assert body["conversationState"]["currentMessage"]["userInputMessage"]["content"] == "see this: end"

    def test_uses_provided_conversation_id(self):
        body = messages_to_q_body(
            [Message(role="user", content="hi")],
            [],
            model="m",
            conversation_id="abc",
            continuation_id="xyz",
        )
        cs = body["conversationState"]
        assert cs["conversationId"] == "abc"
        assert cs["agentContinuationId"] == "xyz"


# ---------------------------------------------------------------------------
# _ToolAccumulator
# ---------------------------------------------------------------------------


class TestToolAccumulator:
    def test_assembles_streamed_input(self):
        acc = _ToolAccumulator()
        assert acc.feed({"toolUseId": "t1", "name": "fs_read"}) is None
        assert acc.feed({"toolUseId": "t1", "input": '{"pa'}) is None
        assert acc.feed({"toolUseId": "t1", "input": 'th": "/x"}'}) is None
        tc = acc.feed({"toolUseId": "t1", "stop": True})
        assert tc is not None
        assert tc.name == "fs_read"
        assert tc.arguments == {"path": "/x"}

    def test_empty_input(self):
        acc = _ToolAccumulator()
        acc.feed({"toolUseId": "t1", "name": "x"})
        tc = acc.feed({"toolUseId": "t1", "stop": True})
        assert tc.arguments == {}

    def test_bad_json_surfaced_as_parse_error(self):
        acc = _ToolAccumulator()
        acc.feed({"toolUseId": "t1", "name": "x", "input": "not json"})
        tc = acc.feed({"toolUseId": "t1", "stop": True})
        assert "_parse_error" in tc.arguments
        assert tc.arguments["_raw"] == "not json"

    def test_no_tool_use_id_ignored(self):
        acc = _ToolAccumulator()
        assert acc.feed({"name": "x"}) is None


# ---------------------------------------------------------------------------
# QProvider.stream() via a mocked q_client
# ---------------------------------------------------------------------------


class _FakeQClient:
    """Stand-in for the real q_client module. Yields a scripted event sequence."""

    def __init__(self, events):
        self._events = events
        self.last_body = None
        self.last_key = None

    def stream_q(self, api_key, body, *, timeout=300, cancel_event=None):
        self.last_body = body
        self.last_key = api_key
        events = self._events

        async def _gen():
            for et, payload in events:
                yield et, payload

        return _gen()


@pytest.mark.asyncio
async def test_stream_text_and_tool(monkeypatch):
    fake = _FakeQClient(
        [
            ("assistantResponseEvent", {"content": "hi "}),
            ("assistantResponseEvent", {"content": "there"}),
            ("toolUseEvent", {"toolUseId": "t1", "name": "fs_read"}),
            ("toolUseEvent", {"toolUseId": "t1", "input": '{"p": 1}'}),
            ("toolUseEvent", {"toolUseId": "t1", "stop": True}),
            ("messageMetadataEvent", {"usage": {"inputTokens": 10, "outputTokens": 3}}),
        ]
    )
    import llm.q_provider as qp

    monkeypatch.setattr(qp, "_get_q_client", lambda: fake)

    p = QProvider(api_key="ksk_fake")
    events = await _collect(
        p.stream([Message(role="user", content="hello")], [], model="claude-haiku-4.5")
    )
    types = [e.type for e in events]
    assert types == ["text", "text", "tool_call", "usage", "done"]
    tc = next(e for e in events if e.type == "tool_call").tool
    assert tc.name == "fs_read"
    assert tc.arguments == {"p": 1}
    usage = next(e for e in events if e.type == "usage").usage
    assert usage.input_tokens == 10 and usage.output_tokens == 3
    # Body was actually built and passed to q_client.
    assert fake.last_key == "ksk_fake"
    assert fake.last_body["conversationState"]["currentMessage"]["userInputMessage"]["content"] == "hello"


@pytest.mark.asyncio
async def test_stream_throttle_and_cancel(monkeypatch):
    fake = _FakeQClient(
        [
            ("_throttle", {"sleep": 0.0, "reason": "429", "attempt": 1}),
            ("_cancelled", {}),
        ]
    )
    import llm.q_provider as qp

    monkeypatch.setattr(qp, "_get_q_client", lambda: fake)

    p = QProvider(api_key="k")
    events = await _collect(p.stream([Message(role="user", content="x")], [], model="m"))
    types = [e.type for e in events]
    # cancelled returns early — no `done` after.
    assert types == ["throttle", "cancelled"]
    assert events[0].meta["reason"] == "429"


@pytest.mark.asyncio
async def test_stream_skips_non_dict_payload(monkeypatch):
    fake = _FakeQClient(
        [
            ("assistantResponseEvent", None),
            ("assistantResponseEvent", {"content": "ok"}),
        ]
    )
    import llm.q_provider as qp

    monkeypatch.setattr(qp, "_get_q_client", lambda: fake)

    p = QProvider(api_key="k")
    events = await _collect(p.stream([Message(role="user", content="x")], [], model="m"))
    types = [e.type for e in events]
    assert types == ["text", "done"]


@pytest.mark.asyncio
async def test_resolve_key_falls_back_to_pool(monkeypatch):
    fake = _FakeQClient([("assistantResponseEvent", {"content": "k"})])
    import llm.q_provider as qp

    monkeypatch.setattr(qp, "_get_q_client", lambda: fake)

    class _Pool:
        keys = ["ksk_from_pool"]

        def current(self):
            return "ksk_from_pool"

    import sys
    import types as _t

    fake_mod = _t.ModuleType("agent_keys")
    fake_mod.key_pool = _Pool()
    monkeypatch.setitem(sys.modules, "agent_keys", fake_mod)

    p = QProvider()  # no explicit key — should consult key_pool
    await _collect(p.stream([Message(role="user", content="x")], [], model="m"))
    assert fake.last_key == "ksk_from_pool"


@pytest.mark.asyncio
async def test_resolve_key_raises_when_no_pool(monkeypatch):
    import sys
    import types as _t

    class _Pool:
        keys = []

        def current(self):
            return None

    fake_mod = _t.ModuleType("agent_keys")
    fake_mod.key_pool = _Pool()
    monkeypatch.setitem(sys.modules, "agent_keys", fake_mod)

    p = QProvider()
    with pytest.raises(RuntimeError, match="no Q API key"):
        await _collect(p.stream([Message(role="user", content="x")], [], model="m"))


@pytest.mark.asyncio
async def test_health_ok(monkeypatch):
    import sys
    import types as _t

    class _Pool:
        keys = ["a", "b"]

        def current(self):
            return "a"

    fake_mod = _t.ModuleType("agent_keys")
    fake_mod.key_pool = _Pool()
    monkeypatch.setitem(sys.modules, "agent_keys", fake_mod)

    h = await QProvider().health()
    assert h["name"] == "amazon-q"
    assert h["status"] == "ok"
    assert h["pool_size"] == 2


@pytest.mark.asyncio
async def test_health_no_key(monkeypatch):
    import sys
    import types as _t

    class _Pool:
        keys = []

        def current(self):
            return None

    fake_mod = _t.ModuleType("agent_keys")
    fake_mod.key_pool = _Pool()
    monkeypatch.setitem(sys.modules, "agent_keys", fake_mod)

    h = await QProvider().health()
    assert h["status"] == "no_key"
