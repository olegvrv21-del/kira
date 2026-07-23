"""Tests for agent_frugal.py — the 'cheap-capable first' spending guard."""
from __future__ import annotations

import importlib

import pytest

import agent_store


@pytest.fixture
def frugal(tmp_path, monkeypatch):
    # Isolate the DB so tier_usage counting doesn't touch the real one.
    db = tmp_path / "t.db"
    monkeypatch.setattr(agent_store, "DB_PATH", str(db))
    agent_store.init()
    import agent_frugal
    importlib.reload(agent_frugal)
    for k in ("KIRA_FRUGAL", "KIRA_EXPENSIVE_MODELS", "KIRA_EXPENSIVE_DAILY_CAP",
              "KIRA_FRUGAL_DOWNGRADE"):
        monkeypatch.delenv(k, raising=False)
    return agent_frugal


def test_cheap_model_untouched(frugal):
    model, note = frugal.guard("gpt-5.4-mini")
    assert model == "gpt-5.4-mini"
    assert note is None
    assert frugal.expensive_calls_today() == 0


@pytest.mark.parametrize("m,expensive", [
    ("claude-opus-4-8", True),
    ("gpt-5.6", True),
    ("gpt-5.5", True),
    ("gpt-5.4-mini", False),
    ("claude-haiku-4-5", False),
    ("gpt-5.4", False),
])
def test_is_expensive(frugal, m, expensive):
    assert frugal.is_expensive(m) is expensive


def test_expensive_under_cap_counts(frugal, monkeypatch):
    monkeypatch.setenv("KIRA_EXPENSIVE_DAILY_CAP", "3")
    for i in range(3):
        model, note = frugal.guard("claude-opus-4-8")
        assert model == "claude-opus-4-8"
        assert note is None
    assert frugal.expensive_calls_today() == 3


def test_over_cap_downgrades(frugal, monkeypatch):
    monkeypatch.setenv("KIRA_EXPENSIVE_DAILY_CAP", "2")
    monkeypatch.setenv("KIRA_FRUGAL_DOWNGRADE", "gpt-5.4")
    frugal.guard("gpt-5.6")
    frugal.guard("gpt-5.6")
    model, note = frugal.guard("gpt-5.6")  # 3rd → over cap
    assert model == "gpt-5.4"
    assert note is not None and "лимит" in note.lower()


def test_disabled_passes_through(frugal, monkeypatch):
    monkeypatch.setenv("KIRA_FRUGAL", "0")
    monkeypatch.setenv("KIRA_EXPENSIVE_DAILY_CAP", "1")
    frugal.guard("claude-opus-4-8")
    model, note = frugal.guard("claude-opus-4-8")
    assert model == "claude-opus-4-8"  # not downgraded when disabled
    assert note is None


def test_cap_zero_means_unlimited(frugal, monkeypatch):
    monkeypatch.setenv("KIRA_EXPENSIVE_DAILY_CAP", "0")
    for _ in range(5):
        model, note = frugal.guard("claude-opus-4-8")
        assert model == "claude-opus-4-8"
        assert note is None


def test_misconfigured_downgrade_does_not_loop(frugal, monkeypatch):
    monkeypatch.setenv("KIRA_EXPENSIVE_DAILY_CAP", "1")
    monkeypatch.setenv("KIRA_FRUGAL_DOWNGRADE", "gpt-5.6")  # itself expensive!
    frugal.guard("claude-opus-4-8")
    model, note = frugal.guard("claude-opus-4-8")  # over cap
    # Must NOT downgrade to another expensive model; keep original + warn.
    assert model == "claude-opus-4-8"
    assert note is not None


def test_custom_expensive_markers(frugal, monkeypatch):
    monkeypatch.setenv("KIRA_EXPENSIVE_MODELS", "glm")
    assert frugal.is_expensive("glm-5.2") is True
    assert frugal.is_expensive("claude-opus-4-8") is False


def test_status_shape(frugal):
    s = frugal.status()
    assert set(s) >= {"enabled", "expensive_calls_today", "daily_cap",
                      "expensive_markers", "downgrade_model"}
