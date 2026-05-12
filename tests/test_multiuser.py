"""Multi-user lite: per-token isolation of sessions/credits via owner_id."""

import importlib
import os

import pytest
from fastapi.testclient import TestClient

import agent_auth


# ---------- store-level ----------


def test_user_id_from_token_deterministic():
    a = agent_auth.user_id_from_token("alice-secret")
    b = agent_auth.user_id_from_token("alice-secret")
    c = agent_auth.user_id_from_token("bob-secret")
    assert a == b
    assert a != c
    assert len(a) == 12


def test_user_id_anon_when_no_token():
    assert agent_auth.user_id_from_token(None) == "anon"
    assert agent_auth.user_id_from_token("") == "anon"


def test_session_owner_isolation(store):
    h = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nx\n--- USER MESSAGE END ---"}}]
    store.save_session("mu_a", h, "m", title="A", owner_id="userA")
    store.save_session("mu_b", h, "m", title="B", owner_id="userB")
    # Cross-user load returns None
    assert store.load_history("mu_a", owner_id="userB") is None
    assert store.load_history("mu_b", owner_id="userA") is None
    # Own load works
    assert store.load_history("mu_a", owner_id="userA") is not None
    # No owner_id filter -> sees everything
    assert store.load_history("mu_a", owner_id=None) is not None


def test_list_sessions_filtered_by_owner(store):
    h = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\ny\n--- USER MESSAGE END ---"}}]
    store.save_session("mu_la", h, "m", title="A", owner_id="ownerA")
    store.save_session("mu_lb", h, "m", title="B", owner_id="ownerB")
    sids_a = {s["sid"] for s in store.list_sessions(limit=200, owner_id="ownerA")}
    sids_b = {s["sid"] for s in store.list_sessions(limit=200, owner_id="ownerB")}
    assert "mu_la" in sids_a and "mu_lb" not in sids_a
    assert "mu_lb" in sids_b and "mu_la" not in sids_b


def test_legacy_null_owner_visible_to_all(store):
    h = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nz\n--- USER MESSAGE END ---"}}]
    # Legacy: no owner_id at save time -> NULL in DB
    store.save_session("mu_legacy", h, "m", title="L")
    assert store.load_history("mu_legacy", owner_id="anyone") is not None
    sids = {s["sid"] for s in store.list_sessions(limit=200, owner_id="anyone")}
    assert "mu_legacy" in sids


def test_legacy_session_claimed_on_first_save(store):
    h = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nq\n--- USER MESSAGE END ---"}}]
    store.save_session("mu_claim", h, "m", title="L")  # legacy
    assert store.session_owner("mu_claim") is None
    # First authed save claims it
    store.save_session("mu_claim", h, "m", title="L", owner_id="claimer")
    assert store.session_owner("mu_claim") == "claimer"
    # Now invisible to other users
    assert store.load_history("mu_claim", owner_id="other") is None


def test_rename_blocked_for_foreign_user(store):
    h = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nr\n--- USER MESSAGE END ---"}}]
    store.save_session("mu_rn", h, "m", title="t", owner_id="userA")
    assert store.rename_session("mu_rn", "hacked", owner_id="userB") is False
    assert store.rename_session("mu_rn", "ok", owner_id="userA") is True


def test_delete_blocked_for_foreign_user(store):
    h = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nd\n--- USER MESSAGE END ---"}}]
    store.save_session("mu_del", h, "m", title="t", owner_id="userA")
    assert store.delete_session("mu_del", owner_id="userB") is False
    assert store.load_history("mu_del", owner_id="userA") is not None
    assert store.delete_session("mu_del", owner_id="userA") is True


def test_user_credits_tracked_per_user(store):
    # Record some credits for two distinct users via two sessions
    store.save_session("mu_c1", [], "m", title="A", owner_id="uA")
    store.save_session("mu_c2", [], "m", title="B", owner_id="uB")
    store.record_credits("mu_c1", 2.5, owner_id="uA")
    store.record_credits("mu_c2", 1.0, owner_id="uB")
    assert store.get_user_today_credits("uA") >= 2.5
    assert store.get_user_today_credits("uB") >= 1.0
    # uA shouldn't see uB's credits
    assert abs(store.get_user_today_credits("uA") - store.get_user_today_credits("uB")) >= 1.0


# ---------- HTTP-level ----------


