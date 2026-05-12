"""Coverage uplift for agent_critic / agent_keys / agent_store.

Targets the remaining missing branches reported by pytest --cov so the three
modules cross the 95% threshold tracked in ~/notebook/TODO.md.
"""

from __future__ import annotations

import time

import pytest


# ----------------------------- agent_critic --------------------------------


def test_critic_truncate_short_passthrough():
    import agent_critic

    assert agent_critic._truncate("abc", 10) == "abc"


def test_critic_truncate_long_inserts_marker():
    import agent_critic

    out = agent_critic._truncate("x" * 100, 10)
    assert "[diff truncated]" in out
    # rough head+tail length around the marker
    assert out.startswith("xxxxx") and out.endswith("xxxxx")


def test_critic_is_auto_enabled_and_reload(monkeypatch):
    import agent_critic

    monkeypatch.setenv("KIRA_CRITIC_AUTO", "1")
    monkeypatch.setenv("KIRA_CRITIC_MODEL", "claude-mini")
    monkeypatch.setenv("KIRA_CRITIC_MAX_DIFF", "123")
    agent_critic.reload_flags()
    assert agent_critic.is_auto_enabled() is True
    assert agent_critic._DEFAULT_MODEL == "claude-mini"
    assert agent_critic._MAX_DIFF == 123
    monkeypatch.setenv("KIRA_CRITIC_AUTO", "0")
    agent_critic.reload_flags()
    assert agent_critic.is_auto_enabled() is False


@pytest.mark.asyncio
async def test_critic_amazon_q_branch_uses_qprovider(monkeypatch):
    """Hit the `provider_name == 'amazon-q'` branch in review_diff.

    We stub QProvider so no network is touched and confirm it actually got
    constructed with the api_key argument from the caller.
    """
    import agent_critic
    from llm.base import StreamEvent

    captured = {}

    class _StubQ:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key

        async def stream(self, messages, tools, *, model, cancel=None, timeout=300, extra=None):
            yield StreamEvent(type="text", text="VERDICT: OK")
            yield StreamEvent(type="done")

    import llm.q_provider as qp

    monkeypatch.setattr(qp, "QProvider", _StubQ)
    monkeypatch.setenv("KIRA_LLM_PROVIDER", "amazon-q")

    v = await agent_critic.review_diff("api-key-XYZ", "diff --git a/x b/x\n+ok\n")
    assert v["verdict"] == "OK"
    # key_pool.current() may return a real key from env; the fallback is the
    # caller-supplied one. Either way, _StubQ MUST have been constructed.
    assert "api_key" in captured


def test_critic_parse_issues_terminate_on_non_bullet():
    import agent_critic

    raw = "VERDICT: OK\nISSUES:\n- one\nplain line ends issues\n- ignored"
    v = agent_critic.parse_verdict(raw)
    assert v["issues"] == ["one"]


# ----------------------------- agent_keys ----------------------------------


@pytest.fixture
def restore_pool():
    """Capture/restore key_pool state so _fresh_pool tests don't leak into
    test_health_endpoint which inspects the same singleton."""
    import agent_keys
    p = agent_keys.key_pool
    with p._lock:
        snap = (list(p._pool), {k: dict(v) for k, v in p._state.items()},
                p._idx, p._rotations)
    yield p
    with p._lock:
        p._pool, p._state, p._idx, p._rotations = snap[0], snap[1], snap[2], snap[3]


def _fresh_pool(monkeypatch, primary="p_main", alt="", keys_csv=""):
    """Reconfigure the shared key_pool *in place*.

    We deliberately avoid `importlib.reload(agent_keys)`: app.py captured
    `from agent_keys import key_pool` at import time, so reloading the module
    leaves stale references everywhere and breaks unrelated tests.
    Instead, swap env vars and call the public `key_pool.reload()` which
    rebuilds the pool while keeping the singleton identity.
    """
    monkeypatch.setenv("KIRO_API_KEY", primary)
    if alt:
        monkeypatch.setenv("KIRO_API_KEY_ALT", alt)
    else:
        monkeypatch.delenv("KIRO_API_KEY_ALT", raising=False)
    if keys_csv:
        monkeypatch.setenv("KIRO_API_KEYS", keys_csv)
    else:
        monkeypatch.delenv("KIRO_API_KEYS", raising=False)
    import agent_keys

    p = agent_keys.key_pool
    # Reset internal state to a known baseline before reload (the existing
    # singleton may already carry bans/rotations from previous tests).
    with p._lock:
        p._pool = []
        p._state = {}
        p._idx = 0
        p._rotations = 0
    p.reload()
    return p


def test_keys_loads_from_keys_csv_env(monkeypatch, restore_pool):
    p = _fresh_pool(monkeypatch, primary="primary", keys_csv="primary,k2,k3")
    # primary already in pool — CSV variant skips dupes
    suffixes = [k["key_suffix"] for k in p.status()["keys"]]
    assert suffixes == ["rimary", "k2", "k3"]


def test_keys_csv_and_alt_dedup(monkeypatch, restore_pool):
    p = _fresh_pool(monkeypatch, primary="a", keys_csv="b,c", alt="c,d")
    suffixes = [k["key_suffix"] for k in p.status()["keys"]]
    # 'c' must not duplicate
    assert suffixes == ["a", "b", "c", "d"]


