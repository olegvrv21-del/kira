"""Kill-switch: freeze flag must block /agent + /chat, master-token gates set/unset."""

import importlib
import json
import os

import pytest
from fastapi.testclient import TestClient

import agent_freeze


@pytest.fixture
def freeze_flag(tmp_path, monkeypatch):
    flag = tmp_path / ".frozen"
    monkeypatch.setattr(agent_freeze, "_FREEZE_FLAG", flag)
    yield flag
    if flag.exists():
        flag.unlink()


def test_freeze_unfreeze_roundtrip(freeze_flag):
    assert agent_freeze.is_frozen() is False
    out = agent_freeze.freeze("manual test")
    assert out["frozen"] is True
    assert agent_freeze.is_frozen() is True
    info = agent_freeze.freeze_info()
    assert info["frozen"] is True
    assert info["reason"] == "manual test"
    res = agent_freeze.unfreeze()
    assert res["frozen"] is False
    assert agent_freeze.is_frozen() is False


def test_unfreeze_when_not_frozen_is_noop(freeze_flag):
    res = agent_freeze.unfreeze()
    assert res["frozen"] is False


def test_master_token_check(monkeypatch):
    monkeypatch.setenv("KIRA_MASTER_TOKEN", "master-x")
    monkeypatch.delenv("KIRA_AUTH_TOKEN", raising=False)
    assert agent_freeze.is_master_token("master-x") is True
    assert agent_freeze.is_master_token("wrong") is False
    assert agent_freeze.is_master_token("") is False
    assert agent_freeze.is_master_token(None) is False


def test_master_token_falls_back_to_first_auth_token(monkeypatch):
    monkeypatch.delenv("KIRA_MASTER_TOKEN", raising=False)
    monkeypatch.setenv("KIRA_AUTH_TOKEN", "primary,secondary")
    assert agent_freeze.is_master_token("primary") is True
    # Secondary tokens must NOT unlock master endpoints — even if they auth /agent.
    assert agent_freeze.is_master_token("secondary") is False


def test_master_token_disabled_when_no_env(monkeypatch):
    monkeypatch.delenv("KIRA_MASTER_TOKEN", raising=False)
    monkeypatch.delenv("KIRA_AUTH_TOKEN", raising=False)
    # No master configured -> all checks fail closed.
    assert agent_freeze.is_master_token("anything") is False


@pytest.mark.asyncio
async def test_run_agent_short_circuits_when_frozen(monkeypatch, tmp_path):
    """run_agent must abort at turn-0 with an error SSE when frozen."""
    import agent_runtime as ar
    flag = tmp_path / ".frozen"
    monkeypatch.setattr(agent_freeze, "_FREEZE_FLAG", flag)
    monkeypatch.setattr(ar, "WORKSPACES", tmp_path)
    agent_freeze.freeze("unit test")
    events = []
    async for raw in ar.run_agent("k", "hi", session_id="freeze_unit"):
        # SSE bytes -> parse the 'data: {...}' line.
        line = raw.decode().strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    types = [e.get("type") for e in events]
    assert types == ["meta", "error", "done"], events
    assert events[1].get("code") == "frozen"
    agent_freeze.unfreeze()


def test_freeze_endpoints_require_master_token(monkeypatch, freeze_flag):
    """Smoke: POST /agent/freeze without master token must 403."""
    monkeypatch.setenv("KIRA_MASTER_TOKEN", "master-only")
    # Reload app so middleware picks up env (TestClient builds it on import).
    import app as _app
    importlib.reload(_app)
    client = TestClient(_app.app)
    # No bearer at all:
    r = client.post("/agent/freeze", json={"reason": "x"})
    assert r.status_code in (403, 401), r.text
    # Wrong bearer:
    r = client.post("/agent/freeze", json={"reason": "x"}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code in (403, 401), r.text
    # Right bearer:
    r = client.post("/agent/freeze", json={"reason": "x"}, headers={"Authorization": "Bearer master-only"})
    assert r.status_code == 200, r.text
    assert r.json().get("frozen") is True
    # Cleanup
    r = client.post("/agent/unfreeze", headers={"Authorization": "Bearer master-only"})
    assert r.status_code == 200
