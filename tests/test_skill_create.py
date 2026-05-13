"""Skill creation: agent_skills.create_skill + POST /skills."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tmp_skills(tmp_path, monkeypatch):
    import agent_skills
    monkeypatch.setattr(agent_skills, "SKILLS_DIR", tmp_path)
    yield tmp_path


def test_create_validates_name(tmp_skills):
    import agent_skills
    assert agent_skills.create_skill("", "d", "b")["ok"] is False
    assert agent_skills.create_skill("Bad Name", "d", "b")["ok"] is False
    # UPPER is auto-lowercased, so this becomes valid "upper". Expect ok=True.
    assert agent_skills.create_skill("UPPER", "Use when X", "body")["ok"] is True
    assert agent_skills.create_skill("9-leading-digit", "d", "b")["ok"] is False


def test_create_validates_description_and_body(tmp_skills):
    import agent_skills
    assert agent_skills.create_skill("ok-name", "", "b")["ok"] is False
    assert agent_skills.create_skill("ok-name", "d", "")["ok"] is False
    assert agent_skills.create_skill("ok-name", "d", "   \n  ")["ok"] is False


def test_create_writes_file_with_frontmatter(tmp_skills):
    import agent_skills
    r = agent_skills.create_skill("hello-world", "Use when greeting", "## Body\nhi")
    assert r["ok"] is True
    f = tmp_skills / "hello-world.md"
    assert f.exists()
    text = f.read_text(encoding="utf-8")
    assert text.startswith("---\nname: hello-world\ndescription: Use when greeting\n---\n")
    assert "## Body" in text


def test_create_refuses_duplicate(tmp_skills):
    import agent_skills
    agent_skills.create_skill("dup-test", "Use when X", "body")
    r2 = agent_skills.create_skill("dup-test", "Use when Y", "body2")
    assert r2["ok"] is False
    assert "exists" in r2["error"].lower()


def test_create_normalises_multiline_description(tmp_skills):
    import agent_skills
    r = agent_skills.create_skill("multi-desc", "line one\nline two\n  spaces", "b")
    assert r["ok"] is True
    text = (tmp_skills / "multi-desc.md").read_text(encoding="utf-8")
    assert "description: line one line two spaces" in text


def test_post_skills_endpoint_success(tmp_skills, monkeypatch):
    monkeypatch.delenv("KIRA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("KIRA_MASTER_TOKEN", raising=False)
    import app as _app
    importlib.reload(_app)
    client = TestClient(_app.app)
    r = client.post("/skills", json={
        "name": "api-skill",
        "description": "Use when testing the POST endpoint",
        "body": "## Steps\n1. call API",
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_post_skills_endpoint_validation_error(tmp_skills, monkeypatch):
    monkeypatch.delenv("KIRA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("KIRA_MASTER_TOKEN", raising=False)
    import app as _app
    importlib.reload(_app)
    client = TestClient(_app.app)
    r = client.post("/skills", json={"name": "X", "description": "d", "body": "b"})
    assert r.status_code == 400, r.text
    assert r.json()["ok"] is False
