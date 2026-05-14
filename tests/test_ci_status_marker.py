"""Tests for prod_observe ci_status marker-line format (PR #32)."""
import json
from unittest.mock import patch

import agent_prod
import agent_tools


def _mk_run(stdout: str) -> dict:
    return {"ok": True, "returncode": 0, "stdout": stdout, "stderr": "", "duration_seconds": 0.05}


def _gh(checks, state="OPEN", pr=29):
    return json.dumps({
        "number": pr, "title": "T", "state": state, "statusCheckRollup": checks
    })


def test_ci_status_marker_success_first_line():
    checks = [
        {"name": "pytest", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": ""},
        {"name": "lint", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": ""},
    ]
    with patch.object(agent_prod, "_run", return_value=_mk_run(_gh(checks, "OPEN", 29))):
        out = agent_tools.prod_observe({"what": "ci_status", "pr": 29}, cwd="/tmp")
    first = out.splitlines()[0]
    assert first.startswith("OK rollup=green")
    assert "pr=29" in first
    assert "state=OPEN" in first
    assert "pass=2" in first
    assert "fail=0" in first
    assert "pending=0" in first


def test_ci_status_marker_red_first_line():
    checks = [
        {"name": "pytest", "conclusion": "FAILURE", "status": "COMPLETED", "detailsUrl": ""},
        {"name": "lint", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": ""},
    ]
    with patch.object(agent_prod, "_run", return_value=_mk_run(_gh(checks, "OPEN", 42))):
        out = agent_tools.prod_observe({"what": "ci_status", "pr": 42}, cwd="/tmp")
    first = out.splitlines()[0]
    assert first.startswith("OK rollup=red")
    assert "fail=1" in first


def test_ci_status_marker_pending_first_line():
    checks = [
        {"name": "pytest", "conclusion": None, "status": "IN_PROGRESS", "detailsUrl": ""},
    ]
    with patch.object(agent_prod, "_run", return_value=_mk_run(_gh(checks, "OPEN", 7))):
        out = agent_tools.prod_observe({"what": "ci_status", "pr": 7}, cwd="/tmp")
    first = out.splitlines()[0]
    assert first.startswith("OK rollup=pending")
    assert "pending=1" in first


def test_ci_status_marker_error_first_line():
    # invalid pr triggers ci_status's own validation, returning ok:false
    out = agent_tools.prod_observe({"what": "ci_status", "pr": 0}, cwd="/tmp")
    first = out.splitlines()[0]
    assert first.startswith("ERROR:")
    assert "range" in first.lower() or "pr" in first.lower()


def test_ci_status_marker_includes_raw():
    checks = [{"name": "pytest", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": ""}]
    with patch.object(agent_prod, "_run", return_value=_mk_run(_gh(checks, "MERGED", 1))):
        out = agent_tools.prod_observe({"what": "ci_status", "pr": 1}, cwd="/tmp")
    assert "raw response:" in out
    assert '"rollup": "green"' in out


def test_other_prod_observe_actions_no_marker():
    """uptime/df/git_log return raw JSON without OK/ERROR prefix."""
    with patch.object(agent_prod, "_run", return_value=_mk_run("load avg 0.1")):
        out = agent_tools.prod_observe({"what": "uptime"}, cwd="/tmp")
    assert not out.startswith("OK ")
    assert not out.startswith("ERROR:")


def test_ci_status_marker_state_merged():
    checks = [{"name": "pytest", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": ""}]
    with patch.object(agent_prod, "_run", return_value=_mk_run(_gh(checks, "MERGED", 99))):
        out = agent_tools.prod_observe({"what": "ci_status", "pr": 99}, cwd="/tmp")
    first = out.splitlines()[0]
    assert "state=MERGED" in first
