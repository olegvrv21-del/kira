"""Provider-agnostic runtime tests via the MockProvider fixture.

These mirror the Q-shape tests in test_runtime_subagent.py but exercise
the runtime through the canonical llm/ abstraction — no q_client imports,
no Bedrock event names. The `assert_no_q_client` fixture proves the code
path never falls back to the legacy adapter, so this file collectively
serves as the Phase 3a/3b/3d regression guard.

Q-shape tests are kept (they verify QProvider's wire-format parsing).
These tests verify everything ABOVE that layer is provider-agnostic.
"""

import json

import pytest

import agent_runtime as ar


# ---------- _llm_one_shot ----------


@pytest.mark.asyncio
async def test_llm_one_shot_text(mock_llm, assert_no_q_client):
    mock_llm([{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}])
    out = await ar._llm_one_shot("k", "hi", "mock-1")
    assert out == "hello world"


@pytest.mark.asyncio
async def test_llm_one_shot_truncation(mock_llm, assert_no_q_client):
    mock_llm([{"type": "text", "text": "x" * 10_000}])
    out = await ar._llm_one_shot("k", "p", "mock-1", max_tokens=10)
    assert out.endswith("... [truncated]")


@pytest.mark.asyncio
async def test_llm_one_shot_provider_raises(mock_llm, assert_no_q_client):
    mock_llm([{"type": "raise", "message": "boom"}])
    out = await ar._llm_one_shot("k", "p", "mock-1")
    assert "[llm_one_shot error]" in out
    assert "boom" in out


@pytest.mark.asyncio
async def test_llm_one_shot_empty(mock_llm, assert_no_q_client):
    mock_llm([])
    out = await ar._llm_one_shot("k", "p", "mock-1")
    assert out == "(empty response)"


@pytest.mark.asyncio
async def test_llm_one_shot_system_prompt_propagated(mock_llm, assert_no_q_client):
    p = mock_llm([{"type": "text", "text": "ok"}])
    await ar._llm_one_shot("k", "user-q", "mock-1", system="SYS-XYZ")
    msgs = p.calls[0]["messages"]
    assert msgs[0].role == "system"
    assert msgs[0].content == "SYS-XYZ"
    assert msgs[1].role == "user"
    assert msgs[1].content == "user-q"


# ---------- _run_subagent_silent ----------


@pytest.mark.asyncio
async def test_subagent_silent_text_only(mock_llm, assert_no_q_client):
    mock_llm([{"type": "text", "text": "subagent reply"}])
    out = await ar._run_subagent_silent("k", "q1", "mock-1", "/c", "sid")
    assert out == "subagent reply"


@pytest.mark.asyncio
async def test_subagent_silent_relevant_context_appended(mock_llm, assert_no_q_client):
    p = mock_llm([{"type": "text", "text": "ok"}])
    await ar._run_subagent_silent("k", "do X", "mock-1", "/c", "sid", relevant_context="FACT=42")
    user_msg = next(m for m in p.calls[0]["messages"] if m.role == "user")
    assert "do X" in user_msg.content
    assert "FACT=42" in user_msg.content


@pytest.mark.asyncio
async def test_subagent_silent_with_tool_call(mock_llm, assert_no_q_client, monkeypatch):
    """Turn 1 emits a tool_call; runtime dispatches the tool, feeds the
    result back; turn 2 produces the final text. Provider sees a canonical
    [system, user, assistant(tool_calls), tool, user(empty)] history."""
    called = {}

    def fake_run_tool(name, args, cwd, sid=None):
        called["name"] = name
        called["args"] = args
        return ("success", "FILE_CONTENTS", None)

    monkeypatch.setattr(ar.toolkit, "run_tool", fake_run_tool)

    p = mock_llm(
        [
            [
                {"type": "text", "text": "checking…"},
                {"type": "tool_call", "id": "t1", "name": "fs_read", "args": {"path": "/x"}},
            ],
            [{"type": "text", "text": "saw: FILE_CONTENTS"}],
        ]
    )
    out = await ar._run_subagent_silent("k", "read /x", "mock-1", "/c", "sid")
    assert called["name"] == "fs_read"
    assert called["args"] == {"path": "/x"}
    assert "saw: FILE_CONTENTS" in out
    # Two provider calls: pre-tool and post-tool.
    assert len(p.calls) == 2
    roles_t2 = [m.role for m in p.calls[1]["messages"]]
    assert roles_t2 == ["system", "user", "assistant", "tool", "user"]
    tool_msg = p.calls[1]["messages"][3]
    assert tool_msg.tool_call_id == "t1"
    assert tool_msg.content == "FILE_CONTENTS"


