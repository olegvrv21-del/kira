"""Tool-handler registry: structural and contract tests.

agent_tool_handlers extracts the per-tool branches from run_agent's loop
into a registry of self-contained async generators. These tests pin down
the contract (one ToolResult terminator) and exercise each built-in
handler end-to-end without going near a real provider.
"""

import json

import pytest

import agent_tool_handlers as th
from agent_tool_handlers import ToolContext, ToolResult


def _sse(obj):
    return ("data: " + json.dumps(obj) + "\n\n").encode("utf-8")


def _ctx(name: str, args: dict, **over) -> ToolContext:
    """Build a stub ToolContext with no-op helpers; override by kwarg as needed."""
    defaults = dict(
        api_key="k", model="m", cwd="/c", session_id="sid", tool_use_id="t1",
        name=name, args=args, use_sandbox=False,
        toolkit=type("FakeTk", (), {"run_tool": staticmethod(lambda *a, **kw: ("success", "OK", None))})(),
        handle_subagent=None, handle_dev_loop=None, llm_one_shot=None,
        handle_plan=lambda sid, args: ("success", "plan-ok"),
        load_plan=lambda sid: {"items": []},
        sse=_sse,
        maybe_diff=lambda *a: (None, 0),
    )
    defaults.update(over)
    return ToolContext(**defaults)


async def _drain(gen):
    """Collect (sse_frames, terminator) from a handler generator."""
    sse = []
    term = None
    async for item in gen:
        if isinstance(item, (bytes, bytearray)):
            sse.append(json.loads(bytes(item)[6:].decode().strip()))
        elif isinstance(item, ToolResult):
            assert term is None, "handler yielded a second terminator"
            term = item
    return sse, term


# ---------- registry surface ----------


def test_registry_has_builtins():
    assert set(th.names()) >= {"llm_one_shot", "output_iframe", "plan", "use_subagent", "dev_loop"}


def test_register_overrides_existing():
    async def _h(ctx):  # noqa: ARG001
        yield ToolResult(status="success", output="override")

    original = th.get("plan")
    th.register("plan", _h)
    try:
        assert th.get("plan") is _h
    finally:
        th.register("plan", original)


def test_get_unknown_falls_back_to_default():
    # Default handler is a real async function, not None.
    h = th.get("definitely-not-a-tool-xyz")
    assert callable(h)
    assert h is not th.get("plan")  # registered ones override the default


# ---------- llm_one_shot ----------


@pytest.mark.asyncio
async def test_llm_one_shot_success():
    async def fake_oneshot(*a, **kw):
        return "hi"

    ctx = _ctx("llm_one_shot", {"prompt": "x", "model": "claude-haiku-4.5"}, llm_one_shot=fake_oneshot)
    sse, term = await _drain(th.get("llm_one_shot")(ctx))
    assert sse == []  # no intermediate SSE for this handler
    assert term.status == "success"
    assert term.output == "hi"


@pytest.mark.asyncio
async def test_llm_one_shot_error_marker_routes_to_error():
    async def fake_oneshot(*a, **kw):
        return "[llm_one_shot error] X"

    ctx = _ctx("llm_one_shot", {"prompt": "x"}, llm_one_shot=fake_oneshot)
    _, term = await _drain(th.get("llm_one_shot")(ctx))
    assert term.status == "error"


@pytest.mark.asyncio
async def test_llm_one_shot_strips_q_prefix():
    captured = {}

    async def fake_oneshot(api_key, prompt, model, **kw):
        captured["model"] = model
        return "ok"

    ctx = _ctx("llm_one_shot", {"prompt": "x", "model": "q/claude-opus-4.7"}, llm_one_shot=fake_oneshot)
    await _drain(th.get("llm_one_shot")(ctx))
    assert captured["model"] == "claude-opus-4.7"


# ---------- output_iframe ----------


@pytest.mark.asyncio
async def test_output_iframe_emits_iframe_sse():
    ctx = _ctx("output_iframe", {"html": "<b>hi</b>", "title": "demo"})
    sse, term = await _drain(th.get("output_iframe")(ctx))
    assert any(e["type"] == "iframe" and e["title"] == "demo" and "<b>hi</b>" in e["html"] for e in sse)
    assert term.status == "success"


@pytest.mark.asyncio
async def test_output_iframe_empty_html_errors():
    ctx = _ctx("output_iframe", {"html": "", "title": "x"})
    sse, term = await _drain(th.get("output_iframe")(ctx))
    # Iframe SSE still emitted? — current behaviour is: error skips it.
    # Actually our impl: emits iframe always. Adjust assertion:
    assert any(e["type"] == "iframe" for e in sse)
    assert term.status == "error"
    assert "html is required" in term.output


# ---------- plan ----------


