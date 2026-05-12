"""/usage endpoint goes through llm.get_provider().usage().

The route used to call Amazon Q directly with httpx; after Phase 3c.3 it
must be vendor-agnostic. These tests pin the new behaviour so a future
provider swap can't silently regress the contract.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("KIRA_AUTH_TOKEN", raising=False)
    import app as app_mod
    importlib.reload(app_mod)
    return TestClient(app_mod.app)


def _stub_provider(monkeypatch, usage_payload):
    """Replace llm.get_provider with a tiny stub returning ."""
    import llm

    class _Stub:
        name = "stub"
        supported_models = ["stub-1"]
        async def usage(self):
            if isinstance(usage_payload, Exception):
                raise usage_payload
            return usage_payload

    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: _Stub())


def test_usage_ok_passthrough(client, monkeypatch):
    _stub_provider(monkeypatch, {
        "supported": True, "status": "ok",
        "plan": "Pro", "used": 12.0, "limit": 800.0, "unit": "Credits",
    })
    r = client.get("/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "Pro"
    assert body["used"] == 12.0
    assert body["provider"] == "stub"


def test_usage_unsupported_returns_200(client, monkeypatch):
    _stub_provider(monkeypatch, {"supported": False, "provider": "mock"})
    r = client.get("/usage")
    assert r.status_code == 200
    assert r.json()["supported"] is False


def test_usage_no_key_returns_400(client, monkeypatch):
    _stub_provider(monkeypatch, {
        "supported": True, "status": "no_key", "error": "no Q API key available",
    })
    r = client.get("/usage")
    assert r.status_code == 400
    assert r.json()["status"] == "no_key"


def test_usage_http_error_propagates_status(client, monkeypatch):
    _stub_provider(monkeypatch, {
        "supported": True, "status": "http_error",
        "http_status": 502, "error": "upstream down",
    })
    r = client.get("/usage")
    assert r.status_code == 502


def test_usage_provider_exception_is_500(client, monkeypatch):
    _stub_provider(monkeypatch, RuntimeError("boom"))
    r = client.get("/usage")
    assert r.status_code == 500
    assert r.json()["error"] == "RuntimeError"


def test_usage_non_dict_is_500(client, monkeypatch):
    _stub_provider(monkeypatch, "not a dict")  # type: ignore[arg-type]
    r = client.get("/usage")
    assert r.status_code == 500


def test_mock_provider_usage_unsupported():
    """MockProvider.usage() returns the canonical unsupported sentinel."""
    import asyncio
    from llm import MockProvider
    out = asyncio.run(MockProvider().usage())
    assert out["supported"] is False
