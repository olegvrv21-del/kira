import importlib
from pathlib import Path

import pytest


@pytest.fixture
def mem(tmp_path, monkeypatch):
    nb = tmp_path / "notebook"
    nb.mkdir()
    (nb / "STATUS.md").write_text(
        "# Status\n\nWebchat service runs on port 3000.\n\n"
        "## Backups\nDaily systemd timer at 04:17 UTC. Keeps 14 days.\n")
    (nb / "WEBCHAT.md").write_text(
        "# Webchat\n\n## Sandbox\nDocker image kira-sandbox:latest.\n\n"
        "## Notes\nPyright runs on port 9001 inside container.\n")
    (nb / "SECRETS.md").write_text("# Secrets\nksk_token=zzzz\n")
    monkeypatch.setenv("KIRA_NOTEBOOK_DIR", str(nb))
    monkeypatch.setenv("KIRA_MEMORY_EXCLUDE", "SECRETS*.md")
    import agent_memory
    importlib.reload(agent_memory)
    return agent_memory.memory


def test_index_excludes_secrets(mem):
    s = mem.status()
    assert "STATUS.md" in s["files"]
    assert "SECRETS.md" not in s["files"]
    assert "SECRETS.md" in s["excluded"]
    assert s["chunks"] > 0


def test_search_finds_relevant(mem):
    hits = mem.search("backup retention systemd", k=3)
    assert hits
    # backups chunk should rank highest
    assert "backup" in hits[0]["snippet"].lower() or "14 days" in hits[0]["snippet"]
    assert hits[0]["file"] == "STATUS.md"


def test_search_no_secret_leak(mem):
    hits = mem.search("ksk_token", k=5)
    for h in hits:
        assert "ksk_token" not in h["snippet"]
        assert h["file"] != "SECRETS.md"


def test_add_appends_and_rebuilds(mem, tmp_path):
    before = mem.status()["chunks"]
    info = mem.add("Discovered that pyright caps at 6s for first indexing.",
                    file="MEMORY.md")
    assert info["file"] == "MEMORY.md"
    assert (Path(mem.status()["root"]) / "MEMORY.md").exists()
    after = mem.status()["chunks"]
    assert after >= before + 1
    hits = mem.search("pyright indexing 6 seconds", k=3)
    assert any("pyright" in h["snippet"].lower() for h in hits)


def test_add_rejects_path_traversal(mem):
    with pytest.raises(ValueError):
        mem.add("hi", file="../etc/x.md")
    with pytest.raises(ValueError):
        mem.add("hi", file=".hidden.md")
    with pytest.raises(ValueError):
        mem.add("")


def test_rebuild_on_mtime_change(mem, tmp_path):
    root = Path(mem.status()["root"])
    chunks_before = mem.status()["chunks"]
    import time; time.sleep(0.05)
    (root / "NEW.md").write_text("# New\n\nThis is a brand new doc about widgets.\n")
    s = mem.status()  # ensure() rebuilds
    assert s["chunks"] > chunks_before
    assert any("widgets" in h["snippet"].lower() for h in mem.search("widgets"))


def test_chunks_have_line_ranges(mem):
    s = mem.status()
    for c in mem.chunks:
        assert c["start_line"] >= 1
        assert c["end_line"] >= c["start_line"]
        assert c["file"]
        assert c["text"]
