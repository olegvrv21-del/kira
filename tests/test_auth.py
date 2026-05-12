"""Bearer auth + rate-limit middleware.

We build a small FastAPI app per test (rather than mutate the real one)
so the middleware install/configuration is deterministic.
"""

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_auth


def _make_app(env: dict):
    """Return a fresh app with auth_module fed via env."""
    import os

    for k in ("KIRA_AUTH_TOKEN", "KIRA_AUTH_ALLOW_PUBLIC", "KIRA_RATE_LIMIT", "KIRA_RATE_WINDOW"):
        os.environ.pop(k, None)
    os.environ.update(env)
    app = FastAPI()
    agent_auth.install(app)

    @app.get("/healthz")
    async def hz():
        return {"ok": True}

    @app.get("/agent/sessions")
    async def sessions():
        return {"sessions": []}

    @app.post("/agent")
    async def agent():
        return {"ok": True}

    return app


# ---------- defaults: no env -> no auth, no rate limit ----------


def test_disabled_by_default(monkeypatch):
    for k in ("KIRA_AUTH_TOKEN", "KIRA_RATE_LIMIT"):
        monkeypatch.delenv(k, raising=False)
    # also disable rate limit (limit=0 also turns it off)
    monkeypatch.setenv("KIRA_RATE_LIMIT", "0")
    app = _make_app({"KIRA_RATE_LIMIT": "0"})
    c = TestClient(app)
    assert c.get("/healthz").status_code == 200
    assert c.post("/agent").status_code == 200  # no auth, no limit
    snap = agent_auth.snapshot()
    assert snap == {"enabled": False}


# ---------- auth ----------


def test_auth_blocks_without_token():
    app = _make_app({"KIRA_AUTH_TOKEN": "sekret", "KIRA_RATE_LIMIT": "0"})
    c = TestClient(app)
    r = c.post("/agent")
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_auth_allows_with_correct_bearer():
    app = _make_app({"KIRA_AUTH_TOKEN": "sekret", "KIRA_RATE_LIMIT": "0"})
    c = TestClient(app)
    r = c.post("/agent", headers={"Authorization": "Bearer sekret"})
    assert r.status_code == 200


def test_auth_allows_with_x_kira_token():
    app = _make_app({"KIRA_AUTH_TOKEN": "sekret", "KIRA_RATE_LIMIT": "0"})
    c = TestClient(app)
    r = c.post("/agent", headers={"X-Kira-Token": "sekret"})
    assert r.status_code == 200


def test_auth_allows_with_query_token():
    """Inline <img>/<a> can only pass auth via ?token=...; middleware must accept it."""
    app = _make_app({"KIRA_AUTH_TOKEN": "sekret", "KIRA_RATE_LIMIT": "0"})
    c = TestClient(app)
    r = c.get("/agent/sessions?token=sekret")
    assert r.status_code == 200
    r2 = c.get("/agent/sessions?token=wrong")
    assert r2.status_code == 401
    r3 = c.get("/agent/sessions")
    assert r3.status_code == 401


def test_auth_rejects_wrong_token():
    app = _make_app({"KIRA_AUTH_TOKEN": "sekret", "KIRA_RATE_LIMIT": "0"})
    c = TestClient(app)
    r = c.post("/agent", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_auth_multi_token_csv():
    app = _make_app({"KIRA_AUTH_TOKEN": "alice,bob", "KIRA_RATE_LIMIT": "0"})
    c = TestClient(app)
    assert c.post("/agent", headers={"Authorization": "Bearer alice"}).status_code == 200
    assert c.post("/agent", headers={"Authorization": "Bearer bob"}).status_code == 200
    assert c.post("/agent", headers={"Authorization": "Bearer charlie"}).status_code == 401


def test_auth_healthz_public():
    app = _make_app({"KIRA_AUTH_TOKEN": "sekret", "KIRA_RATE_LIMIT": "0"})
    c = TestClient(app)
    assert c.get("/healthz").status_code == 200  # no token needed


def test_auth_get_session_still_blocked():
    app = _make_app({"KIRA_AUTH_TOKEN": "sekret", "KIRA_RATE_LIMIT": "0"})
    c = TestClient(app)
    assert c.get("/agent/sessions").status_code == 401


# ---------- rate limit ----------


def test_rate_limit_blocks_after_threshold():
    app = _make_app({"KIRA_RATE_LIMIT": "3", "KIRA_RATE_WINDOW": "60"})
    c = TestClient(app)
    for _ in range(3):
        assert c.post("/agent").status_code == 200
    r = c.post("/agent")
    assert r.status_code == 429
    body = r.json()
    assert body["error"] == "rate_limit"
    assert body["retry_after"] >= 1
    assert r.headers["Retry-After"] == str(body["retry_after"])


def test_rate_limit_does_not_apply_to_get():
    app = _make_app({"KIRA_RATE_LIMIT": "1", "KIRA_RATE_WINDOW": "60"})
    c = TestClient(app)
    # Many GETs are fine; rate limit applies only to state-changing requests.
    for _ in range(10):
        assert c.get("/agent/sessions").status_code == 200


def test_rate_limit_per_ip_via_xff():
    """Different X-Forwarded-For values get separate buckets."""
    app = _make_app({"KIRA_RATE_LIMIT": "2", "KIRA_RATE_WINDOW": "60"})
    c = TestClient(app)
    for _ in range(2):
        assert c.post("/agent", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 200
    assert c.post("/agent", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 429
    # different IP -> independent bucket
    assert c.post("/agent", headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 200


def test_rate_limit_sliding_window():
    """After the window expires, requests are allowed again."""
    app = _make_app({"KIRA_RATE_LIMIT": "1", "KIRA_RATE_WINDOW": "1"})
    c = TestClient(app)
    assert c.post("/agent").status_code == 200
    assert c.post("/agent").status_code == 429
    time.sleep(1.1)
    assert c.post("/agent").status_code == 200


def test_snapshot_after_install():
    _make_app({"KIRA_AUTH_TOKEN": "t", "KIRA_RATE_LIMIT": "5", "KIRA_RATE_WINDOW": "60"})
    snap = agent_auth.snapshot()
    assert snap["enabled"]
    assert snap["limit"] == 5
    assert snap["window_seconds"] == 60


# Clean up env vars after tests
@pytest.fixture(autouse=True)
def _restore_env(monkeypatch):
    yield
    for k in ("KIRA_AUTH_TOKEN", "KIRA_AUTH_ALLOW_PUBLIC", "KIRA_RATE_LIMIT", "KIRA_RATE_WINDOW"):
        monkeypatch.delenv(k, raising=False)
