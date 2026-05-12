"""LRU bound on _AGENT_SESSIONS prevents the prod memory leak.

Before this fix the cache was an unbounded dict — every new (user_id, sid)
session retained its full history (incl. base64 images) in RAM forever.
Bound configurable via KIRA_SESSION_CACHE_MAX (default 128).
"""

import app as app_mod


def test_session_cache_evicts_lru():
    cache = app_mod._SessionCache(maxsize=3)
    cache[("u", "a")] = [{"i": 1}]
    cache[("u", "b")] = [{"i": 2}]
    cache[("u", "c")] = [{"i": 3}]
    assert len(cache) == 3
    # Touch 'a' so it's recently used, then add 'd' -> 'b' should be evicted.
    _ = cache.get(("u", "a"))
    cache[("u", "d")] = [{"i": 4}]
    assert len(cache) == 3
    assert ("u", "b") not in cache
    assert ("u", "a") in cache
    assert ("u", "d") in cache


def test_session_cache_pop_returns_default():
    cache = app_mod._SessionCache(maxsize=2)
    cache[("u", "x")] = [{"i": 1}]
    assert cache.pop(("u", "x")) == [{"i": 1}]
    assert cache.pop(("u", "x")) is None
    assert cache.pop(("u", "x"), "sentinel") == "sentinel"


def test_session_cache_get_default():
    cache = app_mod._SessionCache(maxsize=2)
    assert cache.get(("u", "missing")) is None
    assert cache.get(("u", "missing"), []) == []


def test_session_cache_overwrite_does_not_grow():
    cache = app_mod._SessionCache(maxsize=2)
    cache[("u", "a")] = [1]
    cache[("u", "a")] = [2]  # overwrite, not new entry
    cache[("u", "b")] = [3]
    assert len(cache) == 2
    assert cache.get(("u", "a")) == [2]


def test_default_singleton_bounded():
    # Sanity: the prod singleton uses the env-configured cap.
    assert app_mod._AGENT_SESSIONS.maxsize >= 1
    assert isinstance(app_mod._AGENT_SESSIONS, app_mod._SessionCache)