@pytest.mark.asyncio
async def test_subagent_silent_provider_exception_wrapped(mock_llm, assert_no_q_client):
    mock_llm([{"type": "raise", "message": "transport down"}])
    out = await ar._run_subagent_silent("k", "q", "mock-1", "/c", "sid")
    assert out.startswith("[subagent error]")
    assert "transport down" in out


@pytest.mark.asyncio
async def test_subagent_silent_empty_stream(mock_llm, assert_no_q_client):
    mock_llm([])
    out = await ar._run_subagent_silent("k", "q", "mock-1", "/c", "sid")
    assert out == "(subagent produced no output)"


# ---------- _handle_subagent (fan-out) ----------


@pytest.mark.asyncio
async def test_handle_subagent_two_in_parallel(mock_llm, assert_no_q_client):
    def script(msgs):
        # Pick which response based on which query is in the user turn.
        user = next((m for m in msgs if m.role == "user"), None)
        if user and "alpha" in (user.content or ""):
            return [{"type": "text", "text": "answer-alpha"}]
        return [{"type": "text", "text": "answer-beta"}]

    mock_llm(script)

    sse, final = [], None
    async for sse_bytes, result in ar._handle_subagent(
        "k",
        {
            "command": "InvokeSubagents",
            "content": {"subagents": [{"query": "probe alpha"}, {"query": "probe beta"}]},
        },
        "mock-1",
        "/c",
        "sid",
        "parent",
    ):
        if sse_bytes is not None:
            sse.append(json.loads(sse_bytes[6:].decode().strip()))
        if result is not None:
            final = result

    assert final[0] == "success"
    assert "answer-alpha" in final[1]
    assert "answer-beta" in final[1]
    kinds = [e["type"] for e in sse]
    assert kinds[0] == "subagent_start"
    assert kinds.count("subagent_done") == 2


@pytest.mark.asyncio
async def test_handle_subagent_caps_at_max_parallel(mock_llm, assert_no_q_client):
    """More than MAX_SUBAGENT_PARALLEL specs get truncated."""
    counts = {"n": 0}

    def script(_msgs):
        counts["n"] += 1
        return [{"type": "text", "text": f"reply-{counts['n']}"}]

    mock_llm(script)
    over = ar.MAX_SUBAGENT_PARALLEL + 2
    specs = [{"query": f"q{i}"} for i in range(over)]

    final = None
    async for _sse_bytes, result in ar._handle_subagent(
        "k",
        {"command": "InvokeSubagents", "content": {"subagents": specs}},
        "mock-1",
        "/c",
        "sid",
        "parent",
    ):
        if result is not None:
            final = result

    assert counts["n"] == ar.MAX_SUBAGENT_PARALLEL
    assert final[0] == "success"


# ---------- conftest fixture sanity ----------


@pytest.mark.asyncio
async def test_mock_llm_records_calls(mock_llm):
    p = mock_llm([{"type": "text", "text": "ok"}])
    await ar._llm_one_shot("k", "hi", "mock-1")
    assert p.calls, "MockProvider must record the call"
    assert p.calls[0]["model"] == "mock-1"


def test_assert_no_q_client_works_when_unused(assert_no_q_client):
    # If no test code calls q_client.stream_q, the fixture is a no-op.
    pass
