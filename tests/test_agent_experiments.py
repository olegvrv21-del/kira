"""Tests for agent_experiments.load() + /agent/experiments wiring."""
from pathlib import Path

import pytest

import agent_experiments


HEADER = "ts\ttag\tidea\tpr\tstatus\tci\ttests_after\tnotes\n"
ROW_GREEN = "2026-05-14T14:20:00Z\tdrill-5b\tserver-side-wait\t39\tgreen\tgreen\t1082\t1polls/0.5s\n"
ROW_OPENED = "2026-05-14T15:00:00Z\tdrill-6\tnext-experiment\t40\topened\t\t\t\n"
ROW_RED = "2026-05-14T15:10:00Z\tdrill-7\tflaky-thing\t41\tred\tred\t\tpytest failed: test_x\n"


def _write(tmp_path, text):
    p = tmp_path / "experiments.tsv"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_missing(tmp_path):
    r = agent_experiments.load(tmp_path / "nope.tsv")
    assert r["ok"] is False
    assert r["reason"] == "missing"


def test_load_empty(tmp_path):
    p = _write(tmp_path, "")
    r = agent_experiments.load(p)
    assert r["ok"] is False
    assert r["reason"] == "empty"


def test_load_header_only(tmp_path):
    p = _write(tmp_path, HEADER)
    r = agent_experiments.load(p)
    assert r["ok"] is True
    assert r["rows"] == []
    assert r["count"] == 0


def test_load_normal_rows(tmp_path):
    p = _write(tmp_path, HEADER + ROW_GREEN + ROW_OPENED + ROW_RED)
    r = agent_experiments.load(p)
    assert r["ok"] is True
    assert r["count"] == 3
    rows = r["rows"]
    assert rows[0]["tag"] == "drill-5b"
    assert rows[0]["status"] == "green"
    assert rows[0]["pr"] == "39"
    assert rows[1]["status"] == "opened"
    assert rows[1]["ci"] == ""
    assert rows[2]["notes"] == "pytest failed: test_x"


def test_load_partial_row_is_padded(tmp_path):
    # Half-written row (autoresearch wrote `opened` then session died)
    partial = "2026-05-14T16:00:00Z\tdrill-x\tidea\t42\topened\n"
    p = _write(tmp_path, HEADER + partial)
    r = agent_experiments.load(p)
    assert r["ok"] is True
    assert r["count"] == 1
    row = r["rows"][0]
    assert row["status"] == "opened"
    assert row["ci"] == ""
    assert row["tests_after"] == ""
    assert row["notes"] == ""


def test_load_extra_tabs_in_notes_preserved(tmp_path):
    # Notes column may legitimately contain tabs if Kira pastes a marker line
    row = "2026-05-14T17:00:00Z\ttag\tidea\t43\tred\tred\t\ta\tb\tc\n"
    p = _write(tmp_path, HEADER + row)
    r = agent_experiments.load(p)
    assert r["ok"] is True
    assert r["rows"][0]["notes"] == "a\tb\tc"


def test_load_blank_lines_skipped(tmp_path):
    p = _write(tmp_path, HEADER + ROW_GREEN + "\n\n" + ROW_RED)
    r = agent_experiments.load(p)
    assert r["ok"] is True
    assert r["count"] == 2


def test_load_unexpected_header_flagged_but_parses(tmp_path):
    bad_header = "timestamp\ttag\tidea\tpr\tstatus\tci\ttests_after\tnotes\n"
    p = _write(tmp_path, bad_header + ROW_GREEN)
    r = agent_experiments.load(p)
    assert r["ok"] is True
    assert r["count"] == 1
    assert "warning" in r
    assert "timestamp" in r["warning"]


def test_load_too_large_rejected(tmp_path, monkeypatch):
    p = _write(tmp_path, HEADER + ROW_GREEN)
    monkeypatch.setattr(agent_experiments, "MAX_BYTES", 10)
    r = agent_experiments.load(p)
    assert r["ok"] is False
    assert r["reason"] == "too_large"
    assert r["size"] > 10


def test_load_max_rows_truncates(tmp_path, monkeypatch):
    rows_text = HEADER + (ROW_GREEN * 10)
    p = _write(tmp_path, rows_text)
    monkeypatch.setattr(agent_experiments, "MAX_ROWS", 3)
    r = agent_experiments.load(p)
    assert r["ok"] is True
    assert r["count"] == 3
    assert r["truncated"] is True


def test_notebook_path_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KIRA_NOTEBOOK_DIR", str(tmp_path))
    assert agent_experiments.notebook_path() == tmp_path / "experiments.tsv"


# ---- endpoint wiring ----

def test_endpoint_returns_parsed_tsv(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KIRA_NOTEBOOK_DIR", str(tmp_path))
    _write(tmp_path, HEADER + ROW_GREEN)

    import app as app_mod
    client = TestClient(app_mod.app)
    r = client.get("/agent/experiments")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["count"] == 1
    assert j["rows"][0]["tag"] == "drill-5b"


def test_endpoint_when_missing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KIRA_NOTEBOOK_DIR", str(tmp_path))
    import app as app_mod
    client = TestClient(app_mod.app)
    r = client.get("/agent/experiments")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is False
    assert j["reason"] == "missing"
