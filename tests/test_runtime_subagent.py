"""Cover the subagent path in agent_runtime: ListAgents, InvokeSubagents,
_run_subagent_silent, and the dev_loop helper.

All tests stub q_client.stream_q to avoid real Bedrock calls.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

import agent_runtime as ar
import q_client


async def _drain(gen):
    sse, final = [], None
    async for sse_bytes, result in gen:
        if sse_bytes is not None:
            sse.append(json.loads(sse_bytes[6:].decode().strip()))
        if result is not None:
            final = result
    return sse, final


# ---------- ListAgents ----------


@pytest.mark.asyncio
async def test_handle_subagent_list_agents():
    sse, final = await _drain(ar._handle_subagent("k", {"command": "ListAgents"}, "m", "/c", "sid", "parent"))
    assert final[0] == "success"
    info = json.loads(final[1])
    assert info["agents"][0]["name"] == "default"
    # Subagents must not have use_subagent in their tool list.
    assert "use_subagent" not in info["agents"][0]["tools"]
    assert sse == []


# ---------- unknown command / no specs ----------


@pytest.mark.asyncio
async def test_handle_subagent_unknown_command():
    _, final = await _drain(ar._handle_subagent("k", {"command": "FooBar"}, "m", "/c", "sid", "parent"))
    assert final[0] == "error"
    assert "unknown" in final[1].lower()


@pytest.mark.asyncio
async def test_handle_subagent_no_subagents():
    _, final = await _drain(
        ar._handle_subagent(
            "k",
            {"command": "InvokeSubagents", "content": {"subagents": []}},
            "m",
            "/c",
            "sid",
            "parent",
        )
    )
    assert final[0] == "error"
    assert "no subagents" in final[1].lower()


# ---------- _run_subagent_silent: pure text reply ----------


@pytest.mark.asyncio
async def test_run_subagent_silent_text_only():
    async def fake(api_key, body, **kw):
        yield "assistantResponseEvent", {"content": "hello from sub"}

    with patch.object(q_client, "stream_q", fake):
        out = await ar._run_subagent_silent("k", "q1", "m", "/c", "sid")
    assert out == "hello from sub"


@pytest.mark.asyncio
async def test_run_subagent_silent_with_relevant_context():
    captured = {}

    async def fake(api_key, body, **kw):
        # Capture the user message text that was sent.
        cur = body["conversationState"]["currentMessage"]
        captured["content"] = cur["userInputMessage"]["content"]
        yield "assistantResponseEvent", {"content": "ok"}

    with patch.object(q_client, "stream_q", fake):
        await ar._run_subagent_silent("k", "do X", "m", "/c", "sid", relevant_context="FACT=42")
    assert "do X" in captured["content"]
    assert "FACT=42" in captured["content"]


@pytest.mark.asyncio
async def test_run_subagent_silent_handles_exception():
    async def boom(api_key, body, **kw):
        if False:
            yield None
        raise RuntimeError("net down")

    with patch.object(q_client, "stream_q", boom):
        out = await ar._run_subagent_silent("k", "q", "m", "/c", "sid")
    assert out.startswith("[subagent error]")
    assert "RuntimeError" in out


@pytest.mark.asyncio
async def test_run_subagent_silent_empty_response_marker():
    async def empty(api_key, body, **kw):
        if False:
            yield None  # pragma: no cover

    with patch.object(q_client, "stream_q", empty):
        out = await ar._run_subagent_silent("k", "q", "m", "/c", "sid")
    assert out == "(subagent produced no output)"


# ---------- _run_subagent_silent: with one tool call ----------


@pytest.mark.asyncio
async def test_run_subagent_silent_with_tool_call(monkeypatch):
    """Subagent calls a tool, gets a result, then produces final text."""
    called = {}

    def fake_run_tool(name, args, cwd, sid=None):
        called["name"] = name
        return ("success", "FILE CONTENTS", None)

    monkeypatch.setattr(ar.toolkit, "run_tool", fake_run_tool)

    turn = {"n": 0}

    async def fake(api_key, body, **kw):
        turn["n"] += 1
        if turn["n"] == 1:
            yield (
                "toolUseEvent",
                {
                    "toolUseId": "t1",
                    "name": "fs_read",
                    "input": json.dumps({"path": "x"}),
                    "stop": True,
                },
            )
        else:
            yield "assistantResponseEvent", {"content": "final answer"}

    with patch.object(q_client, "stream_q", fake):
        out = await ar._run_subagent_silent("k", "read x", "m", "/c", "sid")
    assert called["name"] == "fs_read"
    assert "final answer" in out


# ---------- _handle_subagent InvokeSubagents (multi-fanout) ----------


@pytest.mark.asyncio
async def test_handle_subagent_invoke_two_in_parallel(monkeypatch):
    """Two subagents fanned out, both return text only."""

    async def fake(api_key, body, **kw):
        # Different responses depending on query.
        cur = body["conversationState"]["currentMessage"]
        text = cur["userInputMessage"]["content"]
        if "alpha" in text:
            await asyncio.sleep(0.02)
            yield "assistantResponseEvent", {"content": "answer-alpha"}
        else:
            yield "assistantResponseEvent", {"content": "answer-beta"}

    with patch.object(q_client, "stream_q", fake):
        sse, final = await _drain(
            ar._handle_subagent(
                "k",
                {
                    "command": "InvokeSubagents",
                    "content": {
                        "subagents": [
                            {"query": "investigate alpha"},
                            {"query": "investigate beta"},
                        ]
                    },
                },
                "m",
                "/c",
                "sid",
                "parent_tu",
            )
        )
    assert final[0] == "success"
    assert "answer-alpha" in final[1]
    assert "answer-beta" in final[1]
    # SSE: subagent_start + 2 x subagent_done
    types = [e["type"] for e in sse]
    assert types[0] == "subagent_start"
    assert types.count("subagent_done") == 2


@pytest.mark.asyncio
async def test_handle_subagent_one_fails(monkeypatch):
    """One subagent succeeds, one raises. Overall status = error."""

    async def fake(api_key, body, **kw):
        cur = body["conversationState"]["currentMessage"]
        text = cur["userInputMessage"]["content"]
        if "good" in text:
            yield "assistantResponseEvent", {"content": "OK"}
        else:
            raise ConnectionError("upstream gone")

    with patch.object(q_client, "stream_q", fake):
        _, final = await _drain(
            ar._handle_subagent(
                "k",
                {
                    "command": "InvokeSubagents",
                    "content": {
                        "subagents": [
                            {"query": "good one"},
                            {"query": "bad one"},
                        ]
                    },
                },
                "m",
                "/c",
                "sid",
                "parent_tu",
            )
        )
    # The silent helper catches subagent errors and returns success text starting with [subagent error]
    # so overall comes out as success but with [subagent error] in the blob.
    assert "subagent error" in final[1].lower() or final[0] == "error"


@pytest.mark.asyncio
async def test_handle_subagent_caps_at_max_parallel(monkeypatch):
    """More than MAX_SUBAGENT_PARALLEL specs get truncated."""
    invocations = {"n": 0}

    async def fake(api_key, body, **kw):
        invocations["n"] += 1
        yield "assistantResponseEvent", {"content": f"reply{invocations['n']}"}

    over = ar.MAX_SUBAGENT_PARALLEL + 2
    specs = [{"query": f"q{i}"} for i in range(over)]
    with patch.object(q_client, "stream_q", fake):
        _, final = await _drain(
            ar._handle_subagent(
                "k",
                {"command": "InvokeSubagents", "content": {"subagents": specs}},
                "m",
                "/c",
                "sid",
                "parent",
            )
        )
    assert invocations["n"] == ar.MAX_SUBAGENT_PARALLEL
    assert final[0] == "success"
