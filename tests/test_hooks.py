import importlib
import json

import pytest


@pytest.fixture
def hooks_module(tmp_path, monkeypatch):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "id": "deny-etc",
                        "event": "pre_tool",
                        "match": {"tool": "fs_write", "args_regex": {"path": "^/etc/"}},
                        "action": {"type": "deny", "message": "no /etc"},
                    },
                    {
                        "id": "deny-rmrf",
                        "event": "pre_tool",
                        "match": {"tool": "execute_bash", "args_regex": {"command": "rm\\s+-rf\\s+/(\\s|$)"}},
                        "action": {"type": "deny", "message": "blocked"},
                    },
                    {
                        "id": "log-commit",
                        "event": "post_tool",
                        "match": {"tool": "git_commit", "status": "success"},
                        "action": {"type": "log", "message": "good"},
                    },
                    {
                        "id": "log-tool-list",
                        "event": "pre_tool",
                        "match": {"tool": ["foo", "bar"]},
                        "action": {"type": "log", "message": "hello"},
                    },
                    {
                        "id": "out-match",
                        "event": "post_tool",
                        "match": {"tool": "grep", "output_regex": "FOUND"},
                        "action": {"type": "log", "message": "x"},
                    },
                ]
            }
        )
    )
    monkeypatch.setenv("KIRA_HOOKS_CONFIG", str(cfg))
    monkeypatch.setenv("KIRA_HOOKS_ALLOW_SHELL", "0")
    import agent_hooks

    importlib.reload(agent_hooks)
    return agent_hooks


def test_pre_tool_deny_path(hooks_module):
    ev = hooks_module.run_pre_tool("s1", "fs_write", {"path": "/etc/passwd"})
    assert any(e["type"] == "deny" and e["hook_id"] == "deny-etc" for e in ev)


def test_pre_tool_pass_through(hooks_module):
    ev = hooks_module.run_pre_tool("s1", "fs_write", {"path": "/home/x.py"})
    assert all(e.get("type") != "deny" for e in ev)


def test_pre_tool_rmrf_deny(hooks_module):
    ev = hooks_module.run_pre_tool("s1", "execute_bash", {"command": "rm -rf /tmp/x"})
    assert all(e.get("type") != "deny" for e in ev)
    ev = hooks_module.run_pre_tool("s1", "execute_bash", {"command": "rm -rf /"})
    assert any(e["type"] == "deny" for e in ev)


def test_post_tool_log_commit(hooks_module):
    ev = hooks_module.run_post_tool("s1", "git_commit", {}, "success", "abc")
    assert any(e["type"] == "log" and e["hook_id"] == "log-commit" for e in ev)
    # error status should NOT match
    ev = hooks_module.run_post_tool("s1", "git_commit", {}, "error", "oops")
    assert all(e.get("hook_id") != "log-commit" for e in ev)


def test_tool_list_match(hooks_module):
    ev = hooks_module.run_pre_tool("s1", "foo", {})
    assert any(e["hook_id"] == "log-tool-list" for e in ev)
    ev = hooks_module.run_pre_tool("s1", "baz", {})
    assert all(e["hook_id"] != "log-tool-list" for e in ev)


def test_output_regex(hooks_module):
    ev = hooks_module.run_post_tool("s1", "grep", {}, "success", "FOUND it")
    assert any(e["hook_id"] == "out-match" for e in ev)
    ev = hooks_module.run_post_tool("s1", "grep", {}, "success", "nothing")
    assert all(e["hook_id"] != "out-match" for e in ev)


def test_shell_hook_blocked_without_allow(hooks_module, tmp_path, monkeypatch):
    # Add a shell hook on the fly
    cfg = tmp_path / "hooks2.json"
    cfg.write_text(
        json.dumps(
            {
                "hooks": [
                    {"event": "pre_tool", "match": {"tool": "x"}, "action": {"type": "shell", "cmd": "exit 1"}},
                ]
            }
        )
    )
    monkeypatch.setenv("KIRA_HOOKS_CONFIG", str(cfg))
    import agent_hooks

    importlib.reload(agent_hooks)
    ev = agent_hooks.run_pre_tool("s", "x", {})
    assert all(e.get("type") != "deny" for e in ev)
    assert any("shell hook skipped" in (e.get("message", "")) for e in ev)


def test_shell_hook_with_allow(tmp_path, monkeypatch):
    cfg = tmp_path / "hooks.json"
    out_file = tmp_path / "out.txt"
    cfg.write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "event": "post_tool",
                        "match": {"tool": "x"},
                        "action": {"type": "shell", "cmd": f"echo ok-$KIRA_HOOK_TOOL > {out_file}"},
                    },
                ]
            }
        )
    )
    monkeypatch.setenv("KIRA_HOOKS_CONFIG", str(cfg))
    monkeypatch.setenv("KIRA_HOOKS_ALLOW_SHELL", "1")
    import agent_hooks

    importlib.reload(agent_hooks)
    ev = agent_hooks.run_post_tool("s", "x", {}, "success", "out")
    assert any(e.get("type") == "shell" and e.get("rc") == 0 for e in ev)
    assert out_file.read_text().strip() == "ok-x"


def test_hooks_status_no_config(tmp_path, monkeypatch):
    monkeypatch.setenv("KIRA_HOOKS_CONFIG", str(tmp_path / "missing.json"))
    import agent_hooks

    importlib.reload(agent_hooks)
    s = agent_hooks.hooks_status()
    assert s["exists"] is False
    assert s["count"] == 0


def test_shell_pre_tool_nonzero_denies(tmp_path, monkeypatch):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "event": "pre_tool",
                        "match": {"tool": "x"},
                        "action": {"type": "shell", "cmd": "echo nope >&2; exit 7"},
                    },
                ]
            }
        )
    )
    monkeypatch.setenv("KIRA_HOOKS_CONFIG", str(cfg))
    monkeypatch.setenv("KIRA_HOOKS_ALLOW_SHELL", "1")
    import agent_hooks

    importlib.reload(agent_hooks)
    ev = agent_hooks.run_pre_tool("s", "x", {})
    assert any(e.get("type") == "deny" and "nope" in e.get("message", "") for e in ev)
