"""Embeddings-based semantic search on top of BM25.

Guarded by KIRA_MEMORY_EMBED=1; gracefully degrades when sentence-transformers
is not installed.
"""

import pytest

import agent_memory

# Skip the whole module if sentence-transformers isn't available locally.
pytest.importorskip("sentence_transformers")


@pytest.fixture
def notebook(tmp_path, monkeypatch):
    nb = tmp_path / "nb"
    nb.mkdir()
    # Three docs with no shared keywords for the test query.
    (nb / "LSP.md").write_text(
        "# Pyright server\n\nThe Pyright language server hangs when capabilities include workspaceFolders.\n"
    )
    (nb / "DEPLOY.md").write_text("# Deployment\n\nHow to ship the FastAPI service via systemd.\n")
    (nb / "OTHER.md").write_text("# Misc\n\nA recipe for tomato soup. Add basil at the end.\n")
    monkeypatch.setenv("KIRA_NOTEBOOK_DIR", str(nb))
    monkeypatch.setenv("KIRA_MEMORY_EMBED", "1")
    # Reset the global model so it reloads with the test env.
    agent_memory._embed_model = None
    agent_memory._embed_load_error = None
    return nb


def test_status_reports_embeddings_enabled(notebook):
    idx = agent_memory.MemoryIndex()
    idx.rebuild()
    s = idx.status()
    assert s["embeddings"]["enabled"] is True
    assert s["embeddings"]["dim"] in (384, 768)  # MiniLM=384, others=768
    assert s["embeddings"]["model"]


def test_semantic_search_finds_synonym(notebook):
    """Query 'language server stuck' must rank LSP doc first even though
    none of those tokens appear in it."""
    idx = agent_memory.MemoryIndex()
    idx.rebuild()
    hits = idx.search("language server stuck", k=3)
    assert hits, "no hits returned"
    assert "LSP.md" in hits[0]["file"], f"top hit should be LSP, got {hits[0]['file']}"


def test_semantic_search_blends_with_bm25(notebook):
    """Exact keyword match should still surface despite embeddings."""
    idx = agent_memory.MemoryIndex()
    idx.rebuild()
    hits = idx.search("tomato basil", k=2)
    assert hits
    assert hits[0]["file"].endswith("OTHER.md")


def test_search_empty_query_returns_something_or_nothing(notebook):
    """Empty query is degenerate; with embeddings on we get whatever cosine
    similarity to '' returns (usually all docs). Must not crash."""
    idx = agent_memory.MemoryIndex()
    idx.rebuild()
    out = idx.search("", k=3)
    assert isinstance(out, list)


def test_disabled_when_env_unset(tmp_path, monkeypatch):
    nb = tmp_path / "nb2"
    nb.mkdir()
    (nb / "DOC.md").write_text("# Title\n\ncontent goes here\n")
    monkeypatch.setenv("KIRA_NOTEBOOK_DIR", str(nb))
    monkeypatch.delenv("KIRA_MEMORY_EMBED", raising=False)
    agent_memory._embed_model = None
    agent_memory._embed_load_error = None
    idx = agent_memory.MemoryIndex()
    idx.rebuild()
    s = idx.status()
    assert s["embeddings"]["enabled"] is False
    assert "set KIRA_MEMORY_EMBED=1" in s["embeddings"]["reason"]


def test_blend_weight_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KIRA_EMBED_BLEND", "0.9")
    import importlib

    # The blend weight is read at module-load time; reimport to pick it up.
    importlib.reload(agent_memory)
    assert agent_memory._EMBED_BLEND == 0.9
    # restore default
    monkeypatch.setenv("KIRA_EMBED_BLEND", "0.7")
    importlib.reload(agent_memory)
