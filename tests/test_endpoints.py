"""FastAPI endpoint smoke tests via TestClient."""

import shutil


def test_healthz(app_client):
    r = app_client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_models_have_metadata(app_client):
    r = app_client.get("/models")
    d = r.json()
    assert r.status_code == 200
    assert d["models"] and "default" in d
    m = d["models"][0]
    for k in ("id", "label", "provider", "tier", "multiplier", "description", "strengths"):
        assert k in m, f"missing {k}"


def test_models_tiers_known(app_client):
    d = app_client.get("/models").json()
    tiers = {m["tier"] for m in d["models"]}
    assert tiers <= {"opus", "sonnet", "haiku"}


def test_root_serves_html(app_client):
    r = app_client.get("/")
    assert r.status_code == 200
    body = r.text
    # Critical UI bits must be present so we catch broken refs early.
    assert 'id="plan-panel"' in body
    assert 'id="models-view"' in body
    assert 'id="actions-view"' in body
    assert 'data-nav="actions"' in body
    # JS now lives in /static/app.js; verify the shell loads it.
    assert "/static/app.js" in body


def test_static_app_js_served(app_client):
    r = app_client.get("/static/app.js")
    assert r.status_code == 200
    body = r.text
    assert "applyModel(" in body
    assert "setModel(" not in body  # no broken legacy ref
    # The agent SSE handler (incl. the iframe branch) moved into agent_sse.js
    # in phase 3. app.js should now reference the module instead.
    assert "createAgentRunner" in body


def test_static_agent_sse_js_served(app_client):
    """Phase 3 extracted the SSE event loop into its own module."""
    r = app_client.get("/static/agent_sse.js")
    assert r.status_code == 200
    body = r.text
    assert "createAgentRunner" in body
    assert "type === 'iframe'" in body  # iframe handler shipped


def test_static_tool_cards_js_served(app_client):
    """Phase 4 extracted tool-card builders into their own module."""
    r = app_client.get("/static/tool_cards.js")
    assert r.status_code == 200
    body = r.text
    assert "createToolCards" in body
    assert "diff-rb" in body  # rollback button class kept


def test_static_sessions_js_served(app_client):
    """Phase 4b extracted server-side agent-session list/budget/restore."""
    r = app_client.get("/static/sessions.js")
    assert r.status_code == 200
    body = r.text
    assert "createAgentSessions" in body
    assert "/agent/sessions" in body  # the endpoint it owns
    assert "refreshAgentBudget" in body


def test_tool_specs_include_new_tools():
    import json
    from pathlib import Path

    specs = json.loads((Path(__file__).resolve().parent.parent / "agent_tool_specs.json").read_text())
    names = {t["toolSpecification"]["name"] for t in specs}
    for n in (
        "plan",
        "verify_change",
        "change_dir",
        "patch",
        "llm_one_shot",
        "output_iframe",
        "keyword_search",
        "outline",
        "browser_console_logs",
        "browser_network",
        "browser_accessibility",
        "browser_emulate",
    ):
        assert n in names, f"tool spec missing: {n}"


def test_skills_listed(app_client):
    r = app_client.get("/skills")
    d = r.json()
    assert "skills" in d and isinstance(d["skills"], list)


def test_session_not_found(app_client):
    r = app_client.get("/agent/sessions/nope-no-such")
    assert r.status_code == 404


def test_plan_endpoint_empty(app_client):
    r = app_client.get("/agent/plan/never-existed")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_actions_endpoint_returns_list(app_client):
    r = app_client.get("/agent/actions?limit=5")
    assert r.status_code == 200
    assert isinstance(r.json()["actions"], list)


def test_rollback_endpoint_404(app_client):
    r = app_client.post("/agent/actions/999999/rollback")
    assert r.status_code == 404


def test_action_get_endpoint(app_client, store):
    aid = store.log_action(
        "sx",
        "fs_write",
        {"path": "/x"},
        ok=True,
        file="/x",
        backup="/x.bak",
        diff="--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n",
        tool_use_id="tu",
    )
    r = app_client.get(f"/agent/actions/{aid}")
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == aid and d["diff"]


def test_rollback_roundtrip(app_client, tmp_path, store):
    f = tmp_path / "r.txt"
    f.write_text("original")
    bak = tmp_path / "r.txt.bak.1"
    shutil.copy2(f, bak)
    f.write_text("modified")
    aid = store.log_action("sid-rt", "fs_write", {"path": str(f)}, ok=True, file=str(f), backup=str(bak))
    r = app_client.post(f"/agent/actions/{aid}/rollback")
    assert r.status_code == 200, r.text
    assert f.read_text() == "original"
    # Pre-rollback copy of "modified" should now exist
    sibs = list(tmp_path.glob("r.txt.pre_rollback.*"))
    assert sibs and sibs[0].read_text() == "modified"


def test_rollback_missing_backup_400(app_client, store):
    aid = store.log_action("sid-x", "execute_bash", {"command": "ls"}, ok=True)  # no file/backup
    r = app_client.post(f"/agent/actions/{aid}/rollback")
    assert r.status_code == 400


def test_agent_limits(app_client):
    r = app_client.get("/agent/limits")
    assert r.status_code == 200
    for k in ("session_limit", "day_limit", "month_limit"):
        assert k in r.json()
