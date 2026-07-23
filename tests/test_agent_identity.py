"""Tests for agent_identity.py — live self-awareness block."""
from __future__ import annotations

import pytest

import agent_identity


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("KIRA_LLM_PROVIDER", "OPENROUTER_BASE_URL", "KIRA_DEFAULT_MODEL",
              "KIRA_CLAUDE_KEY", "KIRA_FRUGAL", "KIRA_AUTO_RECALL",
              "KIRA_CRITIC_AUTO", "KIRA_LLM_CHAIN", "KIRA_EXPENSIVE_DAILY_CAP"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_render_reports_active_model(monkeypatch):
    block = agent_identity.render("gpt-5.4-mini")
    assert "gpt-5.4-mini" in block
    assert "Кира" in block


def test_render_falls_back_to_default_model(monkeypatch):
    monkeypatch.setenv("KIRA_DEFAULT_MODEL", "gpt-5.4")
    block = agent_identity.render()
    assert "gpt-5.4" in block


def test_render_forbids_chatgpt_claim():
    block = agent_identity.render("gpt-5.4-mini")
    # The block must explicitly instruct NOT to claim being ChatGPT.
    assert "ChatGPT" in block  # mentioned in the negative instruction
    assert "НЕ говори" in block or "не говори" in block.lower()


def test_provider_unity2(monkeypatch):
    monkeypatch.setenv("KIRA_LLM_PROVIDER", "fallback")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://unity2.ai/v1")
    block = agent_identity.render("gpt-5.4-mini")
    assert "Unity2" in block


def test_capabilities_reflect_env(monkeypatch):
    monkeypatch.setenv("KIRA_CRITIC_AUTO", "1")
    monkeypatch.setenv("KIRA_AUTO_RECALL", "1")
    monkeypatch.setenv("KIRA_CLAUDE_KEY", "sk-x")
    caps = " ".join(agent_identity.capabilities())
    assert "Критик" in caps
    assert "Авто-память" in caps
    assert "Claude" in caps


def test_capabilities_omit_disabled(monkeypatch):
    monkeypatch.setenv("KIRA_CRITIC_AUTO", "0")
    monkeypatch.setenv("KIRA_AUTO_RECALL", "0")
    caps = " ".join(agent_identity.capabilities())
    assert "Критик" not in caps
    assert "Авто-память" not in caps


def test_build_system_prompt_includes_identity(monkeypatch):
    import agent_runtime as ar
    monkeypatch.setenv("KIRA_DEFAULT_MODEL", "gpt-5.4-mini")
    sp = ar._build_system_prompt("gpt-5.4-mini")
    assert "Кира" in sp
    assert "gpt-5.4-mini" in sp
    assert "self_status" in sp  # still references the tool for deep detail
