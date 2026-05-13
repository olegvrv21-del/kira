"""Tests for agent_self introspection module + /agent/self endpoint + tool."""

from __future__ import annotations

import time

import agent_self
import agent_tools


def test_status_basic_shape():
    s = agent_self.status()
    assert s["name"] == "Kira"
    assert "git" in s and "head" in s["git"]
    assert isinstance(s["tools"], list) and len(s["tools"]) > 5
    assert "self_status" in s["tools"]
    assert "fs_read" in s["tools"]
    assert isinstance(s["in_flight"], dict)
    assert "count" in s["in_flight"]
    assert s["coverage"] is not None  # either {ok:True, ...} or {ok:False, error:...}


def test_status_with_uptime():
    t0 = time.time() - 42
    s = agent_self.status(start_ts=t0)
    assert s["uptime_seconds"] >= 41
    assert s["uptime_seconds"] <= 60


def test_status_text_includes_key_fields():
    txt = agent_self.status_text(start_ts=time.time() - 10)
    assert "Kira" in txt
    assert "git:" in txt
    assert "tests:" in txt
    assert "coverage:" in txt
    assert "in_flight:" in txt
    assert "uptime:" in txt


def test_status_no_secrets():
    """Make sure we never leak env-token-shaped strings."""
    txt = agent_self.status_text()
    assert "ktk_" not in txt
    assert "ksk_" not in txt
    assert "Bearer" not in txt


def test_tool_registered_in_host():
    assert "self_status" in agent_tools.TOOLS
    out = agent_tools.run_tool("self_status", {}, cwd=".")
    assert out[0] == "success"
    assert "Kira" in out[1]


def test_self_status_in_tool_specs():
    import json
    from pathlib import Path

    specs = json.loads(
        (Path(__file__).resolve().parent.parent / "agent_tool_specs.json").read_text()
    )
    names = [t["toolSpecification"]["name"] for t in specs]
    assert "self_status" in names


def test_endpoint_returns_snapshot():
    from fastapi.testclient import TestClient
    import app as kira_app

    client = TestClient(kira_app.app)
    r = client.get("/agent/self")
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "Kira"
    assert "head" in d["git"]
    assert "uptime_seconds" in d
