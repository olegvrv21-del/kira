"""Tests for agent_pr branch+PR pipeline. Network calls (gh, git push)
are NOT exercised — we test the validation and dispatch layer only."""

from __future__ import annotations

import agent_pr
from agent_pr import _validate


def test_validate_rejects_non_kira_branch():
    err = _validate("main", {"a.txt": "hi"})
    assert err and "branch must match" in err


def test_validate_accepts_kira_branch():
    assert _validate("kira/fix-x", {"a.txt": "hi"}) is None


def test_validate_rejects_path_traversal():
    err = _validate("kira/fix-x", {"../../etc/passwd": "x"})
    assert err and "invalid path" in err


def test_validate_rejects_workflow_files():
    err = _validate("kira/fix-x", {".github/workflows/ci.yml": "x"})
    assert err and "workflow files not allowed" in err


def test_validate_rejects_absolute_path():
    err = _validate("kira/fix-x", {"/etc/passwd": "x"})
    assert err and "invalid path" in err


def test_validate_rejects_too_many_files():
    files = {f"f{i}.txt": "x" for i in range(25)}
    err = _validate("kira/fix-x", files)
    assert err and "too many files" in err


def test_validate_rejects_oversize():
    files = {"big.txt": "x" * (300 * 1024)}
    err = _validate("kira/fix-x", files)
    assert err and "exceeds" in err


def test_open_pr_bad_branch():
    r = agent_pr.open_pr(
        branch="not-kira",
        title="x",
        body="",
        files={"a.txt": "b"},
    )
    assert r["ok"] is False
