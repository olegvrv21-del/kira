"""Tests for agent_recall.py — automatic memory recall."""
from __future__ import annotations

import pytest

import agent_recall


class FakeMemory:
    def __init__(self, hits):
        self._hits = hits
        self.queries = []

    def search(self, query, k=5):
        self.queries.append((query, k))
        return self._hits[:k]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("KIRA_AUTO_RECALL", "KIRA_RECALL_K", "KIRA_RECALL_MIN_SCORE",
              "KIRA_RECALL_MAX_CHARS"):
        monkeypatch.delenv(k, raising=False)
    yield


def _hit(file, snippet, score=1.0, heading=""):
    return {"file": file, "snippet": snippet, "score": score, "heading": heading}


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("KIRA_AUTO_RECALL", "0")
    block, hits = agent_recall.recall("anything", memory=FakeMemory([_hit("a.md", "x")]))
    assert block is None and hits == []


def test_short_prompt_skipped():
    block, hits = agent_recall.recall("hi", memory=FakeMemory([_hit("a.md", "x")]))
    assert block is None


def test_recall_injects_snippets():
    mem = FakeMemory([
        _hit("MEMORY.md", "Kira runs on Unity2 via fallback chain.", 2.0, "LLM"),
        _hit("TODO.md", "Fix auth holes next.", 1.0),
    ])
    block, hits = agent_recall.recall("how does kira reach the LLM?", memory=mem)
    assert block is not None
    assert "auto-recalled" in block.lower()
    assert "Unity2" in block
    assert "MEMORY.md" in block
    assert len(hits) == 2


def test_min_score_filters(monkeypatch):
    monkeypatch.setenv("KIRA_RECALL_MIN_SCORE", "1.5")
    mem = FakeMemory([_hit("a.md", "high", 2.0), _hit("b.md", "low", 0.5)])
    block, hits = agent_recall.recall("query here", memory=mem)
    assert len(hits) == 1
    assert hits[0]["file"] == "a.md"


def test_no_hits_returns_none():
    block, hits = agent_recall.recall("query here", memory=FakeMemory([]))
    assert block is None and hits == []


def test_k_limit(monkeypatch):
    monkeypatch.setenv("KIRA_RECALL_K", "2")
    mem = FakeMemory([_hit(f"f{i}.md", f"snippet {i}", 3.0 - i * 0.1) for i in range(5)])
    block, hits = agent_recall.recall("query here", memory=mem)
    assert len(hits) <= 2
    assert mem.queries[0][1] == 2  # k passed through


def test_max_chars_cap(monkeypatch):
    monkeypatch.setenv("KIRA_RECALL_MAX_CHARS", "200")
    big = "x" * 500
    mem = FakeMemory([_hit("a.md", big, 2.0), _hit("b.md", big, 1.5)])
    block, hits = agent_recall.recall("query here", memory=mem)
    assert block is not None
    assert len(block) < 700  # header + one trimmed entry, not both full


def test_memory_error_is_fail_open():
    class Boom:
        def search(self, q, k=5):
            raise RuntimeError("index down")
    block, hits = agent_recall.recall("query here", memory=Boom())
    assert block is None and hits == []


def test_empty_snippets_skipped():
    mem = FakeMemory([_hit("a.md", "   ", 2.0), _hit("b.md", "real content", 1.5)])
    block, hits = agent_recall.recall("query here", memory=mem)
    assert block is not None
    assert "real content" in block
    assert len(hits) == 1
