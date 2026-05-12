"""Cover agent_hooks branches: shell-allowed pre/post + on_session_start."""

import importlib
import json
import os

import pytest

import agent_hooks


def _reload_with(monkeypatch, cfg_path, allow_shell=False):
    monkeypatch.setenv("KIRA_HOOKS_CONFIG", str(cfg_path))
    monkeypatch.setenv("KIRA_HOOKS_ALLOW_SHELL", "1" if allow_shell else "0")
    importlib.reload(agent_hooks)
    return agent_hooks


def test_load_bad_json_recorded(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text("not json {{")
    h = _reload_with(monkeypatch, cfg)
    assert h.list_hooks() == []
    st = h.hooks_status()
    assert st["exists"] and st["count"] == 0
    assert "parse error" in (st.get("error") or "")


def test_load_missing_file(monkeypatch, tmp_path):
    h = _reload_with(monkeypatch, tmp_path / "nope.json")
    assert h.list_hooks() == []
    st = h.hooks_status()
    assert st["exists"] is False and st["count"] == 0


def test_load_cache_hits_second_call(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [{"id": "h1", "event": "pre_tool", "match": {"tool": "x"}, "action": {"type": "log", "message": "hi"}}]}))
    h = _reload_with(monkeypatch, cfg)
    a = h._load()
    b = h._load()
    assert a is b  # cache hit returns same list object


def test_match_tool_list_and_status_and_regex(monkeypatch, tmp_path):
    h = _reload_with(monkeypatch, tmp_path / "x.json")
    hook = {
        "event": "pre_tool",
        "match": {"tool": ["fs_read", "fs_write"], "args_regex": {"path": r"^/etc/"}},
    }
    assert h._match(hook, "pre_tool", "fs_write", {"path": "/etc/x"})
    assert not h._match(hook, "pre_tool", "fs_write", {"path": "/tmp/x"})
    assert not h._match(hook, "pre_tool", "other", {"path": "/etc/x"})
    # status mismatch
    hook2 = {"event": "post_tool", "match": {"status": "success"}}
    assert h._match(hook2, "post_tool", "x", {}, status="success")
    assert not h._match(hook2, "post_tool", "x", {}, status="error")
    # output_regex
    hook3 = {"event": "post_tool", "match": {"output_regex": r"BOOM"}}
    assert h._match(hook3, "post_tool", "x", {}, status="error", output="oh no BOOM here")
    assert not h._match(hook3, "post_tool", "x", {}, status="error", output="clean")
    # invalid regex returns False
    bad = {"event": "pre_tool", "match": {"args_regex": {"path": "[unbalanced"}}}
    assert not h._match(bad, "pre_tool", "x", {"path": "y"})
    bad2 = {"event": "post_tool", "match": {"output_regex": "[bad"}}
    assert not h._match(bad2, "post_tool", "x", {}, status="x", output="any")
    # args_regex with non-dict args
    bad3 = {"event": "pre_tool", "match": {"args_regex": {"path": "x"}}}
    assert not h._match(bad3, "pre_tool", "x", "not-a-dict")


def test_pre_tool_deny(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"id": "d1", "event": "pre_tool", "match": {"tool": "fs_write"}, "action": {"type": "deny", "message": "nope"}}
    ]}))
    h = _reload_with(monkeypatch, cfg)
    evs = h.run_pre_tool("sid", "fs_write", {"path": "/etc/x"})
    assert evs and evs[0]["type"] == "deny" and evs[0]["message"] == "nope"


def test_pre_tool_log(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"id": "l1", "event": "pre_tool", "match": {"tool": "fs_write"}, "action": {"type": "log", "message": "writing"}}
    ]}))
    h = _reload_with(monkeypatch, cfg)
    evs = h.run_pre_tool("sid", "fs_write", {})
    assert evs and evs[0]["type"] == "log" and evs[0]["message"] == "writing"


def test_pre_tool_shell_skipped_when_disabled(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"id": "s1", "event": "pre_tool", "match": {"tool": "fs_write"}, "action": {"type": "shell", "cmd": "true"}}
    ]}))
    h = _reload_with(monkeypatch, cfg, allow_shell=False)
    evs = h.run_pre_tool("sid", "fs_write", {})
    assert evs and "skipped" in evs[0]["message"]


