"""Cover agent_coverage branches."""

import json
import subprocess
from pathlib import Path

import pytest

import agent_coverage


def _write_cov(monkeypatch, tmp_path, data):
    p = tmp_path / "coverage.json"
    p.write_text(json.dumps(data))
    monkeypatch.setattr(agent_coverage, "COVERAGE_JSON", p)
    return p


def test_load_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_coverage, "COVERAGE_JSON", tmp_path / "nope.json")
    assert agent_coverage._load() is None


def test_load_bad_json(monkeypatch, tmp_path):
    p = tmp_path / "coverage.json"
    p.write_text("not json {{")
    monkeypatch.setattr(agent_coverage, "COVERAGE_JSON", p)
    assert agent_coverage._load() is None


def test_status_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_coverage, "COVERAGE_JSON", tmp_path / "x.json")
    s = agent_coverage.status()
    assert s["ok"] is False and "not found" in s["error"]


def test_status_full(monkeypatch, tmp_path):
    _write_cov(monkeypatch, tmp_path, {
        "meta": {"timestamp": "2026-01-01T00:00:00"},
        "totals": {"percent_covered": 87.7, "num_statements": 100, "covered_lines": 88, "missing_lines": 12},
        "files": {
            "a.py": {"summary": {"num_statements": 50, "covered_lines": 25, "missing_lines": 25, "percent_covered": 50.0}},
            "b.py": {"summary": {"num_statements": 30, "covered_lines": 30, "missing_lines": 0, "percent_covered": 100.0}},
        },
    })
    s = agent_coverage.status()
    assert s["ok"] and s["total_percent"] == 87.7
    assert s["files"][0]["path"] == "a.py"  # sorted by percent ascending
    assert s["age_seconds"] >= 0


def test_file_detail_missing_coverage_json(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_coverage, "COVERAGE_JSON", tmp_path / "x.json")
    d = agent_coverage.file_detail("a.py")
    assert d["ok"] is False


def test_file_detail_unknown_path(monkeypatch, tmp_path):
    _write_cov(monkeypatch, tmp_path, {"files": {"a.py": {"summary": {}}}})
    d = agent_coverage.file_detail("nope.py")
    assert d["ok"] is False and "no coverage entry" in d["error"]


def test_file_detail_ok(monkeypatch, tmp_path):
    _write_cov(monkeypatch, tmp_path, {
        "files": {
            "a.py": {
                "executed_lines": [1, 2, 3],
                "missing_lines": [10, 11],
                "excluded_lines": [],
                "summary": {"num_statements": 5, "covered_lines": 3, "missing_lines": 2, "percent_covered": 60.0},
            }
        }
    })
    d = agent_coverage.file_detail("a.py")
    assert d["ok"] and d["summary"]["percent"] == 60.0
    assert d["missing_lines"] == [10, 11]


def test_run_disabled_without_env(monkeypatch):
    monkeypatch.delenv("KIRA_COVERAGE_ALLOW_RUN", raising=False)
    r = agent_coverage.run()
    assert r["ok"] is False and "KIRA_COVERAGE_ALLOW_RUN" in r["error"]


def test_run_timeout(monkeypatch):
    monkeypatch.setenv("KIRA_COVERAGE_ALLOW_RUN", "1")

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    r = agent_coverage.run(timeout=1)
    assert r["ok"] is False and "timeout" in r["error"]


def test_run_completes(monkeypatch):
    monkeypatch.setenv("KIRA_COVERAGE_ALLOW_RUN", "1")

    class FakeProc:
        returncode = 0
        stdout = "line1\nline2\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeProc())
    r = agent_coverage.run(timeout=5)
    assert r["ok"] is True and r["returncode"] == 0
    assert r["stdout_tail"] == ["line1", "line2"]
