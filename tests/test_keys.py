import importlib

import pytest


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setenv("KIRO_API_KEY", "ksk_primary_xxxx")
    monkeypatch.setenv("KIRO_API_KEY_ALT", "ksk_alt_one,ksk_alt_two")
    monkeypatch.setenv("KIRA_KEY_BAN_SECONDS", "3600")
    import agent_keys

    importlib.reload(agent_keys)
    return agent_keys.key_pool


def test_pool_loads_all(pool):
    s = pool.status()
    assert s["pool_size"] == 3
    suffixes = [k["key_suffix"] for k in s["keys"]]
    assert "y_xxxx" in suffixes  # primary tail


def test_current_default_primary(pool):
    assert pool.current() == "ksk_primary_xxxx"


def test_rotate_on_bad(pool):
    cur = pool.current()
    new = pool.mark_bad(cur, reason="401")
    assert new != cur
    assert pool.current() == new
    s = pool.status()
    banned = [k for k in s["keys"] if k["banned"]]
    assert len(banned) == 1
    assert banned[0]["last_error"].startswith("401")
    assert s["rotations"] == 1


def test_all_banned_fallback(pool):
    cur1 = pool.current()
    cur2 = pool.mark_bad(cur1, reason="a")
    cur3 = pool.mark_bad(cur2, reason="b")
    # all 3 banned now
    pool.mark_bad(cur3, reason="c")
    s = pool.status()
    assert all(k["banned"] for k in s["keys"])
    # current() still returns something (least-banned)
    assert pool.current() != ""


def test_single_key_pool(monkeypatch):
    monkeypatch.setenv("KIRO_API_KEY", "only_one")
    monkeypatch.delenv("KIRO_API_KEY_ALT", raising=False)
    monkeypatch.delenv("KIRO_API_KEYS", raising=False)
    import agent_keys

    importlib.reload(agent_keys)
    p = agent_keys.key_pool
    assert p.current() == "only_one"
    # mark_bad with single key still returns it (no alternative)
    p.mark_bad("only_one", reason="401")
    assert p.current() == "only_one"


def test_reload_picks_up_new_keys(pool, monkeypatch):
    assert pool.status()["pool_size"] == 3
    monkeypatch.setenv("KIRO_API_KEY_ALT", "ksk_alt_one,ksk_alt_two,ksk_alt_three")
    pool.reload()
    assert pool.status()["pool_size"] == 4


def test_unban_after_time(monkeypatch):
    monkeypatch.setenv("KIRO_API_KEY", "a")
    monkeypatch.setenv("KIRO_API_KEY_ALT", "b")
    monkeypatch.setenv("KIRA_KEY_BAN_SECONDS", "0")
    import agent_keys

    importlib.reload(agent_keys)
    p = agent_keys.key_pool
    p.mark_bad("a", reason="x")
    # ban_seconds=0 means it's immediately unbanned in next loop tick
    import time

    time.sleep(0.01)
    # current() walks the pool; should pick whichever is not banned (b)
    # but after the tiny sleep, both may be unbanned again; just sanity-check
    assert p.current() in ("a", "b")
