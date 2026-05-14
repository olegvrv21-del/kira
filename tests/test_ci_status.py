"""Tests for agent_prod.ci_status + prod_observe(what='ci_status') wiring."""
import json
from unittest.mock import patch

import pytest

import agent_prod


def _mk_run_result(stdout: str, ok: bool = True, rc: int = 0) -> dict:
    return {
        "ok": ok, "returncode": rc, "stdout": stdout,
        "stderr": "", "duration_seconds": 0.05,
    }


def _gh_json(checks: list[dict], state: str = "OPEN", title: str = "T", number: int = 26) -> str:
    return json.dumps({
        "number": number, "title": title, "state": state,
        "statusCheckRollup": checks,
    })


# ----- argument validation -----

def test_ci_status_rejects_non_int():
    r = agent_prod.ci_status(pr="not-a-number")
    assert r["ok"] is False
    assert "int" in r["error"]


def test_ci_status_rejects_negative():
    r = agent_prod.ci_status(pr=-1)
    assert r["ok"] is False
    assert "range" in r["error"]


def test_ci_status_rejects_huge():
    r = agent_prod.ci_status(pr=10**9)
    assert r["ok"] is False


def test_ci_status_rejects_zero():
    r = agent_prod.ci_status(pr=0)
    assert r["ok"] is False


# ----- successful parse -----

def test_ci_status_all_green():
    checks = [
        {"name": "pytest", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": "u1"},
        {"name": "lint", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": "u2"},
        {"name": "CodeQL", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": "u3"},
    ]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json(checks))):
        r = agent_prod.ci_status(pr=26)
    assert r["ok"] is True
    assert r["rollup"] == "green"
    assert r["n_pass"] == 3
    assert r["n_fail"] == 0
    assert r["n_pending"] == 0
    assert len(r["checks"]) == 3


def test_ci_status_one_red():
    checks = [
        {"name": "pytest", "conclusion": "FAILURE", "status": "COMPLETED", "detailsUrl": "u1"},
        {"name": "lint", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": "u2"},
    ]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json(checks))):
        r = agent_prod.ci_status(pr=26)
    assert r["rollup"] == "red"
    assert r["n_fail"] == 1
    assert r["n_pass"] == 1


def test_ci_status_pending():
    checks = [
        {"name": "pytest", "conclusion": None, "status": "IN_PROGRESS", "detailsUrl": "u1"},
        {"name": "lint", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": "u2"},
    ]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json(checks))):
        r = agent_prod.ci_status(pr=26)
    assert r["rollup"] == "pending"
    assert r["n_pending"] == 1
    assert r["n_pass"] == 1


def test_ci_status_pending_beats_pass():
    """rollup='pending' should win over green-so-far when anything pending."""
    checks = [
        {"name": "a", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": ""},
        {"name": "b", "conclusion": None, "status": "QUEUED", "detailsUrl": ""},
    ]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json(checks))):
        r = agent_prod.ci_status(pr=42)
    assert r["rollup"] == "pending"


def test_ci_status_red_beats_pending():
    checks = [
        {"name": "a", "conclusion": "FAILURE", "status": "COMPLETED", "detailsUrl": ""},
        {"name": "b", "conclusion": None, "status": "IN_PROGRESS", "detailsUrl": ""},
    ]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json(checks))):
        r = agent_prod.ci_status(pr=42)
    assert r["rollup"] == "red"


def test_ci_status_cancelled_is_red():
    checks = [{"name": "pytest", "conclusion": "CANCELLED", "status": "COMPLETED", "detailsUrl": ""}]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json(checks))):
        r = agent_prod.ci_status(pr=1)
    assert r["rollup"] == "red"


def test_ci_status_timed_out_is_red():
    checks = [{"name": "pytest", "conclusion": "TIMED_OUT", "status": "COMPLETED", "detailsUrl": ""}]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json(checks))):
        r = agent_prod.ci_status(pr=1)
    assert r["rollup"] == "red"


def test_ci_status_skipped_is_green():
    checks = [
        {"name": "a", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": ""},
        {"name": "b", "conclusion": "SKIPPED", "status": "COMPLETED", "detailsUrl": ""},
    ]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json(checks))):
        r = agent_prod.ci_status(pr=1)
    assert r["rollup"] == "green"


def test_ci_status_no_checks():
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json([]))):
        r = agent_prod.ci_status(pr=1)
    assert r["rollup"] == "none"


def test_ci_status_merged_state():
    checks = [{"name": "pytest", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": ""}]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(
        _gh_json(checks, state="MERGED", title="My PR", number=42))):
        r = agent_prod.ci_status(pr=42)
    assert r["state"] == "MERGED"
    assert r["title"] == "My PR"
    assert r["pr"] == 42


# ----- error paths -----

def test_ci_status_gh_failure():
    with patch.object(agent_prod, "_run", return_value={
        "ok": False, "returncode": 1, "stdout": "", "stderr": "boom",
        "duration_seconds": 0.01,
    }):
        r = agent_prod.ci_status(pr=1)
    assert r["ok"] is False
    assert "boom" in r["error"] or "gh failed" in r["error"]


def test_ci_status_bad_json():
    with patch.object(agent_prod, "_run", return_value=_mk_run_result("not json")):
        r = agent_prod.ci_status(pr=1)
    assert r["ok"] is False
    assert "json parse" in r["error"]


def test_ci_status_workflow_name_fallback():
    """If name is missing, fall back to workflowName."""
    checks = [{"workflowName": "CodeQL", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": ""}]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json(checks))):
        r = agent_prod.ci_status(pr=1)
    assert r["checks"][0]["name"] == "CodeQL"


# ----- prod_observe tool wiring -----

def test_prod_observe_dispatches_ci_status():
    import agent_tools
    checks = [{"name": "pytest", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": ""}]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json(checks))):
        out = agent_tools.prod_observe({"what": "ci_status", "pr": 26}, cwd="/tmp")
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert parsed["rollup"] == "green"


def test_prod_observe_unknown_what():
    import agent_tools
    with pytest.raises(ValueError):
        agent_tools.prod_observe({"what": "nuke"}, cwd="/tmp")


# ----- HTTP endpoint wiring -----

def test_endpoint_includes_ci_status_dispatch():
    from fastapi.testclient import TestClient
    import os
    os.environ.setdefault("KIRA_AUTH_TOKEN", "")
    import app as _app
    client = TestClient(_app.app)
    checks = [{"name": "pytest", "conclusion": "SUCCESS", "status": "COMPLETED", "detailsUrl": ""}]
    with patch.object(agent_prod, "_run", return_value=_mk_run_result(_gh_json(checks))):
        r = client.get("/agent/prod/ci_status?pr=26")
    assert r.status_code == 200
    body = r.json()
    assert body["rollup"] == "green"
