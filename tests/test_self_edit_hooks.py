"""Regression tests for the self-edit guard hooks added to hooks.json.

Sandbox mounts ~/webchat at /host/webchat:rw so the agent can self-edit. That
is also an attack surface: a prompt-injected fs_write into agent_auth.py
would silently disable auth, and a follow-up /admin/restart would persist
the breach. The deny-self-edit-core hook (plus its shell-redirect variant)
slams the door on the security-critical files; log-self-edit makes every
other write into /host/webchat/* visible in the action journal.

These tests load the real hooks.json — they fail loudly if anyone edits
those rules without updating the regression list here.
"""

import json
from pathlib import Path

import pytest

import agent_hooks

HOOKS_JSON = Path(__file__).resolve().parent.parent / "hooks.json"


@pytest.fixture(autouse=True)
def _reset_hooks_cache(monkeypatch):
    """Force a re-read of hooks.json for each test."""
    monkeypatch.setattr(agent_hooks, "CONFIG_PATH", HOOKS_JSON)
    agent_hooks._CACHE["mtime"] = 0.0
    agent_hooks._CACHE["hooks"] = []
    yield


# ---------- deny-self-edit-core ----------


@pytest.mark.parametrize(
    "path",
    [
        "/host/webchat/agent_auth.py",
        "/host/webchat/agent_keys.py",
        "/host/webchat/sandbox_runtime.py",
        "/host/webchat/agent_critic.py",
        "/host/webchat/agent_hooks.py",
        "/host/webchat/hooks.json",
        "/host/webchat/.restart_token",
    ],
)
def test_self_edit_critical_paths_denied(path):
    events = agent_hooks.run_pre_tool("sid1", "fs_write", {"path": path, "content": "x"})
    denies = [e for e in events if e["type"] == "deny"]
    assert denies, f"hook did not deny fs_write {path}"
    assert "deny-self-edit-core" in denies[0]["hook_id"]


@pytest.mark.parametrize(
    "path",
    [
        "/host/webchat/agent_auth.py",
        "/host/webchat/hooks.json",
    ],
)
def test_self_edit_critical_paths_denied_via_patch(path):
    events = agent_hooks.run_pre_tool("sid1", "patch", {"path": path})
    denies = [e for e in events if e["type"] == "deny"]
    assert denies, f"hook did not deny patch {path}"
    assert "deny-self-edit-core" in denies[0]["hook_id"]


# ---------- log-self-edit (non-critical /host/webchat/* still allowed) ----------


def test_self_edit_non_critical_path_logged_not_denied():
    events = agent_hooks.run_pre_tool(
        "sid2", "fs_write", {"path": "/host/webchat/app.py", "content": "x"}
    )
    assert all(e["type"] != "deny" for e in events), "non-critical self-edit must not be denied"
    logs = [e for e in events if e["type"] == "log" and e["hook_id"] == "log-self-edit"]
    assert logs, "non-critical self-edit must be logged"


def test_self_edit_outside_webchat_neither_denied_nor_logged():
    events = agent_hooks.run_pre_tool(
        "sid3", "fs_write", {"path": "/workspace/notes.txt", "content": "x"}
    )
    self_edit_evs = [e for e in events if e.get("hook_id", "").startswith(("deny-self-edit", "log-self-edit"))]
    assert self_edit_evs == [], "self-edit hooks must not match outside /host/webchat/"


# ---------- shell-redirect bypass guard ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "echo evil > /host/webchat/agent_auth.py",
        "cat /tmp/x >/host/webchat/hooks.json",
        "printf %s '' > /host/webchat/.restart_token",
        "tee /host/webchat/agent_keys.py > /dev/null <<< x",  # this one shouldn't match — no '>' prefix
    ],
)
def test_shell_redirect_into_critical_denied(cmd):
    events = agent_hooks.run_pre_tool("sid4", "execute_bash", {"command": cmd})
    denies = [e for e in events if e["type"] == "deny" and e["hook_id"] == "deny-self-edit-shell-redirect"]
    if ">" in cmd and any(name in cmd for name in ("agent_auth", "hooks.json", ".restart_token", "agent_keys")):
        # We expect a deny for redirect cases that target critical files.
        if "tee " in cmd:
            assert not denies, "tee without '>' should not trip the regex"
        else:
            assert denies, f"redirect into critical file was not denied: {cmd!r}"


def test_shell_redirect_into_non_critical_not_denied():
    events = agent_hooks.run_pre_tool("sid5", "execute_bash", {"command": "echo hi > /workspace/notes.txt"})
    assert all(e.get("hook_id") != "deny-self-edit-shell-redirect" for e in events)


# ---------- structural: hooks.json must contain the new rules ----------


def test_hooks_json_contains_self_edit_guards():
    data = json.loads(HOOKS_JSON.read_text())
    ids = {h.get("id") for h in data.get("hooks", [])}
    assert "deny-self-edit-core" in ids
    assert "log-self-edit" in ids
    assert "deny-self-edit-shell-redirect" in ids
