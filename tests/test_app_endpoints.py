"""More /app endpoint coverage: agent sessions, plan, actions, upload, file,
restart admin, /usage error path, stream_q early exit, lifespan."""

import asyncio
import io
import json
import os
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

import app as app_mod
import agent_store


@pytest.fixture
def client():
    return TestClient(app_mod.app)


# ---------- /healthz, /models ----------


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_models_lists(client):
    r = client.get("/models")
    assert r.status_code == 200
    d = r.json()
    assert "models" in d and "default" in d


# ---------- /admin/restart success path (don't really restart!) ----------


def test_admin_restart_success(client, monkeypatch):
    called = {"n": 0}

    class FakePopen:
        def __init__(self, *a, **kw):
            called["n"] += 1

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    # avoid actual sleep delay
    real_sleep = asyncio.sleep

    async def fast_sleep(_):
        await real_sleep(0)

    monkeypatch.setattr("asyncio.sleep", fast_sleep)
    tok = app_mod._RESTART_TOKEN
    r = client.post(f"/admin/restart?token={tok}")
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") is True and d.get("scheduled") is True


# ---------- /agent endpoint pre-flight cost cap ----------


def test_agent_endpoint_preflight_session_limit(client, monkeypatch):
    sid = "presid" + uuid.uuid4().hex[:6]
    # force tiny session limit and bump session credits past it
    monkeypatch.setattr(app_mod, "KIRA_SESSION_LIMIT", 0.01)
    agent_store.record_credits(sid, 1.0)
    r = client.post("/agent", json={"prompt": "hi", "session_id": sid, "model": "claude-haiku-4.5"})
    assert r.status_code == 200
    body = r.text
    # SSE meta + error + done frames
    assert "\"type\": \"meta\"" in body
    assert "\"type\": \"error\"" in body
    assert "\"type\": \"done\"" in body


def test_agent_endpoint_no_api_key(client, monkeypatch):
    monkeypatch.setattr(app_mod, "KIRO_API_KEY", "")
    r = client.post("/agent", json={"prompt": "hi"})
    assert r.status_code == 400
    assert "KIRO_API_KEY" in r.json().get("error", "")


# ---------- /agent/stop ----------


def test_agent_stop_unknown_sid(client):
    r = client.post("/agent/stop/nosuch")
    assert r.status_code == 200
    assert r.json().get("ok") is False


# ---------- /agent/sessions + /agent/sessions/{sid} ----------


def _seed_session(sid: str):
    hist = [
        {"userInputMessage": {"content": "hello agent", "modelId": "m"}},
        {"assistantResponseMessage": {"content": "hi there", "toolUses": [{"toolUseId": "t1", "name": "fs_read", "input": {"path": "x"}}]}},
        {"userInputMessage": {"content": "", "userInputMessageContext": {"toolResults": [{"toolUseId": "t1", "status": "success", "content": [{"text": "file body"}]}]}}},
    ]
    agent_store.save_session(sid, hist, "claude-haiku-4.5", "smoke title")
    return hist


def test_agent_sessions_list_and_get(client):
    sid = "sess" + uuid.uuid4().hex[:8]
    _seed_session(sid)
    r = client.get("/agent/sessions")
    assert r.status_code == 200
    assert any(s.get("sid") == sid for s in r.json()["sessions"])

    r2 = client.get(f"/agent/sessions/{sid}")
    assert r2.status_code == 200
    d = r2.json()
    assert d["sid"] == sid
    roles = [t["role"] for t in d["transcript"]]
    assert "assistant" in roles and "tool" in roles


def test_agent_sessions_get_with_subagent_output(client):
    sid = "sub" + uuid.uuid4().hex[:8]
    sub_output = "=== Subagent #1 [success] ===\nquery: do thing\nresult body here\n"
    hist = [
        {"assistantResponseMessage": {"content": "", "toolUses": [{"toolUseId": "u1", "name": "use_subagent", "input": {}}]}},
        {"userInputMessage": {"content": "", "userInputMessageContext": {"toolResults": [{"toolUseId": "u1", "status": "success", "content": [{"text": sub_output}]}]}}},
    ]
    agent_store.save_session(sid, hist, "claude-haiku-4.5", "sub")
    r = client.get(f"/agent/sessions/{sid}")
    assert r.status_code == 200
    d = r.json()
    tool_entries = [t for t in d["transcript"] if t["role"] == "tool"]
    assert any("subagents" in t for t in tool_entries)


