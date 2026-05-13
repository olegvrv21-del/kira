"""Unit tests for agent_self_improve (propose-only self-improvement)."""
import os
import tempfile
from pathlib import Path

import pytest

import agent_self_improve as si


def test_score_extract_pure_json():
    out = si._score_re_extract('{"helpful": 8, "correct": 7, "concise": 6, "safe": 9, "critique": "x"}')
    assert out is not None
    assert out["helpful"] == 8


def test_score_extract_with_prose():
    out = si._score_re_extract("Here you go:\n{\"helpful\": 5, \"correct\": 5, \"concise\": 5, \"safe\": 5}\nthanks")
    assert out is not None
    assert out["helpful"] == 5


def test_score_extract_garbage_returns_none():
    assert si._score_re_extract("no json here") is None
    assert si._score_re_extract("") is None


def test_coerce_score_dict_clamps_and_defaults():
    out = si._coerce_score_dict({"helpful": 15, "correct": -3, "concise": "x", "safe": None, "critique": "abc"})
    assert out["helpful"] == 10
    assert out["correct"] == 0
    assert out["concise"] == 0
    assert out["safe"] == 0
    assert out["critique"] == "abc"
    assert "overall" in out


def test_coerce_handles_none():
    out = si._coerce_score_dict(None)
    assert out["helpful"] == 0
    assert out["overall"] == 0.0


def test_safe_slug_strips_unsafe():
    assert si._safe_slug("Hello World! \u041f\u0440\u0438\u0432\u0435\u0442") in ("hello-world", "hello-world-")
    assert si._safe_slug("") == "proposal"
    assert len(si._safe_slug("a" * 200)) <= 40


def test_save_proposal_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KIRA_NOTEBOOK_DIR", str(tmp_path))
    rel = si.save_proposal("# Test\n\nbody", slug="my-test")
    assert "proposals/" in rel
    full = tmp_path / rel
    assert full.exists()
    assert "# Test" in full.read_text()


def test_save_proposal_rejects_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("KIRA_NOTEBOOK_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        si.save_proposal("")
    with pytest.raises(ValueError):
        si.save_proposal("   \n   ")


def test_list_proposals_returns_recent_first(tmp_path, monkeypatch):
    monkeypatch.setenv("KIRA_NOTEBOOK_DIR", str(tmp_path))
    si.save_proposal("# First", slug="a")
    import time
    time.sleep(1.05)  # ensure distinct mtime in seconds-resolution glob
    si.save_proposal("# Second", slug="b")
    items = si.list_proposals()
    assert len(items) == 2
    assert items[0]["title"] in ("First", "Second")
    titles = [i["title"] for i in items]
    assert "First" in titles and "Second" in titles


@pytest.mark.asyncio
async def test_score_answer_empty_assistant_short_circuits(monkeypatch):
    # Should not call LLM if assistant_msg is blank.
    called = []
    async def fake_stream(*args, **kw):
        called.append(args)
        return ""
    monkeypatch.setattr(si, "_stream_text", fake_stream)
    out = await si.score_answer("key", "hi", "")
    assert called == []
    assert out["critique"] == "empty answer"
    assert out["overall"] == 0.0


@pytest.mark.asyncio
async def test_score_answer_parses_llm_output(monkeypatch):
    async def fake_stream(messages, model, timeout):
        return '{"helpful": 8, "correct": 9, "concise": 7, "safe": 10, "critique": "good"}'
    monkeypatch.setattr(si, "_stream_text", fake_stream)
    out = await si.score_answer("key", "q", "a")
    assert out["helpful"] == 8
    assert out["overall"] == round((8 + 9 + 7 + 10) / 4.0, 2)


@pytest.mark.asyncio
async def test_score_answer_handles_provider_error(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("network")
    monkeypatch.setattr(si, "_stream_text", boom)
    out = await si.score_answer("key", "q", "a")
    assert "scorer-error" in out["critique"]
    assert out["overall"] == 0.0


@pytest.mark.asyncio
async def test_propose_revision_empty_samples():
    out = await si.propose_revision("k", [], "prompt")
    assert out["markdown"] == ""


@pytest.mark.asyncio
async def test_propose_revision_calls_llm(monkeypatch):
    captured = {}
    async def fake_stream(messages, model, timeout):
        captured["messages"] = messages
        return "# Proposal\n\nbody"
    monkeypatch.setattr(si, "_stream_text", fake_stream)
    out = await si.propose_revision(
        "key",
        [{"user": "u", "assistant": "a", "score": {"overall": 3.0, "critique": "bad"}}],
        "current prompt",
    )
    assert out["markdown"].startswith("# Proposal")
    # Verify samples and current prompt were both passed.
    user_text = captured["messages"][-1].content
    assert "current prompt" in user_text
    assert "bad" in user_text