@pytest.fixture
def auth_client(monkeypatch):
    """Spin up the real app with auth enabled and two valid tokens.

    Because agent_auth.install runs at app-import time, we need to set env
    BEFORE importing app, and we re-import a fresh copy.
    """
    os.environ["KIRA_AUTH_TOKEN"] = "tok_alice,tok_bob"
    os.environ["KIRA_RATE_LIMIT"] = "0"
    # Reload app so middleware picks up the new env.
    import app as app_mod
    importlib.reload(app_mod)
    yield TestClient(app_mod.app)
    os.environ.pop("KIRA_AUTH_TOKEN", None)
    os.environ.pop("KIRA_RATE_LIMIT", None)
    # Reload back to default for other tests.
    importlib.reload(app_mod)


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_http_sessions_isolated(auth_client, store):
    h = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nx\n--- USER MESSAGE END ---"}}]
    uid_alice = agent_auth.user_id_from_token("tok_alice")
    uid_bob = agent_auth.user_id_from_token("tok_bob")
    store.save_session("mu_http_a", h, "m", title="alice", owner_id=uid_alice)
    store.save_session("mu_http_b", h, "m", title="bob", owner_id=uid_bob)

    ra = auth_client.get("/agent/sessions", headers=_hdr("tok_alice")).json()
    rb = auth_client.get("/agent/sessions", headers=_hdr("tok_bob")).json()
    sids_a = {s["sid"] for s in ra["sessions"]}
    sids_b = {s["sid"] for s in rb["sessions"]}
    assert "mu_http_a" in sids_a and "mu_http_b" not in sids_a
    assert "mu_http_b" in sids_b and "mu_http_a" not in sids_b


def test_http_session_get_foreign_returns_404(auth_client, store):
    h = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nx\n--- USER MESSAGE END ---"}}]
    uid_alice = agent_auth.user_id_from_token("tok_alice")
    store.save_session("mu_get_a", h, "m", title="alice", owner_id=uid_alice)
    r_alice = auth_client.get("/agent/sessions/mu_get_a", headers=_hdr("tok_alice"))
    assert r_alice.status_code == 200
    r_bob = auth_client.get("/agent/sessions/mu_get_a", headers=_hdr("tok_bob"))
    assert r_bob.status_code == 404


def test_http_rename_foreign_returns_ok_false(auth_client, store):
    h = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nx\n--- USER MESSAGE END ---"}}]
    uid_alice = agent_auth.user_id_from_token("tok_alice")
    store.save_session("mu_rn_a", h, "m", title="orig", owner_id=uid_alice)
    r = auth_client.post(
        "/agent/sessions/mu_rn_a/rename",
        json={"title": "evil"},
        headers=_hdr("tok_bob"),
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False
    # title untouched
    sessions = store.list_sessions(limit=200, owner_id=uid_alice)
    assert next(s for s in sessions if s["sid"] == "mu_rn_a")["title"] == "orig"


def test_http_delete_foreign_blocked(auth_client, store):
    h = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nx\n--- USER MESSAGE END ---"}}]
    uid_alice = agent_auth.user_id_from_token("tok_alice")
    store.save_session("mu_del_http", h, "m", title="t", owner_id=uid_alice)
    auth_client.delete("/agent/sessions/mu_del_http", headers=_hdr("tok_bob"))
    # Either 403 or {ok:false} — both are acceptable; assert session not deleted.
    assert store.load_history("mu_del_http", owner_id=uid_alice) is not None
    r2 = auth_client.delete("/agent/sessions/mu_del_http", headers=_hdr("tok_alice"))
    assert r2.status_code == 200
    assert store.load_history("mu_del_http", owner_id=uid_alice) is None


def test_http_limits_per_user(auth_client, store):
    uid_alice = agent_auth.user_id_from_token("tok_alice")
    uid_bob = agent_auth.user_id_from_token("tok_bob")
    store.save_session("mu_lim_a", [], "m", title="A", owner_id=uid_alice)
    store.record_credits("mu_lim_a", 4.0, owner_id=uid_alice)
    r_a = auth_client.get("/agent/limits", headers=_hdr("tok_alice")).json()
    r_b = auth_client.get("/agent/limits", headers=_hdr("tok_bob")).json()
    assert r_a["user_id"] == uid_alice
    assert r_b["user_id"] == uid_bob
    assert r_a["day_credits"] >= 4.0
    # Bob hasn't spent anything in this fixture's user_credits namespace
    assert r_b["day_credits"] < r_a["day_credits"]


def test_http_agent_foreign_sid_forbidden(auth_client, store):
    """Caller can't piggyback on someone else's sid via /agent."""
    h = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nx\n--- USER MESSAGE END ---"}}]
    uid_alice = agent_auth.user_id_from_token("tok_alice")
    store.save_session("mu_fa_sid", h, "m", title="A", owner_id=uid_alice)
    r = auth_client.post(
        "/agent",
        json={"prompt": "hi", "session_id": "mu_fa_sid"},
        headers=_hdr("tok_bob"),
    )
    assert r.status_code == 403
