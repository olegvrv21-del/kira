"""Kiro API key pool with auto-rotation on 401/403.

Env variables:
  KIRO_API_KEY              primary key (always present)
  KIRO_API_KEY_ALT          comma-separated list of fallback keys (optional)
  KIRO_API_KEYS             alternative single env: comma-separated full pool
  KIRA_KEY_BAN_SECONDS      how long a key stays banned after 401/403 (def 3600)

Usage:
    from agent_keys import key_pool
    api_key = key_pool.current()
    # ... make request ...
    if status in (401, 403):
        key_pool.mark_bad(api_key, reason="401")
        api_key = key_pool.current()  # auto-rotated

If all keys are exhausted, current() falls back to the primary (so the caller
still gets a string and the upstream returns its own 401, surfaced to the user).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Iterable

_BAN_SECONDS = int(os.environ.get("KIRA_KEY_BAN_SECONDS", "3600"))


def _load_pool() -> list[str]:
    pool: list[str] = []
    primary = os.environ.get("KIRO_API_KEY", "").strip()
    if primary:
        pool.append(primary)
    raw = os.environ.get("KIRO_API_KEYS", "").strip()
    if raw:
        for k in raw.split(","):
            k = k.strip()
            if k and k not in pool:
                pool.append(k)
    alt = os.environ.get("KIRO_API_KEY_ALT", "").strip()
    if alt:
        for k in alt.split(","):
            k = k.strip()
            if k and k not in pool:
                pool.append(k)
    return pool


class KeyPool:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pool = _load_pool()
        # key -> {banned_until: float|None, last_error: str, rotations: int}
        self._state: dict[str, dict] = {}
        for k in self._pool:
            self._state.setdefault(k, {"banned_until": 0.0, "last_error": "",
                                        "used": 0, "failed": 0})
        self._idx = 0
        self._rotations = 0

    # ---- public API ----

    def reload(self) -> None:
        """Re-read env (call after writing a new key to systemd override)."""
        with self._lock:
            new = _load_pool()
            for k in new:
                self._state.setdefault(k, {"banned_until": 0.0,
                                            "last_error": "",
                                            "used": 0, "failed": 0})
            self._pool = new
            if self._idx >= len(self._pool):
                self._idx = 0

    def current(self) -> str:
        """Return a currently-usable key (skips banned ones)."""
        with self._lock:
            return self._current_unlocked()

    def _current_unlocked(self) -> str:
        if not self._pool:
            return ""
        now = time.time()
        n = len(self._pool)
        for offset in range(n):
            i = (self._idx + offset) % n
            k = self._pool[i]
            st = self._state[k]
            if (st["banned_until"] or 0) <= now:
                self._idx = i
                st["used"] += 1
                return k
        # All banned — return the one with earliest unban time, but don't reset.
        return min(self._pool, key=lambda k: self._state[k]["banned_until"] or 0)

    def mark_bad(self, key: str, *, reason: str = "",
                 ban_seconds: int | None = None) -> str | None:
        """Ban this key and rotate to the next. Returns the new current key."""
        with self._lock:
            if key not in self._state:
                # Unknown key (e.g. removed from env); just rotate
                return self._current_unlocked() or None
            self._state[key]["banned_until"] = time.time() + (ban_seconds or _BAN_SECONDS)
            self._state[key]["last_error"] = reason[:200]
            self._state[key]["failed"] += 1
            self._rotations += 1
            # advance index past the banned key
            n = len(self._pool)
            if n > 1:
                try:
                    cur_i = self._pool.index(key)
                    self._idx = (cur_i + 1) % n
                except ValueError:
                    pass
            return self._current_unlocked() or None

    def status(self) -> dict:
        now = time.time()
        with self._lock:
            keys = []
            for k in self._pool:
                st = self._state[k]
                ban = st["banned_until"] or 0
                keys.append({
                    "key_suffix": (k[-6:] if len(k) > 6 else k),
                    "current": (k == self._pool[self._idx]) if self._pool else False,
                    "banned": ban > now,
                    "banned_for_seconds": max(0, int(ban - now)),
                    "last_error": st["last_error"],
                    "used": st["used"],
                    "failed": st["failed"],
                })
            return {"pool_size": len(self._pool),
                    "rotations": self._rotations,
                    "keys": keys}


key_pool = KeyPool()