def test_agent_sessions_get_404(client):
    r = client.get("/agent/sessions/nope-zzz")
    assert r.status_code == 404


def test_agent_session_rename_and_delete(client, tmp_path, monkeypatch):
    sid = "ren" + uuid.uuid4().hex[:8]
    _seed_session(sid)
    r = client.post(f"/agent/sessions/{sid}/rename", json={"title": "new title "})
    assert r.status_code == 200 and r.json()["ok"]

    # create a workspace dir for delete branch to exercise rmtree
    ws = os.path.join(os.path.dirname(app_mod.__file__), "workspaces", sid)
    os.makedirs(ws, exist_ok=True)
    (open(os.path.join(ws, "f.txt"), "w")).write("ok")

    r = client.delete(f"/agent/sessions/{sid}")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert not os.path.isdir(ws)


# ---------- /agent/limits, /agent/plan ----------


def test_agent_limits_no_sid(client):
    r = client.get("/agent/limits")
    assert r.status_code == 200
    d = r.json()
    for k in ("session_credits", "session_limit", "day_credits", "month_credits"):
        assert k in d


def test_agent_limits_with_sid(client):
    sid = "lim" + uuid.uuid4().hex[:6]
    agent_store.record_credits(sid, 0.42)
    r = client.get(f"/agent/limits?session_id={sid}")
    assert r.status_code == 200 and r.json()["session_credits"] >= 0.4


def test_agent_plan_default(client):
    sid = "plan" + uuid.uuid4().hex[:6]
    r = client.get(f"/agent/plan/{sid}")
    assert r.status_code == 200
    assert r.json() == {"items": []}


# ---------- /agent/actions[/aid][/rollback] ----------


def test_agent_action_get_404(client):
    r = client.get("/agent/actions/9999999")
    assert r.status_code == 404


def test_agent_action_rollback_404(client):
    r = client.post("/agent/actions/9999999/rollback")
    assert r.status_code == 404


def test_agent_action_rollback_no_backup(client):
    aid = agent_store.log_action(sid="x", tool="fs_write", args={}, ok=True, file="/tmp/some.txt", backup="", tool_use_id="tu1")
    r = client.post(f"/agent/actions/{aid}/rollback")
    assert r.status_code == 400


def test_agent_action_rollback_missing_backup_file(client, tmp_path):
    f = tmp_path / "target.txt"
    f.write_text("NEW")
    aid = agent_store.log_action(sid="x", tool="fs_write", args={}, ok=True, file=str(f), backup="/nonexistent/path.bak", tool_use_id="tum")
    r = client.post(f"/agent/actions/{aid}/rollback")
    assert r.status_code == 404


def test_agent_action_rollback_full(client, tmp_path):
    f = tmp_path / "target.txt"
    bak = tmp_path / "target.bak"
    f.write_text("NEW")
    bak.write_text("OLD")
    aid = agent_store.log_action(sid="x", tool="fs_write", args={}, ok=True, file=str(f), backup=str(bak), tool_use_id="tu2")
    r = client.post(f"/agent/actions/{aid}/rollback")
    assert r.status_code == 200
    assert f.read_text() == "OLD"


# ---------- /agent/upload, /agent/file, /agent/reset ----------


def test_agent_upload_and_file(client):
    sid = "upl" + uuid.uuid4().hex[:8]
    files = [
        ("files", ("hello.txt", io.BytesIO(b"alpha-bytes"), "text/plain")),
        ("files", ("hello.txt", io.BytesIO(b"beta-bytes"), "text/plain")),
    ]
    r = client.post(f"/agent/upload/{sid}", files=files)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and len(d["saved"]) == 2
    # second file got numeric suffix
    names = [s["name"] for s in d["saved"]]
    assert any("_1" in n for n in names)

    # fetch first file via /agent/file
    r2 = client.get(f"/agent/file/{sid}/hello.txt")
    assert r2.status_code == 200
    assert r2.content == b"alpha-bytes"


def test_agent_file_404(client):
    sid = "f404" + uuid.uuid4().hex[:6]
    os.makedirs(os.path.join(os.path.dirname(app_mod.__file__), "workspaces", sid), exist_ok=True)
    r = client.get(f"/agent/file/{sid}/missing.txt")
    assert r.status_code == 404


