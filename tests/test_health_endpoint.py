"""Tests for /agent/health aggregator endpoint."""

import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app as app_mod
import agent_runtime
import agent_store


@pytest.fixture
def client():
    return TestClient(app_mod.app)


def test_health_basic_shape(client):
    r = client.get("/agent/health")
    assert r.status_code == 200
    d = r.json()
    for k in ("ok", "status", "reasons", "uptime_seconds", "started_at", "in_flight", "keys", "credits", "tools_24h"):
        assert k in d, f"missing key {k}"
    assert d["status"] in ("ok", "degraded", "critical")
    assert d["uptime_seconds"] >= 0
    assert isinstance(d["reasons"], list)
    assert isinstance(d["in_flight"], int)
    assert isinstance(d["in_flight_sids"], list)
    for ck in ("day", "month", "day_forecast", "month_forecast", "day_limit", "month_limit"):
        assert ck in d["credits"]
    for tk in ("total", "fail", "success_rate", "hook_denies", "top_errors"):
        assert tk in d["tools_24h"]


def test_health_reports_in_flight(client):
    """Register a fake cancel event and confirm it shows up."""
    sid = "healthsid1"
    ev = agent_runtime._register_cancel(sid)
    try:
        r = client.get("/agent/health")
        d = r.json()
        assert d["in_flight"] >= 1
        assert sid in d["in_flight_sids"]
    finally:
        agent_runtime._unregister_cancel(sid)


def test_health_critical_when_month_budget_exhausted(client, monkeypatch):
    monkeypatch.setattr(app_mod, "KIRA_MONTHLY_LIMIT", 10.0)
    monkeypatch.setattr(agent_store, "get_month_credits", lambda: 9.99)
    r = client.get("/agent/health")
    d = r.json()
    assert d["status"] == "critical"
    assert any("budget" in s.lower() for s in d["reasons"])


def test_health_degraded_when_one_key_banned(client, monkeypatch):
    from agent_keys import key_pool

    monkeypatch.setattr(
        key_pool,
        "status",
        lambda: {
            "pool_size": 2,
            "rotations": 0,
            "keys": [
                {"key_suffix": "aaa", "banned": True, "current": False},
                {"key_suffix": "bbb", "banned": False, "current": True},
            ],
        },
    )
    r = client.get("/agent/health")
    d = r.json()
    assert d["status"] == "degraded"
    assert any("banned" in s for s in d["reasons"])


def test_health_critical_when_all_keys_banned(client, monkeypatch):
    from agent_keys import key_pool

    monkeypatch.setattr(
        key_pool,
        "status",
        lambda: {
            "pool_size": 2,
            "keys": [
                {"key_suffix": "a", "banned": True, "current": False},
                {"key_suffix": "b", "banned": True, "current": False},
            ],
        },
    )
    r = client.get("/agent/health")
    d = r.json()
    assert d["status"] == "critical"
    assert any("all api keys" in s for s in d["reasons"])


def test_health_degraded_when_low_success_rate(client, monkeypatch):
    monkeypatch.setattr(
        agent_store,
        "compute_metrics",
        lambda sid=None, window_seconds=None: {
            "total": 30,
            "fail": 20,
            "ok": 10,
            "success_rate": 0.33,
            "hook_denies": 0,
            "top_errors": [{"tool": "x", "count": 5}],
        },
    )
    # ensure no other condition triggers; clear key bans
    from agent_keys import key_pool
    monkeypatch.setattr(key_pool, "status", lambda: {"pool_size": 1, "keys": [{"banned": False, "current": True}]})
    monkeypatch.setattr(agent_store, "get_month_credits", lambda: 0.0)
    r = client.get("/agent/health")
    d = r.json()
    assert d["status"] == "degraded"
    assert any("success_rate" in s for s in d["reasons"])


def test_health_handles_metrics_exception(client, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(agent_store, "compute_metrics", boom)
    r = client.get("/agent/health")
    d = r.json()
    # Should still return 200 with defaults for tools_24h
    assert d["ok"] is True
    assert d["tools_24h"]["total"] == 0


def test_health_handles_credits_exception(client, monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(agent_store, "get_today_credits", boom)
    monkeypatch.setattr(agent_store, "get_month_credits", boom)
    r = client.get("/agent/health")
    d = r.json()
    assert d["credits"]["day"] == 0.0
    assert d["credits"]["month"] == 0.0


def test_health_handles_keys_exception(client, monkeypatch):
    from agent_keys import key_pool

    def boom():
        raise RuntimeError("pool dead")

    monkeypatch.setattr(key_pool, "status", boom)
    r = client.get("/agent/health")
    d = r.json()
    assert "error" in d["keys"]
