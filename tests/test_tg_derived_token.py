"""Tests for per-user Telegram bearer tokens.

These tokens let one Kira instance serve many TG users without a database:
the bot HMACs the tg_user_id under a server-side secret, the middleware
verifies on each request, and downstream code (sessions, quotas, history)
naturally isolates because user_id_from_token(token) hashes to a stable
per-token identifier.
"""
from __future__ import annotations

import importlib
import os

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import agent_auth


def test_derive_and_verify_roundtrip():
    secret = "top-secret-server-side"
    tok = agent_auth.derive_tg_token(424242, secret)
    assert tok.startswith("ktk_tg_424242_")
    assert len(tok.split("_")[-1]) == 16
    assert agent_auth.verify_tg_token(tok, secret) == 424242


def test_derive_is_deterministic():
    s = "abc"
    assert agent_auth.derive_tg_token(7, s) == agent_auth.derive_tg_token(7, s)


def test_different_users_get_different_tokens():
    s = "abc"
    assert agent_auth.derive_tg_token(1, s) != agent_auth.derive_tg_token(2, s)


def test_different_secrets_invalidate_token():
    tok = agent_auth.derive_tg_token(1, "old")
    assert agent_auth.verify_tg_token(tok, "new") is None


def test_verify_rejects_garbage():
    assert agent_auth.verify_tg_token("", "s") is None
    assert agent_auth.verify_tg_token("ktk_admin", "s") is None
    assert agent_auth.verify_tg_token("ktk_tg_nope", "s") is None
    assert agent_auth.verify_tg_token("ktk_tg_1_short", "s") is None
    # Right shape, wrong tag
    bad = "ktk_tg_1_" + "f" * 16
    assert agent_auth.verify_tg_token(bad, "secret") is None


def test_derive_requires_secret():
    with pytest.raises(ValueError):
        agent_auth.derive_tg_token(1, "")


def _app_with_auth(monkeypatch, **env):
    # Clear any env from prior tests so each scenario is hermetic.
    for k in ("KIRA_AUTH_TOKEN", "KIRA_TG_DERIVE_SECRET",
              "KIRA_TG_ALLOWED_USERS", "KIRA_RATE_LIMIT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # Re-import to re-evaluate build_middleware() with the fresh env.
    importlib.reload(agent_auth)
    app = FastAPI()
    agent_auth.install(app)

    @app.get("/whoami")
    def _who(request: Request):
        return {"uid": agent_auth.current_user_id(request)}

    return TestClient(app)


def test_middleware_accepts_derived_tg_token(monkeypatch):
    secret = "mw-secret"
    client = _app_with_auth(
        monkeypatch,
        KIRA_AUTH_TOKEN="admin-token",
        KIRA_TG_DERIVE_SECRET=secret,
        KIRA_RATE_LIMIT="0",
    )
    tok = agent_auth.derive_tg_token(111, secret)
    r = client.get("/whoami", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    uid_111 = r.json()["uid"]

    # Different TG user → different stamped user_id (isolation works).
    tok2 = agent_auth.derive_tg_token(222, secret)
    r2 = client.get("/whoami", headers={"Authorization": f"Bearer {tok2}"})
    assert r2.json()["uid"] != uid_111


def test_middleware_rejects_forged_tg_token(monkeypatch):
    client = _app_with_auth(
        monkeypatch,
        KIRA_AUTH_TOKEN="admin-token",
        KIRA_TG_DERIVE_SECRET="real-secret",
        KIRA_RATE_LIMIT="0",
    )
    forged = "ktk_tg_999_" + "a" * 16
    r = client.get("/whoami", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_middleware_respects_tg_whitelist(monkeypatch):
    secret = "s"
    client = _app_with_auth(
        monkeypatch,
        KIRA_AUTH_TOKEN="admin-token",
        KIRA_TG_DERIVE_SECRET=secret,
        KIRA_TG_ALLOWED_USERS="111",
        KIRA_RATE_LIMIT="0",
    )
    ok = client.get("/whoami", headers={
        "Authorization": f"Bearer {agent_auth.derive_tg_token(111, secret)}"
    })
    assert ok.status_code == 200

    blocked = client.get("/whoami", headers={
        "Authorization": f"Bearer {agent_auth.derive_tg_token(222, secret)}"
    })
    assert blocked.status_code == 401


def test_admin_token_still_works_alongside_tg(monkeypatch):
    client = _app_with_auth(
        monkeypatch,
        KIRA_AUTH_TOKEN="admin-token",
        KIRA_TG_DERIVE_SECRET="s",
        KIRA_RATE_LIMIT="0",
    )
    r = client.get("/whoami", headers={"Authorization": "Bearer admin-token"})
    assert r.status_code == 200