@pytest.mark.asyncio
async def test_plan_delegates_and_emits_plan_sse():
    captured = {}

    def plan_handler(sid, args):
        captured["args"] = args
        return ("success", "PLAN OK")

    ctx = _ctx("plan", {"op": "set", "items": ["a", "b"]}, handle_plan=plan_handler)
    sse, term = await _drain(th.get("plan")(ctx))
    assert captured["args"] == {"op": "set", "items": ["a", "b"]}
    assert any(e["type"] == "plan" for e in sse)
    assert term.status == "success"
    assert term.output == "PLAN OK"


# ---------- use_subagent (delegated to passed-in async generator) ----------


@pytest.mark.asyncio
async def test_use_subagent_streams_and_terminates():
    async def fake_subagent(api_key, args, model, cwd, sid, parent_tid):
        yield _sse({"type": "subagent_start", "parent_id": parent_tid, "count": 1, "queries": ["x"]}), None
        yield _sse({"type": "subagent_done", "parent_id": parent_tid, "index": 0, "status": "success", "preview": "p"}), None
        yield None, ("success", "combined")

    ctx = _ctx(
        "use_subagent",
        {"command": "InvokeSubagents", "content": {"subagents": [{"query": "x"}]}},
        handle_subagent=fake_subagent,
    )
    sse, term = await _drain(th.get("use_subagent")(ctx))
    kinds = [e["type"] for e in sse]
    assert "subagent_start" in kinds and "subagent_done" in kinds
    assert term.status == "success"
    assert term.output == "combined"


# ---------- dev_loop ----------


@pytest.mark.asyncio
async def test_dev_loop_streams_and_terminates():
    async def fake_dev_loop(api_key, args, model, cwd, sid, parent_tid, toolkit):
        yield _sse({"type": "dev_loop_iter", "parent_id": parent_tid, "n": 1, "max": 1, "action": "edit", "summary": "s"}), None
        yield None, ("success", "DEV_LOOP=PASS iters=1")

    ctx = _ctx("dev_loop", {"task": "fix"}, handle_dev_loop=fake_dev_loop)
    sse, term = await _drain(th.get("dev_loop")(ctx))
    assert any(e["type"] == "dev_loop_iter" for e in sse)
    assert term.status == "success"


# ---------- default handler: hooks deny, toolkit invocation, diff propagation ----------


@pytest.mark.asyncio
async def test_default_handler_runs_toolkit_and_emits_hook_events(monkeypatch):
    import agent_hooks

    monkeypatch.setattr(agent_hooks, "run_pre_tool", lambda sid, name, args: [
        {"hook_id": "pre-log", "event": "pre_tool", "type": "log", "message": "ok", "tool": name}
    ])
    monkeypatch.setattr(agent_hooks, "run_post_tool", lambda *a, **kw: [
        {"hook_id": "post-log", "event": "post_tool", "type": "log", "message": "done", "tool": a[1]}
    ])

    class TK:
        @staticmethod
        def run_tool(name, args, cwd, sid=None):
            return ("success", "DATA", None)

    ctx = _ctx("fs_read", {"path": "/x"}, toolkit=TK())
    sse, term = await _drain(th.get("fs_read")(ctx))  # falls back to default
    hook_types = [e["hook_id"] for e in sse if e["type"] == "hook"]
    assert "pre-log" in hook_types and "post-log" in hook_types
    assert term.status == "success"
    assert term.output == "DATA"


@pytest.mark.asyncio
async def test_default_handler_denies_when_hook_says_deny(monkeypatch):
    import agent_hooks

    monkeypatch.setattr(agent_hooks, "run_pre_tool", lambda *a, **kw: [
        {"hook_id": "guard", "event": "pre_tool", "type": "deny", "message": "nope", "tool": a[1]}
    ])
    # toolkit must NOT be called when deny fires
    called = {"n": 0}

    class TK:
        @staticmethod
        def run_tool(name, args, cwd, sid=None):
            called["n"] += 1
            return ("success", "...", None)

    ctx = _ctx("fs_write", {"path": "/etc/passwd", "content": "x"}, toolkit=TK())
    sse, term = await _drain(th.get("fs_write")(ctx))
    assert term.status == "error"
    assert "HOOK_DENY" in term.output and "nope" in term.output
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_default_handler_captures_diff_when_provided(monkeypatch):
    import agent_hooks

    monkeypatch.setattr(agent_hooks, "run_pre_tool", lambda *a, **kw: [])
    monkeypatch.setattr(agent_hooks, "run_post_tool", lambda *a, **kw: [])

    class TK:
        @staticmethod
        def run_tool(name, args, cwd, sid=None):
            return ("success", "wrote [BACKUP=/tmp/bak]", None)

    fake_diff = ("--- a\n+++ b\n+line\n", 1)
    ctx = _ctx("fs_write", {"path": "/x"}, toolkit=TK(), maybe_diff=lambda *a: fake_diff)
    _, term = await _drain(th.get("fs_write")(ctx))
    assert term.backup == "/tmp/bak"
    assert term.diff == "--- a\n+++ b\n+line\n"
    assert term.diff_lines == 1
    assert term.extra_sse.get("diff") == "--- a\n+++ b\n+line\n"