def test_pre_tool_shell_pass(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"id": "s2", "event": "pre_tool", "match": {"tool": "fs_write"}, "action": {"type": "shell", "cmd": "echo hello"}}
    ]}))
    h = _reload_with(monkeypatch, cfg, allow_shell=True)
    evs = h.run_pre_tool("sid", "fs_write", {"path": "/x"})
    assert evs and evs[0]["type"] == "shell" and evs[0]["message"].startswith("hello")


def test_pre_tool_shell_deny_on_nonzero(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"id": "s3", "event": "pre_tool", "match": {"tool": "fs_write"}, "action": {"type": "shell", "cmd": "echo bad >&2; exit 1"}}
    ]}))
    h = _reload_with(monkeypatch, cfg, allow_shell=True)
    evs = h.run_pre_tool("sid", "fs_write", {})
    assert evs and evs[-1]["type"] == "deny" and "bad" in evs[-1]["message"]


def test_post_tool_log(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"id": "p1", "event": "post_tool", "match": {"status": "success"}, "action": {"type": "log", "message": "ok"}}
    ]}))
    h = _reload_with(monkeypatch, cfg)
    evs = h.run_post_tool("sid", "x", {}, status="success", output="ok out")
    assert evs and evs[0]["type"] == "log" and evs[0]["event"] == "post_tool"


def test_post_tool_shell_disabled_logs(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"id": "p2", "event": "post_tool", "match": {}, "action": {"type": "shell", "cmd": "true"}}
    ]}))
    h = _reload_with(monkeypatch, cfg, allow_shell=False)
    evs = h.run_post_tool("sid", "x", {}, status="success", output="")
    assert evs and "skipped" in evs[0]["message"]


def test_post_tool_shell_enabled(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"id": "p3", "event": "post_tool", "match": {}, "action": {"type": "shell", "cmd": "echo done"}}
    ]}))
    h = _reload_with(monkeypatch, cfg, allow_shell=True)
    evs = h.run_post_tool("sid", "x", {}, status="success", output="")
    assert evs


def test_session_start_log(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"id": "ss1", "event": "on_session_start", "action": {"type": "log", "message": "hello"}}
    ]}))
    h = _reload_with(monkeypatch, cfg)
    evs = h.run_session_start("sid42")
    assert evs and evs[0]["type"] == "log" and evs[0]["message"] == "hello"


def test_session_start_shell(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"id": "ss2", "event": "on_session_start", "action": {"type": "shell", "cmd": "echo started"}}
    ]}))
    h = _reload_with(monkeypatch, cfg, allow_shell=True)
    evs = h.run_session_start("sid42")
    assert evs and evs[0]["type"] == "shell" and evs[0]["rc"] == 0


def test_session_start_other_event_ignored(monkeypatch, tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"id": "ss3", "event": "pre_tool", "action": {"type": "log", "message": "x"}}
    ]}))
    h = _reload_with(monkeypatch, cfg)
    assert h.run_session_start("sid") == []


def test_run_shell_timeout(monkeypatch, tmp_path):
    h = _reload_with(monkeypatch, tmp_path / "x.json")
    rc, so, se = h._run_shell("sleep 5", {}, timeout=1)
    assert rc == 124 and "timeout" in se


def test_run_shell_exception(monkeypatch, tmp_path):
    h = _reload_with(monkeypatch, tmp_path / "x.json")

    import subprocess as sp

    def boom(*a, **kw):
        raise OSError("no bash")

    monkeypatch.setattr(sp, "run", boom)
    rc, so, se = h._run_shell("true", {})
    assert rc == 1 and "no bash" in se


def test_trim_long_string(monkeypatch, tmp_path):
    h = _reload_with(monkeypatch, tmp_path / "x.json")
    s = "a" * 10000
    out = h._trim(s, n=100)
    assert "[trimmed]" in out and len(out) < len(s)
    assert h._trim(None) == ""
    assert h._trim("short", n=100) == "short"
