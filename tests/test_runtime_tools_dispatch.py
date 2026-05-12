"""Cover the per-tool dispatch branches inside run_agent + _handle_dev_loop.

We stub q_client.stream_q so the agent emits scripted toolUseEvents, and we
stub toolkit.run_tool / _llm_one_shot / _handle_subagent / _handle_dev_loop
inner pieces. Goal: exercise the llm_one_shot, output_iframe, plan,
use_subagent, dev_loop, hook-deny, and git_commit auto-critic branches.
"""

import asyncio
import json
import os
from unittest.mock import patch

import pytest

import agent_runtime as ar
import q_client


def _stream(*turns):
    scripts = list(turns)

    async def fake_stream(api_key, body, **kw):
        for et, payload in scripts.pop(0):
            yield et, payload

    return fake_stream


async def _collect(gen):
    out = []
    async for ev in gen:
        if ev.startswith(b"data: "):
            try:
                out.append(json.loads(ev[6:].decode("utf-8").strip()))
            except Exception:
                pass
    return out


def _tool_use(tid: str, name: str, args: dict):
    return ("toolUseEvent", {"toolUseId": tid, "name": name, "input": json.dumps(args), "stop": True})


# ---------- llm_one_shot tool branch ----------


@pytest.mark.asyncio
async def test_dispatch_llm_one_shot_success(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)

    async def fake_one_shot(api_key, prompt, model, system=None, max_tokens=None):
        return "FAKE-ANSWER"

    monkeypatch.setattr(ar, "_llm_one_shot", fake_one_shot)
    fake = _stream(
        [_tool_use("tu1", "llm_one_shot", {"prompt": "hi", "model": "claude-haiku-4.5"})],
        [("assistantResponseEvent", {"content": "done", "messageId": "m"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "go", session_id="los_ok"))
    tr = [e for e in events if e.get("type") == "tool_result"]
    assert tr and tr[0]["status"] == "success" and tr[0]["output"] == "FAKE-ANSWER"


@pytest.mark.asyncio
async def test_dispatch_llm_one_shot_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)

    async def fake_one_shot(*a, **kw):
        return "[llm_one_shot error] RuntimeError: nope"

    monkeypatch.setattr(ar, "_llm_one_shot", fake_one_shot)
    fake = _stream(
        [_tool_use("tu1", "llm_one_shot", {"prompt": "hi", "model": "q/claude-haiku-4.5"})],
        [("assistantResponseEvent", {"content": "x"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "go", session_id="los_err"))
    tr = [e for e in events if e.get("type") == "tool_result"]
    assert tr and tr[0]["status"] == "error"


# ---------- output_iframe tool branch ----------


@pytest.mark.asyncio
async def test_dispatch_output_iframe_success(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)
    fake = _stream(
        [_tool_use("tu1", "output_iframe", {"html": "<h1>x</h1>", "title": "demo"})],
        [("assistantResponseEvent", {"content": "k"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "render", session_id="oif_ok"))
    types = [e.get("type") for e in events]
    assert "iframe" in types
    tr = [e for e in events if e.get("type") == "tool_result"]
    assert tr and tr[0]["status"] == "success"


@pytest.mark.asyncio
async def test_dispatch_output_iframe_missing_html(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)
    fake = _stream(
        [_tool_use("tu1", "output_iframe", {"title": "x"})],
        [("assistantResponseEvent", {"content": "k"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "render", session_id="oif_err"))
    tr = [e for e in events if e.get("type") == "tool_result"]
    assert tr and tr[0]["status"] == "error"


# ---------- plan tool branch ----------


@pytest.mark.asyncio
async def test_dispatch_plan_set(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)
    fake = _stream(
        [_tool_use("tu1", "plan", {"op": "set", "items": ["step1", "step2"]})],
        [("assistantResponseEvent", {"content": "k"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "plan it", session_id="plan_ok"))
    plan_evs = [e for e in events if e.get("type") == "plan"]
    assert plan_evs and len(plan_evs[0]["plan"]["items"]) == 2


@pytest.mark.asyncio
async def test_dispatch_plan_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)
    fake = _stream(
        [_tool_use("tu1", "plan", {"op": "bogus"})],
        [("assistantResponseEvent", {"content": "k"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "x", session_id="plan_err"))
    tr = [e for e in events if e.get("type") == "tool_result"]
    assert tr and tr[0]["status"] == "error"


# ---------- use_subagent dispatch branch ----------


@pytest.mark.asyncio
async def test_dispatch_use_subagent(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)

    async def fake_sub(api_key, args, model, cwd, session_id, parent_tool_id):
        # emit one progress event then a final result
        yield ar._sse({"type": "subagent_done", "parent_id": parent_tool_id, "index": 0, "status": "success", "preview": "hey"}), None
        yield None, ("success", "sub-output")

    monkeypatch.setattr(ar, "_handle_subagent", fake_sub)
    fake = _stream(
        [_tool_use("tu1", "use_subagent", {"command": "ListAgents"})],
        [("assistantResponseEvent", {"content": "k"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "go", session_id="sub_ok"))
    types = [e.get("type") for e in events]
    assert "subagent_done" in types
    tr = [e for e in events if e.get("type") == "tool_result"]
    assert tr and tr[0]["status"] == "success"


# ---------- dev_loop dispatch branch ----------


@pytest.mark.asyncio
async def test_dispatch_dev_loop(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)

    async def fake_dl(api_key, args, model, cwd, session_id, parent_tool_id, toolkit):
        yield ar._sse({"type": "dev_loop_iter", "parent_id": parent_tool_id, "n": 1}), None
        yield None, ("success", "DEV_LOOP=PASS")

    monkeypatch.setattr(ar, "_handle_dev_loop", fake_dl)
    fake = _stream(
        [_tool_use("tu1", "dev_loop", {"task": "do thing"})],
        [("assistantResponseEvent", {"content": "k"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "loop", session_id="dl_ok"))
    types = [e.get("type") for e in events]
    assert "dev_loop_iter" in types
    tr = [e for e in events if e.get("type") == "tool_result"]
    assert tr and tr[0]["status"] == "success"


# ---------- cost-limit ejection ----------


@pytest.mark.asyncio
async def test_cost_limit_kills_after_first_turn(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)
    monkeypatch.setattr(ar, "_cost_limit_exceeded", lambda sid, c: "OVER")
    fake = _stream(
        [_tool_use("tu1", "fs_read", {"path": "x"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "go", session_id="cl_a"))
    err = [e for e in events if e.get("type") == "error"]
    assert err and err[0]["message"] == "OVER"


# ---------- git_commit auto-critic BLOCK path ----------


@pytest.mark.asyncio
async def test_git_commit_auto_critic_block(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)
    monkeypatch.setattr(ar, "USE_SANDBOX", False)
    monkeypatch.setenv("KIRA_CRITIC_AUTO", "1")

    # Stub subprocess.run used by the non-sandbox critic branch.
    class FakeRun:
        def __init__(self, stdout="", returncode=0):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def fake_run(*a, **kw):
        return FakeRun(stdout="diff --git a/x b/x\n+bad code\n")

    monkeypatch.setattr("subprocess.run", fake_run)

    import agent_critic

    async def fake_review(api_key, diff_text, intent=""):
        return {"verdict": "BLOCK", "reason": "do not commit", "issues": ["bad"]}

    monkeypatch.setattr(agent_critic, "review_diff", fake_review)

    fake = _stream(
        [_tool_use("tu1", "git_commit", {"message": "feat: x", "path": "/tmp"})],
        [("assistantResponseEvent", {"content": "k"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "commit", session_id="gc_b"))
    types = [e.get("type") for e in events]
    assert "critic" in types
    crit = [e for e in events if e.get("type") == "critic"][0]
    assert crit["verdict"] == "BLOCK"
    tr = [e for e in events if e.get("type") == "tool_result"]
    assert tr and "HOOK_DENY" in tr[0]["output"]


@pytest.mark.asyncio
async def test_git_commit_auto_critic_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)
    monkeypatch.setattr(ar, "USE_SANDBOX", False)
    monkeypatch.setenv("KIRA_CRITIC_AUTO", "1")

    class FakeRun:
        stdout = "diff --git a/x b/x\n+ok\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeRun())

    import agent_critic

    async def fake_review(api_key, diff_text, intent=""):
        return {"verdict": "OK", "reason": "", "issues": []}

    monkeypatch.setattr(agent_critic, "review_diff", fake_review)

    # Stub the actual git_commit toolkit call so we don't run git.
    def fake_run_tool(name, args, cwd, sid=None):
        return ("success", "committed abc1234", None)

    monkeypatch.setattr(ar.toolkit, "run_tool", fake_run_tool)

    fake = _stream(
        [_tool_use("tu1", "git_commit", {"message": "feat: ok"})],
        [("assistantResponseEvent", {"content": "k"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "commit", session_id="gc_o"))
    crits = [e for e in events if e.get("type") == "critic"]
    assert crits and crits[0]["verdict"] == "OK"
    tr = [e for e in events if e.get("type") == "tool_result"]
    assert tr and tr[0]["status"] == "success"


@pytest.mark.asyncio
async def test_git_commit_auto_critic_exception_falls_back(monkeypatch, tmp_path):
    """If critic itself blows up, we should yield critic-error and still call the tool."""
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)
    monkeypatch.setattr(ar, "USE_SANDBOX", False)
    monkeypatch.setenv("KIRA_CRITIC_AUTO", "1")

    def boom(*a, **kw):
        raise RuntimeError("git missing")

    monkeypatch.setattr("subprocess.run", boom)

    import agent_critic

    async def fake_review(*a, **kw):
        raise RuntimeError("critic boom")

    monkeypatch.setattr(agent_critic, "review_diff", fake_review)

    def fake_run_tool(name, args, cwd, sid=None):
        return ("success", "ok", None)

    monkeypatch.setattr(ar.toolkit, "run_tool", fake_run_tool)

    fake = _stream(
        [_tool_use("tu1", "git_commit", {"message": "x"})],
        [("assistantResponseEvent", {"content": "k"})],
    )
    with patch.object(q_client, "stream_q", fake):
        events = await _collect(ar.run_agent("k", "go", session_id="gc_x"))
    crit = [e for e in events if e.get("type") == "critic"]
    assert crit and "critic-error" in crit[0].get("reason", "")
    tr = [e for e in events if e.get("type") == "tool_result"]
    assert tr and tr[0]["status"] == "success"


# ---------- _handle_dev_loop direct ----------


class _FakeToolkit:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def run_tool(self, name, args, cwd, sid=None):
        self.calls.append((name, dict(args)))
        return self.outputs.pop(0)


@pytest.mark.asyncio
async def test_handle_dev_loop_passes_first_iter(monkeypatch):
    async def fake_sub(api_key, q, model, cwd, sid, relevant_context=""):
        return "edited file"

    monkeypatch.setattr(ar, "_run_subagent_silent", fake_sub)
    monkeypatch.setattr(ar, "USE_SANDBOX", False)
    tk = _FakeToolkit([("success", "TESTS=PASS\n5 passed", None)])
    out_events = []
    final = None
    async for ev, res in ar._handle_dev_loop("k", {"task": "x"}, "m", "/tmp", "sid", "pid", tk):
        if ev is not None:
            out_events.append(json.loads(ev[6:].decode("utf-8").strip()))
        if res is not None:
            final = res
    assert final and final[0] == "success" and "PASS" in final[1]
    kinds = [e["type"] for e in out_events]
    assert "dev_loop_iter" in kinds and "dev_loop_test" in kinds and "dev_loop_done" in kinds


@pytest.mark.asyncio
async def test_handle_dev_loop_no_task():
    final = None
    async for ev, res in ar._handle_dev_loop("k", {}, "m", "/tmp", "sid", "pid", None):
        if res is not None:
            final = res
    assert final == ("error", "task is required")


@pytest.mark.asyncio
async def test_handle_dev_loop_subagent_exception(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("sub died")

    monkeypatch.setattr(ar, "_run_subagent_silent", boom)
    final = None
    async for ev, res in ar._handle_dev_loop("k", {"task": "x"}, "m", "/tmp", "sid", "pid", _FakeToolkit([])):
        if res is not None:
            final = res
    assert final and final[0] == "error" and "subagent" in final[1]


@pytest.mark.asyncio
async def test_handle_dev_loop_exhausts_iters(monkeypatch):
    async def fake_sub(*a, **kw):
        return "tried"

    monkeypatch.setattr(ar, "_run_subagent_silent", fake_sub)
    monkeypatch.setattr(ar, "USE_SANDBOX", False)
    tk = _FakeToolkit([("error", "TESTS=FAIL", None)] * 3)
    final = None
    async for ev, res in ar._handle_dev_loop("k", {"task": "x", "max_iters": 3}, "m", "/tmp", "sid", "pid", tk):
        if res is not None:
            final = res
    assert final and final[0] == "error" and "DEV_LOOP=FAIL" in final[1]


@pytest.mark.asyncio
async def test_handle_dev_loop_sandbox_path(monkeypatch):
    async def fake_sub(*a, **kw):
        return "edit done"

    monkeypatch.setattr(ar, "_run_subagent_silent", fake_sub)
    monkeypatch.setattr(ar, "USE_SANDBOX", True)

    class SBoxToolkit:
        def run_tool(self, name, args, cwd, sid):
            return ("success", "TESTS=PASS", None)

    final = None
    async for ev, res in ar._handle_dev_loop(
        "k", {"task": "x", "runner": "pytest", "target": "tests/", "path": "y.py", "relevant_context": "ctx"},
        "m", "/workspace", "sid", "pid", SBoxToolkit(),
    ):
        if res is not None:
            final = res
    assert final and final[0] == "success"


# ---------- _maybe_diff branches ----------


def test_maybe_diff_non_fs_write_returns_none():
    assert ar._maybe_diff("fs_read", {"path": "x"}, "/tmp/bak") == (None, 0)


def test_maybe_diff_no_backup_returns_none():
    assert ar._maybe_diff("fs_write", {"path": "x"}, None) == (None, 0)


def test_maybe_diff_no_path_in_args():
    assert ar._maybe_diff("fs_write", {}, "/tmp/bak") == (None, 0)


def test_maybe_diff_produces_diff(tmp_path):
    cur = tmp_path / "f.txt"
    bak = tmp_path / "f.bak"
    bak.write_text("alpha\nbeta\n")
    cur.write_text("alpha\nGAMMA\n")
    text, changed = ar._maybe_diff("fs_write", {"path": str(cur)}, str(bak))
    assert text and "alpha" in text and changed >= 2


def test_maybe_diff_same_files_returns_none(tmp_path):
    cur = tmp_path / "f.txt"
    bak = tmp_path / "f.bak"
    cur.write_text("x\n")
    bak.write_text("x\n")
    assert ar._maybe_diff("fs_write", {"path": str(cur)}, str(bak)) == (None, 0)


def test_maybe_diff_truncates_large(tmp_path, monkeypatch):
    cur = tmp_path / "f.txt"
    bak = tmp_path / "f.bak"
    bak.write_text("\n".join(f"old{i}" for i in range(20000)))
    cur.write_text("\n".join(f"new{i}" for i in range(20000)))
    text, changed = ar._maybe_diff("fs_write", {"path": str(cur)}, str(bak))
    assert text and "[diff truncated]" in text
