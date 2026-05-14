"""Server-side wait mode for ci_status (PR introducing wait=true).

Motivation: drills 2/3/4 showed Sonnet terminates the agent loop after
repeated `sleep` or pending tool_results. Move the poll loop into the
tool.
"""
import json
from unittest.mock import patch

import agent_prod


def _run_ok(stdout: str) -> dict:
    return {"ok": True, "returncode": 0, "stdout": stdout,
            "stderr": "", "duration_seconds": 0.05}


def _gh(checks: list[dict], state: str = "OPEN") -> str:
    return json.dumps({"number": 99, "title": "T", "state": state,
                       "statusCheckRollup": checks})


GREEN = [{"name": "pytest", "conclusion": "SUCCESS", "status": "COMPLETED"}]
PENDING = [{"name": "pytest", "conclusion": None, "status": "IN_PROGRESS"}]
RED = [{"name": "pytest", "conclusion": "FAILURE", "status": "COMPLETED"}]


def test_wait_false_default_single_poll():
    with patch.object(agent_prod, "_run", return_value=_run_ok(_gh(PENDING))) as m:
        r = agent_prod.ci_status(pr=42)
    assert r["ok"] is True
    assert r["rollup"] == "pending"
    assert r["polls"] == 1
    assert m.call_count == 1


def test_wait_true_polls_until_green(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    responses = [
        _run_ok(_gh(PENDING)),
        _run_ok(_gh(PENDING)),
        _run_ok(_gh(GREEN)),
    ]
    with patch.object(agent_prod, "_run", side_effect=responses):
        r = agent_prod.ci_status(pr=42, wait=True, timeout=300, poll_interval=2)
    assert r["ok"] is True
    assert r["rollup"] == "green"
    assert r["polls"] == 3
    assert sleeps == [2, 2]  # slept between, not after final
    assert r.get("timeout") is not True


def test_wait_true_stops_on_red(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    responses = [_run_ok(_gh(PENDING)), _run_ok(_gh(RED))]
    with patch.object(agent_prod, "_run", side_effect=responses):
        r = agent_prod.ci_status(pr=42, wait=True, poll_interval=2)
    assert r["rollup"] == "red"
    assert r["polls"] == 2


def test_wait_true_honors_timeout(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    with patch.object(agent_prod, "_run", return_value=_run_ok(_gh(PENDING))):
        # timeout=5, poll_interval=2 → expect to stop after a few polls
        r = agent_prod.ci_status(pr=42, wait=True, timeout=5, poll_interval=2)
    assert r["ok"] is True
    assert r["rollup"] == "pending"
    assert r.get("timeout") is True
    assert r["polls"] >= 1


def test_wait_clamps_poll_interval_low():
    # poll_interval=0 should clamp to >= 2; we just check it doesn't crash and
    # returns immediately on non-pending.
    with patch.object(agent_prod, "_run", return_value=_run_ok(_gh(GREEN))):
        r = agent_prod.ci_status(pr=42, wait=True, timeout=10, poll_interval=0)
    assert r["rollup"] == "green"
    assert r["polls"] == 1


def test_wait_clamps_timeout_high(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    with patch.object(agent_prod, "_run", return_value=_run_ok(_gh(GREEN))):
        r = agent_prod.ci_status(pr=42, wait=True, timeout=99999, poll_interval=2)
    assert r["rollup"] == "green"


def test_wait_propagates_run_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    bad = {"ok": False, "stderr": "boom", "returncode": 1, "stdout": ""}
    with patch.object(agent_prod, "_run", return_value=bad):
        r = agent_prod.ci_status(pr=42, wait=True, timeout=10, poll_interval=2)
    assert r["ok"] is False
    assert "boom" in r["error"]
    assert r["polls"] == 1


def test_response_marker_includes_polls_and_waited(monkeypatch):
    """prod_observe marker line carries the new fields."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    import agent_tools
    with patch.object(agent_prod, "_run", return_value=_run_ok(_gh(GREEN))):
        out = agent_tools.prod_observe(
            {"what": "ci_status", "pr": 42, "wait": True, "poll_interval": 2},
            cwd="/tmp",
        )
    first_line = out.splitlines()[0]
    assert first_line.startswith("OK rollup=green pr=42")
    assert "polls=" in first_line
    assert "waited=" in first_line
