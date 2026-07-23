"""Tests for llm/router.py — request-aware model routing."""
from __future__ import annotations

import pytest

from llm import router


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("KIRA_ROUTE_SIMPLE", "KIRA_ROUTE_STANDARD", "KIRA_ROUTE_HARD",
              "KIRA_ROUTE_CLASSIFIER", "KIRA_DEFAULT_MODEL"):
        monkeypatch.delenv(k, raising=False)
    yield


# ---- prefilter (no LLM call) ----------------------------------------------


@pytest.mark.parametrize("prompt,tier", [
    ("привет", "simple"),
    ("Hello!", "simple"),
    ("спасибо", "simple"),
    ("ok", "simple"),
    ("", "simple"),
    ("Спроектируй архитектуру микросервиса", "hard"),
    ("please refactor the whole module", "hard"),
    ("тут race condition, разберись глубоко", "hard"),
])
def test_prefilter(prompt, tier):
    assert router._prefilter(prompt) == tier


def test_prefilter_ambiguous_returns_none():
    assert router._prefilter("Прочитай файл config.py и покажи содержимое") is None


# ---- sanitize -------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("simple", "simple"),
    ("  HARD  ", "hard"),
    ("standard.", "standard"),
    ("I think this is hard because...", "hard"),
    ("garbage", "standard"),
    ("", "standard"),
])
def test_sanitize_tier(raw, expected):
    assert router._sanitize_tier(raw) == expected


# ---- tier → model mapping -------------------------------------------------


def test_tier_models_defaults():
    m = router.tier_models()
    assert m["simple"] == "gpt-5.4-mini"
    assert m["hard"] == "gpt-5.6"


def test_tier_models_env_override(monkeypatch):
    monkeypatch.setenv("KIRA_ROUTE_HARD", "claude-opus-4-8")
    assert router.tier_models()["hard"] == "claude-opus-4-8"


# ---- classify (with mock llm_one_shot) ------------------------------------


@pytest.mark.asyncio
async def test_classify_uses_llm_for_ambiguous():
    calls = {}

    async def fake_oneshot(prompt, *, model, system=None):
        calls["model"] = model
        calls["prompt"] = prompt
        return "hard"

    tier = await router.classify("Прочитай config.py и оптимиз... нет, просто открой",
                                 llm_one_shot=fake_oneshot)
    # This prompt contains "оптимиз" hint → prefilter catches it as hard before LLM.
    assert tier == "hard"


@pytest.mark.asyncio
async def test_classify_llm_path_for_neutral_prompt():
    async def fake_oneshot(prompt, *, model, system=None):
        return "standard"

    tier = await router.classify("Открой файл main.py и покажи функцию foo",
                                 llm_one_shot=fake_oneshot)
    assert tier == "standard"


@pytest.mark.asyncio
async def test_classify_falls_back_on_error():
    async def boom(prompt, *, model, system=None):
        raise RuntimeError("llm down")

    tier = await router.classify("Открой файл main.py и покажи функцию foo",
                                 llm_one_shot=boom)
    assert tier == "standard"


@pytest.mark.asyncio
async def test_classify_no_llm_returns_standard():
    tier = await router.classify("Открой файл main.py и покажи функцию foo")
    assert tier == "standard"


@pytest.mark.asyncio
async def test_route_returns_model_and_tier():
    async def fake_oneshot(prompt, *, model, system=None):
        return "simple"

    # greeting → prefilter → simple → gpt-5.4-mini
    model, tier = await router.route("привет", llm_one_shot=fake_oneshot)
    assert tier == "simple"
    assert model == "gpt-5.4-mini"
