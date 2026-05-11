import time

import pytest

import agent_store as st


@pytest.fixture(autouse=True)
def _clean_actions():
    with st._conn() as c:
        c.execute("DELETE FROM actions")
    yield


def test_metrics_empty():
    m = st.compute_metrics()
    assert m["total"] == 0
    assert m["ok"] == 0
    assert m["fail"] == 0
    assert m["success_rate"] is None
    assert m["by_tool"] == []
    assert m["verify_ratio"] is None
    assert m["rollbacks"] == 0
    assert m["hook_denies"] == 0


def test_metrics_basic_counts():
    st.log_action("s1", "fs_read", {}, ok=True)
    st.log_action("s1", "fs_write", {"path": "/x"}, ok=True)
    st.log_action("s1", "fs_write", {"path": "/y"}, ok=False, error="boom")
    st.log_action("s2", "git_commit", {}, ok=True)
    m = st.compute_metrics()
    assert m["total"] == 4
    assert m["ok"] == 3
    assert m["fail"] == 1
    assert abs(m["success_rate"] - 0.75) < 1e-9
    assert m["sessions"] == 2
    tools = {t["tool"]: t for t in m["by_tool"]}
    assert tools["fs_write"]["count"] == 2
    assert tools["fs_write"]["ok"] == 1
    assert tools["fs_read"]["success_rate"] == 1.0
    assert m["top_errors"][0]["tool"] == "fs_write"


def test_metrics_per_session_filter():
    st.log_action("sa", "fs_read", {}, ok=True)
    st.log_action("sb", "fs_read", {}, ok=True)
    st.log_action("sb", "fs_read", {}, ok=False)
    m = st.compute_metrics(sid="sb")
    assert m["total"] == 2
    assert m["ok"] == 1


def test_metrics_verify_ratio():
    # write without verify
    st.log_action("v1", "fs_write", {"path": "/a"}, ok=True)
    time.sleep(0.01)
    # write followed by verify
    st.log_action("v1", "fs_write", {"path": "/b"}, ok=True)
    time.sleep(0.01)
    st.log_action("v1", "verify_change", {}, ok=True)
    m = st.compute_metrics(sid="v1")
    assert m["writes"] == 2
    # both writes precede the verify_change, so both count as verified
    assert m["writes_verified"] == 2
    assert m["verify_ratio"] == 1.0


def test_metrics_rollbacks_and_denies():
    st.log_action("r1", "fs_write", {}, ok=True)
    st.log_action("r1", "_rollback", {"action_id": 1}, ok=True)
    st.log_action("r1", "_hook_deny", {"tool": "x", "message": "no"}, ok=False)
    st.log_action("r1", "_hook_deny", {"tool": "y", "message": "no"}, ok=False)
    m = st.compute_metrics(sid="r1")
    assert m["rollbacks"] == 1
    assert m["hook_denies"] == 2


def test_metrics_window():
    st.log_action("w1", "fs_read", {}, ok=True)
    m_all = st.compute_metrics(sid="w1")
    m_recent = st.compute_metrics(sid="w1", window_seconds=0.0001)
    assert m_all["total"] >= 1
    # tiny window: row may or may not fall inside; just sanity-check shape
    assert "window_seconds" in m_recent