def test_agent_file_path_traversal_rejected(client):
    sid = "trav" + uuid.uuid4().hex[:6]
    os.makedirs(os.path.join(os.path.dirname(app_mod.__file__), "workspaces", sid), exist_ok=True)
    r = client.get(f"/agent/file/{sid}/../../etc/passwd")
    # FastAPI may collapse `..` in path; either 400 or 404 is acceptable
    assert r.status_code in (400, 404)


def test_agent_file_bad_sid(client):
    r = client.get("/agent/file/bad..sid/x")
    assert r.status_code == 400


def test_agent_reset(client):
    sid = "rst" + uuid.uuid4().hex[:6]
    app_mod._AGENT_SESSIONS[sid] = [{"x": 1}]
    r = client.post("/agent/reset", json={"prompt": "", "session_id": sid})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert sid not in app_mod._AGENT_SESSIONS


# ---------- /usage error branches (provider-routed) ----------
#
# After Phase 3c.3 /usage delegates to llm.get_provider().usage(); these
# tests stub the provider directly rather than mocking httpx, so they
# survive vendor swaps.


def _stub_usage_provider(monkeypatch, payload):
    import llm

    class _Stub:
        name = "stub"
        supported_models = ["stub-1"]
        async def usage(self):
            if isinstance(payload, Exception):
                raise payload
            return payload

    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: _Stub())


def test_usage_no_key(client, monkeypatch):
    _stub_usage_provider(monkeypatch, {"supported": True, "status": "no_key",
                                        "error": "no Q API key available"})
    r = client.get("/usage")
    assert r.status_code == 400


def test_usage_upstream_error(client, monkeypatch):
    _stub_usage_provider(monkeypatch, {"supported": True, "status": "http_error",
                                        "http_status": 500, "error": "upstream busted"})
    r = client.get("/usage")
    assert r.status_code == 500
    assert r.json().get("error")


def test_usage_exception(client, monkeypatch):
    _stub_usage_provider(monkeypatch, RuntimeError("boom"))
    r = client.get("/usage")
    assert r.status_code == 500
    assert r.json().get("error") == "RuntimeError"


def test_usage_success(client, monkeypatch):
    _stub_usage_provider(monkeypatch, {
        "supported": True, "status": "ok",
        "plan": "Pro", "plan_type": "INDIVIDUAL",
        "used": 12.5, "limit": 100.0,
        "overage": 0.0, "overage_cap": 50.0, "overage_rate": 0.04,
        "overage_status": "DISABLED",
        "reset_at": "2026-01-01", "unit": "Credits",
    })
    r = client.get("/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "Pro"
    assert body["used"] == 12.5
    assert body["unit"] == "Credits"
    assert body["provider"] == "stub"

# ---------- stream_q early exit (no key) ----------


def test_stream_q_no_key(monkeypatch):
    monkeypatch.setattr(app_mod, "KIRO_API_KEY", "")

    async def collect():
        out = []
        async for chunk in app_mod.stream_q("claude", [{"role": "user", "content": "hi"}]):
            out.append(chunk)
        return out

    chunks = asyncio.run(collect())
    assert any(b"KIRO_API_KEY" in c for c in chunks)


# ---------- lifespan callback ----------


def test_lifespan_cleanup(monkeypatch):
    called = {"sids": ["old1", "old2"]}

    def fake_cleanup(_ttl):
        return called["sids"]

    monkeypatch.setattr(agent_store, "cleanup_old_sessions", fake_cleanup)
    # create a workspace for old1 to exercise rmtree
    ws = os.path.join(os.path.dirname(app_mod.__file__), "workspaces", "old1")
    os.makedirs(ws, exist_ok=True)

    async def run():
        async with app_mod._lifespan(app_mod.app):
            pass

    asyncio.run(run())
    assert not os.path.isdir(ws)


def test_lifespan_cleanup_error(monkeypatch):
    def boom(_ttl):
        raise RuntimeError("x")

    monkeypatch.setattr(agent_store, "cleanup_old_sessions", boom)

    async def run():
        async with app_mod._lifespan(app_mod.app):
            pass

    # Should swallow the error
    asyncio.run(run())