def test_keys_empty_env_returns_empty_string(monkeypatch, restore_pool):
    monkeypatch.delenv("KIRO_API_KEY", raising=False)
    monkeypatch.delenv("KIRO_API_KEY_ALT", raising=False)
    monkeypatch.delenv("KIRO_API_KEYS", raising=False)
    import agent_keys

    p = agent_keys.key_pool
    with p._lock:
        p._pool = []
        p._state = {}
        p._idx = 0
        p._rotations = 0
    p.reload()
    assert p.current() == ""


def test_keys_reload_shrinks_pool(monkeypatch, restore_pool):
    p = _fresh_pool(monkeypatch, primary="a", alt="b,c")
    assert p.status()["pool_size"] == 3
    # Drain index to the tail so reload triggers idx-reset path.
    p.mark_bad("a", reason="x", ban_seconds=10)
    p.mark_bad("b", reason="y", ban_seconds=10)
    # Now shrink pool by removing alt entirely.
    monkeypatch.delenv("KIRO_API_KEY_ALT", raising=False)
    monkeypatch.delenv("KIRO_API_KEYS", raising=False)
    p.reload()
    assert p.status()["pool_size"] == 1


def test_keys_mark_bad_unknown_key(monkeypatch, restore_pool):
    """mark_bad on a key that's not in the pool should still rotate gracefully."""
    p = _fresh_pool(monkeypatch, primary="a", alt="b")
    res = p.mark_bad("not_in_pool", reason="stale")
    # Unknown key path: returns current() without recording a ban.
    assert res in ("a", "b")
    # No new rotations recorded since we didn't actually ban a real key.
    assert p.status()["rotations"] == 0


# ----------------------------- agent_store ---------------------------------


def test_store_user_credits_empty_user(store):
    assert store.get_user_today_credits("") == 0.0
    assert store.get_user_month_credits("") == 0.0


def test_store_user_credits_aggregate(store, monkeypatch):
    # record_credits increments today's bucket for the user
    store.save_session("scu1", [], "m", title="t", owner_id="userA")
    # record_credits stores absolute session total; bump twice with increasing values.
    store.record_credits("scu1", 1.5, owner_id="userA")
    store.record_credits("scu1", 1.75, owner_id="userA")
    today = store.get_user_today_credits("userA")
    month = store.get_user_month_credits("userA")
    assert today >= 1.75 - 1e-6
    assert month >= today - 1e-6


def test_store_get_session_credits_owner_mismatch(store):
    store.save_session("scu2", [], "m", title="t", owner_id="alice")
    store.record_credits("scu2", 2.0, owner_id="alice")
    # alice sees her credits
    assert store.get_session_credits("scu2", owner_id="alice") >= 2.0 - 1e-6
    # bob (different owner) sees zero
    assert store.get_session_credits("scu2", owner_id="bob") == 0.0
    # missing sid returns 0
    assert store.get_session_credits("nonexistent") == 0.0


def test_store_update_branch_preserves_owner(store):
    # First save with owner None (legacy), then save again as authed user
    # to exercise the COALESCE/UPDATE branch that *doesn't* set owner_id.
    store.save_session("scu3", [], "m", title="orig")
    store.save_session("scu3", [], "m2", title="renamed")  # owner stays NULL
    items = [i for i in store.list_sessions(limit=50) if i["sid"] == "scu3"]
    assert items and items[0]["model"] == "m2"


def test_store_derive_title_skips_tool_results(store):
    hist = [
        # First message: tool result — must be skipped.
        {
            "userInputMessage": {
                "content": "ignored",
                "userInputMessageContext": {"toolResults": [{"x": 1}]},
            }
        },
        # Then the real user turn.
        {
            "userInputMessage": {
                "content": "--- USER MESSAGE BEGIN ---\nreal prompt line 1\nline2\n--- USER MESSAGE END ---",
                "userInputMessageContext": {},
            }
        },
    ]
    title = store.derive_title(hist)
    assert title == "real prompt line 1"


def test_store_derive_title_no_user_text(store):
    # Only assistant turns / no userInputMessage at all
    assert store.derive_title([{"assistantResponseMessage": {"content": "hi"}}]) is None


def test_store_cleanup_old_sessions(store):
    # Insert two sessions with backdated updated_at and one fresh.
    store.save_session("cleanup_old1", [], "m")
    store.save_session("cleanup_old2", [], "m")
    store.save_session("cleanup_fresh", [], "m")
    with store._conn() as c:
        old = time.time() - 365 * 86400
        c.execute("UPDATE sessions SET updated_at=? WHERE sid IN (?,?)", (old, "cleanup_old1", "cleanup_old2"))
    sids = store.cleanup_old_sessions(30)
    assert set(sids) >= {"cleanup_old1", "cleanup_old2"}
    assert "cleanup_fresh" not in sids
    # No-op when max_age_days <= 0
    assert store.cleanup_old_sessions(0) == []


def test_store_log_action_handles_unserializable_args(store):
    class _NotJSON:
        def __repr__(self):
            return "<NotJSON>"

    aid = store.log_action("scu_log", "weird_tool", {"obj": _NotJSON()}, ok=True)
    assert aid > 0
    a = store.get_action(aid)
    # args is the serialized string (truncated); since the dict contains an
    # unserialisable value, the except branch falls back to str(args)[:8000].
    assert "NotJSON" in str(a["args"])


def test_store_get_all_meta_with_plain_string(store):
    # Plain strings (non-JSON) take the except branch in get_all_meta.
    store.set_meta("scu_meta", "raw_key", "plain_str_value")
    full = store.get_all_meta("scu_meta")
    assert full["raw_key"] == "plain_str_value"
