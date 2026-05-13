"""Regression: record_credits must bump sessions.updated_at on ON CONFLICT.

Before fix, the ON CONFLICT DO UPDATE clause omitted updated_at, so a session
that kept receiving credit events kept its initial created_at as updated_at
forever, breaking the cleanup-by-age path.
"""
import time
import sqlite3


def test_record_credits_bumps_updated_at(store):
    sid = "ts_updated_at"
    store.save_session(sid, [], "m", title="t", owner_id="u1")
    old = time.time() - 3600
    with sqlite3.connect(store.DB_PATH) as c:
        c.execute("UPDATE sessions SET updated_at=? WHERE sid=?", (old, sid))
    time.sleep(0.01)
    store.record_credits(sid, 1.0, owner_id="u1")
    with sqlite3.connect(store.DB_PATH) as c:
        row = c.execute("SELECT updated_at FROM sessions WHERE sid=?", (sid,)).fetchone()
    assert row is not None, "session row missing"
    assert row[0] > old + 100, f"updated_at not bumped: {row[0]} vs old {old}"
