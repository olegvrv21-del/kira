"""End-to-end run_agent tests with a mocked q_client.stream_q.

We replace q_client.stream_q with an async generator that yields a scripted
sequence of (event_type, payload) tuples — same shape as the real upstream
parser. This lets us cover the full agent loop (turn switching, tool dispatch,
cancellation, history persistence) without hitting Bedrock or running docker.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

import agent_runtime as ar
import q_client


# ---------- helpers ----------


def _stream(*events):
    """Build a fresh async generator over a list of (event_type, payload).

    Returns a callable usable as a side_effect for q_client.stream_q.
    """
    scripts = list(events)

    async def fake_stream(api_key, body, **kwargs):
        for et, payload in scripts.pop(0):
            yield et, payload

    return fake_stream


async def _collect(gen) -> list[dict]:
    out = []
    async for ev in gen:
        if ev.startswith(b"data: "):
            try:
                out.append(json.loads(ev[6:].decode("utf-8").strip()))
            except json.JSONDecodeError:
                # malformed SSE chunk; tolerate and skip
                continue
    return out


# ---------- single-turn, text only ----------


@pytest.mark.asyncio
async def test_run_agent_simple_text_turn(monkeypatch, tmp_path):
    # Redirect workspaces to a tmp dir so we don't pollute real workspaces/.
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)
    fake = _stream(
        [
            ("assistantResponseEvent", {"content": "hello", "messageId": "m1"}),
            ("assistantResponseEvent", {"content": " world"}),
            ("meteringEvent", {"usage": 0.0012}),
            ("contextUsageEvent", {"contextUsagePercentage": 12.5}),
        ]
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "hi", session_id="unit_a"))
    types = [e.get("type") for e in events]
    assert "meta" in types
    text_deltas = [e["delta"] for e in events if e.get("type") == "text"]
    assert "".join(text_deltas) == "hello world"
    stats = [e for e in events if e.get("type") == "stats"]
    assert stats and stats[-1]["credits"] == pytest.approx(0.0012, rel=1e-6)
    assert stats[-1]["context_pct"] == pytest.approx(12.5, rel=1e-6)
    assert events[-1]["type"] == "done"


# ---------- tool use + tool result loop ----------


@pytest.mark.asyncio
async def test_run_agent_tool_use_dispatched(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)

    # Stub the toolkit dispatch so we don't touch docker / filesystem.
    called = {}

    def fake_run_tool(name, args, cwd, sid=None):
        called["name"] = name
        called["args"] = args
        called["sid"] = sid
        return ("success", "FAKE_OK", None)

    monkeypatch.setattr(ar.toolkit, "run_tool", fake_run_tool)

    fake = _stream(
        # turn 1: emit a tool_use
        [
            (
                "toolUseEvent",
                {
                    "toolUseId": "tu1",
                    "name": "fs_read",
                    "input": json.dumps({"path": "x.py"}),
                    "stop": True,
                },
            ),
        ],
        # turn 2: assistant produces final text after seeing tool result
        [
            ("assistantResponseEvent", {"content": "done", "messageId": "m2"}),
        ],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "read x.py", session_id="unit_b"))

    assert called["name"] == "fs_read"
    assert called["args"] == {"path": "x.py"}
    tool_events = [e for e in events if e.get("type") in ("tool_use", "tool_result")]
    # We expect at least one tool_use_started + one tool_result_event
    kinds = {e["type"] for e in tool_events}
    assert kinds & {"tool_use", "tool_result"}


# ---------- cancellation mid-stream ----------


@pytest.mark.asyncio
async def test_run_agent_cancellation(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)

    async def slow_stream(api_key, body, cancel_event=None, **kw):
        # Wait until cancellation flips.
        for _ in range(50):
            if cancel_event and cancel_event.is_set():
                yield "_cancelled", {}
                return
            await asyncio.sleep(0.01)
        yield "assistantResponseEvent", {"content": "never"}

    sid = "unit_cancel"

    async def trigger_cancel():
        await asyncio.sleep(0.05)
        assert ar.request_cancel(sid) is True

    with patch.object(q_client, "stream_q", slow_stream):
        cancel_task = asyncio.create_task(trigger_cancel())
        events = await _collect(ar.run_agent("k", "hi", session_id=sid))
        await cancel_task

    types = [e.get("type") for e in events]
    assert "cancelled" in types
    assert types[-1] == "done"
    assert cancel_task.done()  # cancellation trigger was awaited


# ---------- error propagation ----------


@pytest.mark.asyncio
async def test_run_agent_upstream_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)

    async def boom(api_key, body, **kw):
        yield "assistantResponseEvent", {"content": "partial"}
        raise RuntimeError("upstream blew up")

    with patch.object(q_client, "stream_q", boom):
        events = await _collect(ar.run_agent("k", "hi", session_id="unit_err"))

    errors = [e for e in events if e.get("type") == "error"]
    assert errors
    assert "RuntimeError" in errors[0]["message"]
    assert "upstream blew up" in errors[0]["message"]


# ---------- history orphan tool_uses ----------


@pytest.mark.asyncio
async def test_run_agent_injects_synthetic_tool_results_for_orphans(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)

    # History where the last assistant turn left tool_uses unfulfilled.
    history = [
        {
            "userInputMessage": {
                "content": "prev",
                "userInputMessageContext": {"envState": ar._env_state("/x")},
                "origin": "KIRO_CLI",
                "modelId": "m",
            }
        },
        {
            "assistantResponseMessage": {
                "content": "using tool",
                "toolUses": [{"toolUseId": "orphan1", "name": "fs_read", "input": {}}],
            }
        },
    ]

    fake = _stream([("assistantResponseEvent", {"content": "ok now"})])
    with patch.object(q_client, "stream_q", fake):
        # Use a *copy* so we can inspect the mutation
        h = list(history)
        async for _ in ar.run_agent("k", "continue", session_id="unit_orph", history=h):
            pass
    # The runtime should have appended a synthetic user msg carrying toolResults
    # for the orphan tool_use before sending the new prompt.
    found_synth = any(
        "orphan1" in json.dumps((m.get("userInputMessage") or {}).get("userInputMessageContext") or {}) for m in h
    )
    assert found_synth, "orphan tool_use was not stubbed with synthetic tool_results"


# ---------- _llm_one_shot ----------


@pytest.mark.asyncio
async def test_llm_one_shot_concatenates_text():
    async def fake(api_key, body, **kw):
        yield "assistantResponseEvent", {"content": "part1 "}
        yield "assistantResponseEvent", {"content": "part2"}
        yield "_throttle", {"reason": "rate"}  # ignored

    with patch.object(q_client, "stream_q", fake):
        out = await ar._llm_one_shot("k", "hello", "claude-m")
    assert out == "part1 part2"


@pytest.mark.asyncio
async def test_llm_one_shot_truncation():
    payload_text = "x" * 10000

    async def fake(api_key, body, **kw):
        yield "assistantResponseEvent", {"content": payload_text}

    with patch.object(q_client, "stream_q", fake):
        out = await ar._llm_one_shot("k", "prompt", "m", max_tokens=10)
    assert out.endswith("... [truncated]")
    assert len(out) <= 10 * 4 + len("\n... [truncated]")


@pytest.mark.asyncio
async def test_llm_one_shot_handles_exception():
    async def boom(api_key, body, **kw):
        yield "assistantResponseEvent", {"content": "hi"}
        raise ValueError("upstream fail")

    with patch.object(q_client, "stream_q", boom):
        out = await ar._llm_one_shot("k", "prompt", "m")
    assert "[llm_one_shot error]" in out
    assert "ValueError" in out


@pytest.mark.asyncio
async def test_llm_one_shot_via_mock_provider(monkeypatch):
    """Phase 3a guard: _llm_one_shot routes through the llm/ abstraction.

    By setting KIRA_LLM_PROVIDER=mock and registering a deterministic
    MockProvider, we should get the mock's output WITHOUT touching q_client.
    This proves the provider layer is actually wired into the runtime.
    """
    import llm
    from llm import MockProvider

    captured = {}

    def factory():
        # Capture messages so we can assert system+user shape.
        p = MockProvider(
            [
                {"type": "text", "text": "hello "},
                {"type": "text", "text": "from mock"},
            ]
        )
        captured["provider"] = p
        return p

    llm.register("mock", factory)
    monkeypatch.setenv("KIRA_LLM_PROVIDER", "mock")

    # Ensure q_client isn't even called — replace stream_q with a poison pill.
    async def boom(*a, **kw):
        raise AssertionError("q_client must not be called when provider=mock")
        yield None  # pragma: no cover  (make it a generator)

    with patch.object(q_client, "stream_q", boom):
        out = await ar._llm_one_shot("k", "hi there", "mock-1")

    assert out == "hello from mock"
    p = captured["provider"]
    assert len(p.calls) == 1
    roles = [m.role for m in p.calls[0]["messages"]]
    assert roles == ["system", "user"]
    assert p.calls[0]["messages"][1].content == "hi there"
    assert p.calls[0]["model"] == "mock-1"


@pytest.mark.asyncio
async def test_llm_one_shot_empty_response_marker():
    async def empty(api_key, body, **kw):
        # need to be a generator that yields nothing
        if False:
            yield None  # pragma: no cover

    with patch.object(q_client, "stream_q", empty):
        out = await ar._llm_one_shot("k", "prompt", "m")
    assert out == "(empty response)"


# ---------- run_agent: synthetic stop on MAX_TURNS ----------


@pytest.mark.asyncio
async def test_run_agent_respects_max_turns(monkeypatch, tmp_path):
    """Loop should hit MAX_TURNS cap if model keeps emitting tool_uses forever."""
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)
    monkeypatch.setattr(ar, "MAX_TURNS", 3)

    def fake_run_tool(name, args, cwd, sid=None):
        return ("success", "ok", None)

    monkeypatch.setattr(ar.toolkit, "run_tool", fake_run_tool)

    counter = {"n": 0}

    async def keep_calling_tools(api_key, body, **kw):
        counter["n"] += 1
        yield (
            "toolUseEvent",
            {
                "toolUseId": f"tu{counter['n']}",
                "name": "fs_read",
                "input": json.dumps({"path": "x"}),
                "stop": True,
            },
        )

    with patch.object(q_client, "stream_q", keep_calling_tools):
        events = await _collect(ar.run_agent("k", "hi", session_id="unit_maxt"))
    # We should hit MAX_TURNS=3 and bail out with an error event explaining why.
    assert counter["n"] == 3, f"expected exactly 3 turns, got {counter['n']}"
    assert events[-1]["type"] == "error"
    assert "max turns" in events[-1]["message"].lower()


# ---------- hook deny path ----------


@pytest.mark.asyncio
async def test_run_agent_hook_deny_blocks_tool(monkeypatch, tmp_path):
    """When pre_tool hook returns deny, tool must not be dispatched, and an
    explanatory error tool_result must be fed back to the model."""
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)

    dispatch_called = {"n": 0}

    def fake_run_tool(name, args, cwd, sid=None):
        dispatch_called["n"] += 1
        return ("success", "should_not_be_called", None)

    monkeypatch.setattr(ar.toolkit, "run_tool", fake_run_tool)

    # Stub agent_hooks.run_pre_tool to return a deny verdict.
    import agent_hooks

    def fake_pre(sid, tool, args):
        return [
            {
                "type": "deny",
                "message": "blocked by test policy",
                "hook_id": "unit_test_block",
                "event": "pre_tool",
                "tool": tool,
            }
        ]

    monkeypatch.setattr(agent_hooks, "run_pre_tool", fake_pre)

    fake = _stream(
        [
            (
                "toolUseEvent",
                {
                    "toolUseId": "tu_deny",
                    "name": "execute_bash",
                    "input": json.dumps({"command": "rm -rf /"}),
                    "stop": True,
                },
            ),
        ],
        [("assistantResponseEvent", {"content": "acknowledged"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "please", session_id="unit_hook"))

    assert dispatch_called["n"] == 0, "hook deny did not stop dispatch"
    hook_events = [e for e in events if e.get("type") == "hook"]
    assert hook_events, "no hook SSE event emitted"


@pytest.mark.asyncio
async def test_plan_nudge_when_plan_only_round_yields_no_followup(monkeypatch, tmp_path):
    """Regression for Bug 1 (plan-tool early end_turn).

    When the model emits exactly one `plan` tool_call and then a zero-tool
    follow-up turn, the loop must NOT return done. It should emit a
    plan_nudge SSE event and feed a follow-up user prompt so the model
    actually executes the next plan step.
    """
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)

    # Stub plan handler so it returns success without touching SQLite.
    import agent_runtime as _ar
    monkeypatch.setattr(_ar, "_handle_plan", lambda sid, args: ("success", "ok"))
    monkeypatch.setattr(_ar, "_load_plan", lambda sid: {"items": [{"text": "step1", "status": "in_progress"}]})

    fake = _stream(
        # turn 1: plan tool only
        [
            (
                "toolUseEvent",
                {
                    "toolUseId": "tu_plan",
                    "name": "plan",
                    "input": json.dumps({"op": "set", "items": ["step1"]}),
                    "stop": True,
                },
            ),
        ],
        # turn 2: nothing — model ends turn with no tools and short text
        [
            ("assistantResponseEvent", {"content": "ok", "messageId": "m2"}),
        ],
        # turn 3: AFTER the nudge, model finally executes a real tool then stops
        [
            ("assistantResponseEvent", {"content": "doing it", "messageId": "m3"}),
        ],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "do thing", session_id="unit_plannudge"))

    nudges = [e for e in events if e.get("type") == "plan_nudge"]
    assert nudges, f"expected plan_nudge SSE event, got: {[e.get('type') for e in events]}"
    # Loop must reach a done event (not an error) only after the third turn.
    dones = [e for e in events if e.get("type") == "done"]
    assert dones, "no done event after nudge cycle"
