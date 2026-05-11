"""Coverage status endpoint reads coverage.json and returns a UI summary."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent_coverage
import app as app_mod


@pytest.fixture
def tmp_cov(tmp_path, monkeypatch):
    p = tmp_path / "coverage.json"
    sample = {
        "meta": {"timestamp": "2026-05-11T15:30:00"},
        "totals": {
            "num_statements": 100,
            "covered_lines": 65,
            "missing_lines": 35,
            "percent_covered": 65.0,
        },
        "files": {
            "app.py": {
                "summary": {
                    "num_statements": 50,
                    "covered_lines": 30,
                    "missing_lines": 20,
                    "percent_covered": 60.0,
                },
                "executed_lines": [1, 2, 3],
                "missing_lines": [10, 11, 12],
                "excluded_lines": [],
            },
            "agent_runtime.py": {
                "summary": {
                    "num_statements": 50,
                    "covered_lines": 35,
                    "missing_lines": 15,
                    "percent_covered": 70.0,
                },
                "executed_lines": [],
                "missing_lines": [],
                "excluded_lines": [],
            },
        },
    }
    p.write_text(json.dumps(sample))
    monkeypatch.setattr(agent_coverage, "COVERAGE_JSON", Path(p))
    return p


def test_status_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_coverage, "COVERAGE_JSON", tmp_path / "nope.json")
    s = agent_coverage.status()
    assert s["ok"] is False
    assert "not found" in s["error"].lower()


def test_status_summary(tmp_cov):
    s = agent_coverage.status()
    assert s["ok"]
    assert s["total_percent"] == 65.0
    assert s["total_statements"] == 100
    assert len(s["files"]) == 2
    # files sorted by ascending percent (worst first)
    assert s["files"][0]["path"] == "app.py"
    assert s["files"][0]["percent"] == 60.0
    assert s["files"][1]["path"] == "agent_runtime.py"


def test_file_detail_present(tmp_cov):
    d = agent_coverage.file_detail("app.py")
    assert d["ok"]
    assert d["summary"]["percent"] == 60.0
    assert d["missing_lines"] == [10, 11, 12]


def test_file_detail_missing(tmp_cov):
    d = agent_coverage.file_detail("does_not_exist.py")
    assert d["ok"] is False


def test_run_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KIRA_COVERAGE_ALLOW_RUN", raising=False)
    r = agent_coverage.run()
    assert r["ok"] is False
    assert "KIRA_COVERAGE_ALLOW_RUN" in r["error"]


def test_endpoint_status(tmp_cov):
    c = TestClient(app_mod.app)
    r = c.get("/agent/coverage")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["total_percent"] == 65.0


def test_endpoint_file(tmp_cov):
    c = TestClient(app_mod.app)
    r = c.get("/agent/coverage/file", params={"path": "app.py"})
    assert r.status_code == 200
    assert r.json()["ok"]


def test_endpoint_run_blocked():
    c = TestClient(app_mod.app)
    r = c.post("/agent/coverage/run")
    assert r.status_code == 200
    assert r.json()["ok"] is False
