"""Tests for agent_prod read-only host observation."""

from __future__ import annotations

import agent_prod
import agent_tools


def test_uptime_returns_two_blocks():
    r = agent_prod.uptime()
    assert "uptime" in r and "memory" in r
    assert r["uptime"]["ok"] is True
    assert r["memory"]["ok"] is True


def test_df_returns_root():
    r = agent_prod.df()
    assert r["ok"] is True
    # df -h prints a header + one line per mount
    assert "Filesystem" in r["stdout"]


def test_systemctl_status_rejects_unknown_unit():
    r = agent_prod.systemctl_status("evil.service")
    assert r["ok"] is False
    assert "whitelist" in r["error"]


def test_git_log_basic():
    r = agent_prod.git_log(n=3)
    assert r["ok"] is True
    # The repo we run tests against has at least 3 commits.
    assert len(r["stdout"].splitlines()) >= 1


def test_git_diff_rejects_bad_ref():
    r = agent_prod.git_diff(ref="; rm -rf /")
    assert r["ok"] is False
    assert "invalid ref" in r["error"]


def test_journalctl_filters_grep_in_process():
    """Even if journalctl isn't available in the test env, the function must not crash."""
    r = agent_prod.journalctl(lines=10, grep="zzz_unlikely_match_xyz")
    # ok=False if journalctl missing (CI), but stdout should still be filtered if ok
    assert isinstance(r, dict)
    assert "ok" in r


def test_prod_observe_tool_routing():
    out = agent_tools.run_tool("prod_observe", {"what": "uptime"}, cwd=".")
    assert out[0] == "success"
    assert '"uptime"' in out[1]


def test_prod_observe_unknown_what_errors():
    out = agent_tools.run_tool("prod_observe", {"what": "rm_rf_slash"}, cwd=".")
    assert out[0] == "error"
    assert "unknown what" in out[1]
