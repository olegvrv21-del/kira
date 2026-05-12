"""Cover agent_skills branches."""

import importlib
from pathlib import Path

import pytest

import agent_skills


def _reload(monkeypatch, dirpath: Path):
    monkeypatch.setattr(agent_skills, "SKILLS_DIR", dirpath)
    return agent_skills


def test_parse_no_frontmatter():
    meta, body = agent_skills._parse("just text, no front matter")
    assert meta == {} and body == "just text, no front matter"


def test_parse_with_frontmatter_and_bad_line():
    text = "---\nname: foo\ndescription: bar baz\nno-colon-line\n---\nbody here\n"
    meta, body = agent_skills._parse(text)
    assert meta["name"] == "foo" and meta["description"] == "bar baz"
    # no-colon-line should be skipped silently
    assert body.strip() == "body here"


def test_list_skills_missing_dir(monkeypatch, tmp_path):
    s = _reload(monkeypatch, tmp_path / "nope")
    assert s.list_skills() == []


def test_list_skills_with_files(monkeypatch, tmp_path):
    s = _reload(monkeypatch, tmp_path)
    (tmp_path / "a.md").write_text("---\nname: alpha\ndescription: desc-a\n---\nA body")
    (tmp_path / "b.md").write_text("no frontmatter body")  # name falls back to stem, desc empty
    out = s.list_skills()
    names = {x["name"] for x in out}
    assert "alpha" in names and "b" in names
    b = next(x for x in out if x["name"] == "b")
    assert b["description"] == ""


def test_list_skills_swallows_read_errors(monkeypatch, tmp_path):
    s = _reload(monkeypatch, tmp_path)
    bad = tmp_path / "bad.md"
    bad.write_text("---\nname: x\n---\nok")

    real_read_text = Path.read_text

    def boom(self, *a, **kw):
        if self.name == "bad.md":
            raise OSError("perm")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", boom)
    out = s.list_skills()
    assert all(x["file"] != "bad.md" for x in out)


def test_load_skill_missing_dir(monkeypatch, tmp_path):
    s = _reload(monkeypatch, tmp_path / "nope")
    assert s.load_skill("x") is None


def test_load_skill_by_stem(monkeypatch, tmp_path):
    s = _reload(monkeypatch, tmp_path)
    (tmp_path / "my.md").write_text("---\nname: not-my\n---\nstem-body")
    assert s.load_skill("my") == "stem-body"


def test_load_skill_by_name_field(monkeypatch, tmp_path):
    s = _reload(monkeypatch, tmp_path)
    (tmp_path / "file1.md").write_text("---\nname: real-name\n---\nthe-body")
    assert s.load_skill("real-name") == "the-body"


def test_load_skill_not_found(monkeypatch, tmp_path):
    s = _reload(monkeypatch, tmp_path)
    (tmp_path / "file1.md").write_text("---\nname: other\n---\nbody")
    assert s.load_skill("nope") is None


def test_load_skill_swallows_parse_errors_on_name_lookup(monkeypatch, tmp_path):
    s = _reload(monkeypatch, tmp_path)
    (tmp_path / "bad.md").write_text("x")
    (tmp_path / "good.md").write_text("---\nname: target\n---\nB")

    real_read_text = Path.read_text
    calls = {"n": 0}

    def maybe_boom(self, *a, **kw):
        if self.name == "bad.md":
            calls["n"] += 1
            raise OSError("x")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", maybe_boom)
    assert s.load_skill("target") == "B"


def test_render_skills_section_empty(monkeypatch, tmp_path):
    s = _reload(monkeypatch, tmp_path)
    assert s.render_skills_section() == ""


def test_render_skills_section_with_skills(monkeypatch, tmp_path):
    s = _reload(monkeypatch, tmp_path)
    (tmp_path / "a.md").write_text("---\nname: alpha\ndescription: D\n---\nbody")
    out = s.render_skills_section()
    assert "<available_skills>" in out and "alpha" in out and 'load_skill name="alpha"' in out
