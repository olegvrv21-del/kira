"""Tests for llm/endpoints.py — per-model credential/endpoint routing."""
from __future__ import annotations

import pytest

from llm import endpoints


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("KIRA_ENDPOINTS", "KIRA_CLAUDE_KEY", "OPENROUTER_API_KEY",
              "OPENROUTER_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_no_config_returns_none():
    assert endpoints.resolve("gpt-5.4-mini") is None


def test_default_key_for_gpt(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "gpt-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://unity2.ai/v1")
    ep = endpoints.resolve("gpt-5.4-mini")
    assert ep.api_key == "gpt-key"
    assert ep.base_url == "https://unity2.ai/v1"


def test_claude_uses_dedicated_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "gpt-key")
    monkeypatch.setenv("KIRA_CLAUDE_KEY", "claude-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://unity2.ai/v1")
    ep = endpoints.resolve("claude-sonnet-4-6")
    assert ep.api_key == "claude-key"
    # gpt still uses the default key
    assert endpoints.resolve("gpt-5.4").api_key == "gpt-key"


def test_claude_falls_back_to_default_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "gpt-key")
    # no KIRA_CLAUDE_KEY set
    ep = endpoints.resolve("claude-opus-4-8")
    assert ep.api_key == "gpt-key"


def test_explicit_rules(monkeypatch):
    monkeypatch.setenv("KIRA_ENDPOINTS",
                       '[{"match":"^claude","base_url":"https://a/v1","key":"K1"},'
                       '{"match":".*","base_url":"https://b/v1","key":"K2"}]')
    c = endpoints.resolve("claude-haiku-4-5")
    assert c.api_key == "K1" and c.base_url == "https://a/v1"
    g = endpoints.resolve("gpt-5.4")
    assert g.api_key == "K2" and g.base_url == "https://b/v1"


def test_explicit_rules_key_env(monkeypatch):
    monkeypatch.setenv("MYKEY", "secret123")
    monkeypatch.setenv("KIRA_ENDPOINTS",
                       '[{"match":".*","base_url":"https://x/v1","key_env":"MYKEY"}]')
    ep = endpoints.resolve("anything")
    assert ep.api_key == "secret123"


def test_is_configured(monkeypatch):
    assert endpoints.is_configured() is False
    monkeypatch.setenv("KIRA_CLAUDE_KEY", "k")
    assert endpoints.is_configured() is True
