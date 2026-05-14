"""Tests for skill frontmatter allowed-tools (PR #22)."""

import pytest


@pytest.fixture
def tmp_skills(tmp_path, monkeypatch):
    import agent_skills
    monkeypatch.setattr(agent_skills, "SKILLS_DIR", tmp_path)
    yield tmp_path


def test_parse_inline_list():
    import agent_skills
    meta, body = agent_skills._parse(
        "---\nname: foo\ndescription: bar\nallowed-tools: [fs_read, grep, execute_bash]\n---\nBODY\n"
    )
    assert meta["name"] == "foo"
    assert meta["allowed-tools"] == ["fs_read", "grep", "execute_bash"]
    assert body.strip() == "BODY"


def test_parse_empty_inline_list():
    import agent_skills
    meta, _ = agent_skills._parse("---\nname: x\nallowed-tools: []\n---\nb\n")
    assert meta["allowed-tools"] == []


def test_parse_block_list():
    import agent_skills
    text = "---\nname: foo\ndescription: bar\nallowed-tools:\n  - fs_read\n  - grep\n---\nBODY\n"
    meta, _ = agent_skills._parse(text)
    assert meta["allowed-tools"] == ["fs_read", "grep"]


def test_parse_no_allowed_tools():
    import agent_skills
    meta, _ = agent_skills._parse("---\nname: foo\ndescription: bar\n---\nBODY\n")
    assert "allowed-tools" not in meta


def test_list_skills_exposes_allowed_tools(tmp_skills):
    import agent_skills
    (tmp_skills / "a.md").write_text(
        "---\nname: a\ndescription: d\nallowed-tools: [fs_read, grep]\n---\nbody\n"
    )
    (tmp_skills / "b.md").write_text("---\nname: b\ndescription: d\n---\nbody\n")
    items = {s["name"]: s for s in agent_skills.list_skills()}
    assert items["a"]["allowed_tools"] == ["fs_read", "grep"]
    assert "allowed_tools" not in items["b"]


def test_create_with_allowed_tools(tmp_skills):
    import agent_skills
    r = agent_skills.create_skill(
        "limited", "Use when X", "body content here", allowed_tools=["fs_read", "grep"]
    )
    assert r["ok"] is True
    text = (tmp_skills / "limited.md").read_text()
    assert "allowed-tools: [fs_read, grep]" in text
    # Round-trip: parser reads it back.
    items = {s["name"]: s for s in agent_skills.list_skills()}
    assert items["limited"]["allowed_tools"] == ["fs_read", "grep"]


def test_create_rejects_bad_tool_name(tmp_skills):
    import agent_skills
    r = agent_skills.create_skill("xx", "d", "body content", allowed_tools=["fs read"])
    assert r["ok"] is False
    assert "allowed_tools" in r["error"]


def test_create_rejects_non_list_allowed_tools(tmp_skills):
    import agent_skills
    r = agent_skills.create_skill("xx", "d", "body", allowed_tools="fs_read")  # type: ignore[arg-type]
    assert r["ok"] is False


def test_create_empty_allowed_tools_omits_line(tmp_skills):
    import agent_skills
    r = agent_skills.create_skill("yy", "d", "body content", allowed_tools=[])
    assert r["ok"] is True
    text = (tmp_skills / "yy.md").read_text()
    assert "allowed-tools" not in text


def test_render_skills_section_includes_allowed_tools(tmp_skills):
    import agent_skills
    (tmp_skills / "lim.md").write_text(
        "---\nname: lim\ndescription: d\nallowed-tools: [fs_read]\n---\nb\n"
    )
    (tmp_skills / "any.md").write_text("---\nname: any\ndescription: d\n---\nb\n")
    out = agent_skills.render_skills_section()
    assert "<allowed-tools>fs_read</allowed-tools>" in out
    # `any` block has no allowed-tools.
    assert out.count("<allowed-tools>") == 1


def test_load_skill_tool_appends_policy_footer(tmp_skills):
    import agent_tools
    (tmp_skills / "lim.md").write_text(
        "---\nname: lim\ndescription: d\nallowed-tools: [fs_read, grep]\n---\nBODY\n"
    )
    out = agent_tools.load_skill_tool({"name": "lim"}, cwd=str(tmp_skills))
    assert "BODY" in out
    assert "Tool policy" in out
    assert "fs_read" in out and "grep" in out


def test_load_skill_tool_no_footer_when_no_policy(tmp_skills):
    import agent_tools
    (tmp_skills / "free.md").write_text("---\nname: free\ndescription: d\n---\nBODY\n")
    out = agent_tools.load_skill_tool({"name": "free"}, cwd=str(tmp_skills))
    assert "Tool policy" not in out


def test_sandbox_load_skill_tool_appends_policy_footer(tmp_skills):
    import sandbox_tools
    (tmp_skills / "lim2.md").write_text(
        "---\nname: lim2\ndescription: d\nallowed-tools: [fs_read]\n---\nBODY\n"
    )
    out = sandbox_tools.load_skill_tool({"name": "lim2"}, cwd=str(tmp_skills), sid="s")
    assert "Tool policy" in out
    assert "fs_read" in out


def test_existing_skills_still_load(tmp_skills):
    """Backwards compat: skills without allowed-tools still load."""
    import agent_skills
    (tmp_skills / "old.md").write_text("---\nname: old\ndescription: d\n---\nthe body\n")
    body = agent_skills.load_skill("old")
    assert body == "the body"
