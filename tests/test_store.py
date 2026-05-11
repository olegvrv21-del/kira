"""agent_store: actions log, plan meta, sessions."""


def test_log_and_list_actions(store):
    aid = store.log_action("s1", "fs_write", {"path": "/x"}, ok=True, file="/x", backup="/x.bak.1")
    assert aid > 0
    rows = store.list_actions(sid="s1")
    assert any(r["id"] == aid and r["tool"] == "fs_write" for r in rows)
    a = store.get_action(aid)
    assert a["backup"] == "/x.bak.1"
    assert a["ok"] is True


def test_action_failure_recorded(store):
    aid = store.log_action("s1", "execute_bash", {"command": "false"}, ok=False, error="nonzero")
    a = store.get_action(aid)
    assert a["ok"] is False
    assert "nonzero" in a["error"]


def test_meta_set_get(store):
    store.set_meta("s2", "plan", {"items": [{"text": "step 1", "status": "pending"}]})
    p = store.get_meta("s2", "plan")
    assert isinstance(p, dict) and p["items"][0]["text"] == "step 1"
    assert store.get_meta("s2", "missing", "default") == "default"
    full = store.get_all_meta("s2")
    assert "plan" in full


def test_meta_overwrite(store):
    store.set_meta("s3", "k", "v1")
    store.set_meta("s3", "k", "v2")
    assert store.get_meta("s3", "k") == "v2"


def test_session_save_and_model(store):
    hist = [{"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nhi\n--- USER MESSAGE END ---"}}]
    store.save_session("s4", hist, "claude-opus-4.7", title="t")
    assert store.get_session_model("s4") == "claude-opus-4.7"
    loaded = store.load_history("s4")
    assert loaded and "hi" in loaded[0]["userInputMessage"]["content"]
    items = store.list_sessions(limit=10)
    assert any(i["sid"] == "s4" for i in items)
