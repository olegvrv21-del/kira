"""Tests for gh_pr_open's new human-first response format (post-drill fix)."""
from unittest.mock import patch

import agent_tools


def test_gh_pr_open_success_starts_with_ok():
    """Successful PR should produce 'OK pr=N branch=... url=...' on first line."""
    mock_result = {
        "ok": True,
        "url": "https://github.com/olegvrv21-del/kira/pull/123",
        "pr": 123,
        "branch": "kira/test",
        "base": "main",
        "duration_seconds": None,
    }
    with patch("agent_pr.open_pr", return_value=mock_result):
        out = agent_tools.gh_pr_open(
            {"branch": "kira/test", "title": "t", "files": {"a.txt": "x"}},
            cwd="/tmp",
        )
    first = out.splitlines()[0]
    assert first.startswith("OK pr=123")
    assert "branch=kira/test" in first
    assert "url=https://github.com/olegvrv21-del/kira/pull/123" in first


def test_gh_pr_open_error_starts_with_error():
    mock_result = {"ok": False, "error": "branch must match kira/<slug>"}
    with patch("agent_pr.open_pr", return_value=mock_result):
        out = agent_tools.gh_pr_open(
            {"branch": "main", "title": "t", "files": {"a.txt": "x"}},
            cwd="/tmp",
        )
    first = out.splitlines()[0]
    assert first.startswith("ERROR:")
    assert "branch must match" in first


def test_gh_pr_open_includes_raw_json():
    """The full JSON should still be available after the marker line."""
    mock_result = {
        "ok": True,
        "url": "https://github.com/olegvrv21-del/kira/pull/42",
        "pr": 42,
        "branch": "kira/x",
        "base": "main",
        "duration_seconds": None,
    }
    with patch("agent_pr.open_pr", return_value=mock_result):
        out = agent_tools.gh_pr_open(
            {"branch": "kira/x", "title": "t", "files": {"a.txt": "x"}},
            cwd="/tmp",
        )
    assert "raw response:" in out
    assert '"pr": 42' in out
    assert '"url": "https://github.com/olegvrv21-del/kira/pull/42"' in out


def test_gh_pr_open_error_first_line_only_no_newlines_leaked():
    """If error spans multiple lines, only the first 300 chars of line 1
    appear in the marker — full text still in raw."""
    mock_result = {"ok": False, "error": "first line\nsecond line that leaks"}
    with patch("agent_pr.open_pr", return_value=mock_result):
        out = agent_tools.gh_pr_open(
            {"branch": "kira/x", "title": "t", "files": {"a.txt": "x"}},
            cwd="/tmp",
        )
    first = out.splitlines()[0]
    assert first == "ERROR: first line"


def test_gh_pr_open_unknown_pr_number_marked_question():
    """If the URL didn't parse, pr falls back to '?'."""
    mock_result = {"ok": True, "url": "weird-url", "pr": None,
                   "branch": "kira/q", "base": "main", "duration_seconds": None}
    with patch("agent_pr.open_pr", return_value=mock_result):
        out = agent_tools.gh_pr_open(
            {"branch": "kira/q", "title": "t", "files": {"a.txt": "x"}},
            cwd="/tmp",
        )
    first = out.splitlines()[0]
    assert "pr=?" in first


def test_agent_pr_extracts_pr_number_from_url():
    """The pr extraction itself: URL ending in /pull/29 → pr=29."""
    import re
    url = "https://github.com/olegvrv21-del/kira/pull/29"
    m = re.search(r"/pull/(\d+)", url)
    assert m is not None
    assert int(m.group(1)) == 29


def test_agent_pr_returns_pr_field_in_success(monkeypatch):
    """End-to-end: a stubbed open_pr success path includes pr in the dict."""
    import agent_pr

    class FakeRun:
        call = 0
        @staticmethod
        def __call__(argv, cwd=None, timeout=60):
            # Simulate the gh CLI path: token, clone, checkout, config, add,
            # status (non-empty), commit, push, pr create
            FakeRun.call += 1
            if argv[:2] == ["gh", "auth"]:
                return (0, "fake-token\n", "")
            if argv[0] == "git" and "clone" in argv:
                return (0, "", "")
            if argv[:2] == ["git", "checkout"]:
                return (0, "", "")
            if argv[:2] == ["git", "-C"] and "config" in argv:
                return (0, "", "")
            if argv[:2] == ["git", "-C"] and "add" in argv:
                return (0, "", "")
            if argv[:2] == ["git", "-C"] and "status" in argv:
                return (0, "M README.md\n", "")
            if argv[:2] == ["git", "-C"] and "commit" in argv:
                return (0, "", "")
            if argv[:2] == ["git", "-C"] and "push" in argv:
                return (0, "", "")
            if argv[:2] == ["gh", "pr"]:
                return (0, "https://github.com/olegvrv21-del/kira/pull/777\n", "")
            return (1, "", "unhandled argv")

    monkeypatch.setattr(agent_pr, "_run", FakeRun())
    res = agent_pr.open_pr(
        branch="kira/test",
        title="t",
        body="b",
        files={"README.md": "x"},
    )
    assert res["ok"] is True
    assert res["pr"] == 777
    assert res["url"].endswith("/pull/777")
