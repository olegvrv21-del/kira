"""plan tool: handler and round-trip via store."""

import agent_runtime


def test_plan_set(store):
    status, msg = agent_runtime._handle_plan("pl1", {"op": "set", "items": ["step a", "step b"]})
    assert status == "success"
    p = agent_runtime._load_plan("pl1")
    assert [i["text"] for i in p["items"]] == ["step a", "step b"]
    assert all(i["status"] == "pending" for i in p["items"])


def test_plan_update(store):
    agent_runtime._handle_plan("pl2", {"op": "set", "items": ["a", "b"]})
    status, _ = agent_runtime._handle_plan("pl2", {"op": "update", "index": 0, "status": "done"})
    assert status == "success"
    p = agent_runtime._load_plan("pl2")
    assert p["items"][0]["status"] == "done"
    assert p["items"][1]["status"] == "pending"


def test_plan_update_invalid_index(store):
    agent_runtime._handle_plan("pl3", {"op": "set", "items": ["only"]})
    status, msg = agent_runtime._handle_plan("pl3", {"op": "update", "index": 9, "status": "done"})
    assert status == "error" and "invalid index" in msg


def test_plan_add_and_clear(store):
    agent_runtime._handle_plan("pl4", {"op": "set", "items": ["a"]})
    agent_runtime._handle_plan("pl4", {"op": "add", "text": "b"})
    p = agent_runtime._load_plan("pl4")
    assert [i["text"] for i in p["items"]] == ["a", "b"]
    agent_runtime._handle_plan("pl4", {"op": "clear"})
    assert agent_runtime._load_plan("pl4")["items"] == []


def test_plan_set_empty_errors(store):
    status, _ = agent_runtime._handle_plan("pl5", {"op": "set", "items": []})
    assert status == "error"


def test_plan_unknown_op(store):
    status, msg = agent_runtime._handle_plan("pl6", {"op": "explode"})
    assert status == "error" and "unknown plan op" in msg
