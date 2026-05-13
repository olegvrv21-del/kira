"""SQLite-backed persistence for /agent sessions.

Table schema:
  sessions(sid TEXT PK, title TEXT, model TEXT, created_at REAL, updated_at REAL)
  history(sid TEXT, idx INTEGER, msg_json TEXT, PRIMARY KEY (sid, idx))

History entries are the raw Q-protocol turns (the same dicts kept in memory).
Images embedded as base64 inflate rows; SQLite handles BLOBs of MB size fine.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_USER_MSG_BEGIN = "--- USER MESSAGE BEGIN ---"
_USER_MSG_END = "--- USER MESSAGE END ---"


def extract_user_text(content: str) -> str | None:
    """Pull the actual user prompt out of Kiro's wrapper template.

    Uses plain string slicing (no regex) to avoid catastrophic backtracking
    on long inputs starting with the BEGIN marker (CodeQL py/polynomial-redos).
    """
    if not isinstance(content, str):
        return None
    i = content.find(_USER_MSG_BEGIN)
    if i < 0:
        return None
    i += len(_USER_MSG_BEGIN)
    if i < len(content) and content[i] == "\n":
        i += 1
    j = content.find(_USER_MSG_END, i)
    if j < 0:
        return None
    t = content[i:j].strip()
    return t or None


DB_PATH = Path(__file__).parent / "agent_sessions.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, isolation_level=None)  # autocommit
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init() -> None:
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(
                sid TEXT PRIMARY KEY,
                title TEXT,
                model TEXT,
                created_at REAL,
                updated_at REAL,
                credits REAL DEFAULT 0,
                owner_id TEXT
            );
            CREATE TABLE IF NOT EXISTS user_credits(
                user_id TEXT,
                day TEXT,
                credits REAL DEFAULT 0,
                PRIMARY KEY(user_id, day)
            );
            CREATE INDEX IF NOT EXISTS idx_user_credits_day ON user_credits(day);
            CREATE TABLE IF NOT EXISTS history(
                sid TEXT,
                idx INTEGER,
                msg_json TEXT,
                PRIMARY KEY(sid, idx)
            );
            CREATE TABLE IF NOT EXISTS daily_credits(
                day TEXT PRIMARY KEY,
                credits REAL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
            CREATE TABLE IF NOT EXISTS actions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sid TEXT,
                ts REAL,
                tool TEXT,
                args_json TEXT,
                ok INTEGER,
                error TEXT,
                file TEXT,
                backup TEXT,
                diff TEXT,
                tool_use_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_actions_sid_ts ON actions(sid, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts DESC);
            CREATE TABLE IF NOT EXISTS session_meta(
                sid TEXT,
                key TEXT,
                value TEXT,
                updated_at REAL,
                PRIMARY KEY (sid, key)
            );
        """)
        # add column to old DBs
        cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
        if "credits" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN credits REAL DEFAULT 0")
        if "owner_id" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN owner_id TEXT")
        # Now safe to create the owner_id index (column guaranteed to exist).
        c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_id, updated_at DESC)")
        acols = {r[1] for r in c.execute("PRAGMA table_info(actions)").fetchall()}
        if acols:
            if "diff" not in acols:
                c.execute("ALTER TABLE actions ADD COLUMN diff TEXT")
            if "tool_use_id" not in acols:
                c.execute("ALTER TABLE actions ADD COLUMN tool_use_id TEXT")


def _today_key() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _month_key() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def get_month_credits() -> float:
    with _conn() as c:
        rows = c.execute(
            "SELECT credits FROM daily_credits WHERE day LIKE ?",
            (_month_key() + "-%",),
        ).fetchall()
    return sum(float(r[0]) for r in rows)


def record_credits(sid: str, credits_total_for_session: float, owner_id: str | None = None) -> None:
    """Set absolute session total; bump daily by the delta. If owner_id is
    given, also bump per-user daily totals so /agent/limits can be sliced
    per user.

    Also stamps `owner_id` on the sessions row when creating/claiming it, so a
    session that receives its first credit event BEFORE save_session() runs
    does not end up with NULL owner (which would leak it to every user via
    the legacy back-compat rule in list_sessions / _owner_ok).
    """
    with _conn() as c:
        row = c.execute("SELECT credits FROM sessions WHERE sid=?", (sid,)).fetchone()
        prev = float(row[0]) if row and row[0] is not None else 0.0
        delta = max(0.0, credits_total_for_session - prev)
        c.execute(
            "INSERT INTO sessions(sid, credits, created_at, updated_at, owner_id) VALUES (?,?,?,?,?) "
            "ON CONFLICT(sid) DO UPDATE SET "
            "credits=excluded.credits, "
            "updated_at=excluded.updated_at, "
            "owner_id=COALESCE(sessions.owner_id, excluded.owner_id)",
            (sid, credits_total_for_session, time.time(), time.time(), owner_id),
        )
        if delta > 0:
            day = _today_key()
            c.execute(
                "INSERT INTO daily_credits(day, credits) VALUES (?,?) "
                "ON CONFLICT(day) DO UPDATE SET credits = credits + excluded.credits",
                (day, delta),
            )
            if owner_id:
                c.execute(
                    "INSERT INTO user_credits(user_id, day, credits) VALUES (?,?,?) "
                    "ON CONFLICT(user_id, day) DO UPDATE SET credits = credits + excluded.credits",
                    (owner_id, day, delta),
                )


def get_user_today_credits(user_id: str) -> float:
    if not user_id:
        return 0.0
    with _conn() as c:
        r = c.execute(
            "SELECT credits FROM user_credits WHERE user_id=? AND day=?",
            (user_id, _today_key()),
        ).fetchone()
    return float(r[0]) if r else 0.0


def get_user_month_credits(user_id: str) -> float:
    if not user_id:
        return 0.0
    with _conn() as c:
        rows = c.execute(
            "SELECT credits FROM user_credits WHERE user_id=? AND day LIKE ?",
            (user_id, _month_key() + "-%"),
        ).fetchall()
    return sum(float(r[0]) for r in rows)


def get_session_credits(sid: str, owner_id: str | None = None) -> float:
    with _conn() as c:
        row = c.execute("SELECT credits, owner_id FROM sessions WHERE sid=?", (sid,)).fetchone()
    if not row:
        return 0.0
    if not _owner_ok(row[1], owner_id):
        return 0.0
    return float(row[0]) if row[0] is not None else 0.0


def get_today_credits() -> float:
    with _conn() as c:
        row = c.execute("SELECT credits FROM daily_credits WHERE day=?", (_today_key(),)).fetchone()
    return float(row[0]) if row else 0.0


def session_owner(sid: str) -> str | None:
    """Return owner_id of session, or None if session missing/legacy (NULL)."""
    with _conn() as c:
        r = c.execute("SELECT owner_id FROM sessions WHERE sid=?", (sid,)).fetchone()
    return r[0] if r else None


def _owner_ok(stored: str | None, requester: str | None) -> bool:
    """Authorization rule: legacy sessions (stored NULL) are visible to anyone
    so we don't break existing data. Otherwise stored must equal requester."""
    if requester is None:
        return True  # caller opted out of filtering
    if stored is None:
        return True
    return stored == requester


def load_history(sid: str, owner_id: str | None = None) -> list[dict] | None:
    with _conn() as c:
        row = c.execute("SELECT owner_id FROM sessions WHERE sid=?", (sid,)).fetchone()
        if not row:
            return None
        if not _owner_ok(row[0], owner_id):
            return None
        rows = c.execute("SELECT msg_json FROM history WHERE sid=? ORDER BY idx", (sid,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def save_session(
    sid: str,
    history: list[dict],
    model: str,
    title: str | None = None,
    owner_id: str | None = None,
) -> None:
    now = time.time()
    with _conn() as c:
        existing = c.execute("SELECT title, created_at, owner_id FROM sessions WHERE sid=?", (sid,)).fetchone()
        if existing:
            # Claim legacy (NULL) rows for the current owner so they stop being
            # globally visible after first authed touch.
            if owner_id and existing[2] is None:
                c.execute(
                    "UPDATE sessions SET model=?, updated_at=?, title=COALESCE(?, title), owner_id=? WHERE sid=?",
                    (model, now, title, owner_id, sid),
                )
            else:
                c.execute(
                    "UPDATE sessions SET model=?, updated_at=?, title=COALESCE(?, title) WHERE sid=?",
                    (model, now, title, sid),
                )
        else:
            c.execute(
                "INSERT INTO sessions(sid, title, model, created_at, updated_at, owner_id) VALUES (?,?,?,?,?,?)",
                (sid, title, model, now, now, owner_id),
            )
        # rewrite history (idempotent; histories are short enough)
        c.execute("DELETE FROM history WHERE sid=?", (sid,))
        c.executemany(
            "INSERT INTO history(sid, idx, msg_json) VALUES (?,?,?)",
            [(sid, i, json.dumps(m, ensure_ascii=False)) for i, m in enumerate(history)],
        )


def list_sessions(limit: int = 100, owner_id: str | None = None) -> list[dict[str, Any]]:
    where = ""
    params: tuple = ()
    if owner_id is not None:
        # Legacy (NULL) rows visible to everyone for back-compat.
        where = " WHERE (s.owner_id IS NULL OR s.owner_id = ?)"
        params = (owner_id,)
    with _conn() as c:
        rows = c.execute(
            f"""SELECT s.sid, s.title, s.model, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM history h WHERE h.sid=s.sid) AS n_msgs
               FROM sessions s{where} ORDER BY s.updated_at DESC LIMIT ?""",
            params + (limit,),
        ).fetchall()
    out = []
    with _conn() as c:
        for r in rows:
            cred = c.execute("SELECT credits FROM sessions WHERE sid=?", (r[0],)).fetchone()
            out.append(
                {
                    "sid": r[0],
                    "title": r[1],
                    "model": r[2],
                    "created_at": r[3],
                    "updated_at": r[4],
                    "n_msgs": r[5],
                    "credits": float(cred[0]) if cred and cred[0] is not None else 0.0,
                }
            )
    return out


def get_session_model(sid: str) -> str | None:
    with _conn() as c:
        row = c.execute("SELECT model FROM sessions WHERE sid=?", (sid,)).fetchone()
    return row[0] if row else None


def rename_session(sid: str, title: str, owner_id: str | None = None) -> bool:
    with _conn() as c:
        row = c.execute("SELECT owner_id FROM sessions WHERE sid=?", (sid,)).fetchone()
        if not row or not _owner_ok(row[0], owner_id):
            return False
        cur = c.execute("UPDATE sessions SET title=? WHERE sid=?", (title, sid))
        return cur.rowcount > 0


def delete_session(sid: str, owner_id: str | None = None) -> bool:
    with _conn() as c:
        row = c.execute("SELECT owner_id FROM sessions WHERE sid=?", (sid,)).fetchone()
        if not row or not _owner_ok(row[0], owner_id):
            return False
        c.execute("DELETE FROM history WHERE sid=?", (sid,))
        cur = c.execute("DELETE FROM sessions WHERE sid=?", (sid,))
        return cur.rowcount > 0


def derive_title(history: list[dict]) -> str | None:
    """First user-typed prompt (skip system prompt + tool-result turns)."""
    for m in history:
        uim = m.get("userInputMessage")
        if not uim:
            continue
        ctx = uim.get("userInputMessageContext") or {}
        if ctx.get("toolResults"):
            continue
        txt = extract_user_text(uim.get("content", ""))
        if txt:
            return txt.splitlines()[0][:80]
    return None


def cleanup_old_sessions(max_age_days: int) -> list[str]:
    """Delete sessions whose updated_at is older than N days. Returns deleted sids."""
    if max_age_days <= 0:
        return []
    cutoff = time.time() - max_age_days * 86400
    with _conn() as c:
        rows = c.execute("SELECT sid FROM sessions WHERE updated_at < ?", (cutoff,)).fetchall()
        sids = [r[0] for r in rows]
        for sid in sids:
            c.execute("DELETE FROM history WHERE sid=?", (sid,))
            c.execute("DELETE FROM sessions WHERE sid=?", (sid,))
    return sids


def log_action(
    sid: str,
    tool: str,
    args: dict,
    ok: bool,
    error: str | None = None,
    file: str | None = None,
    backup: str | None = None,
    diff: str | None = None,
    tool_use_id: str | None = None,
) -> int:
    try:
        args_s = json.dumps(args, ensure_ascii=False)[:8000]
    except Exception:
        args_s = str(args)[:8000]
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO actions(sid, ts, tool, args_json, ok, error, file, backup, diff, tool_use_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                sid,
                time.time(),
                tool,
                args_s,
                1 if ok else 0,
                (error or "")[:4000],
                file,
                backup,
                (diff or None),
                tool_use_id,
            ),
        )
        return int(cur.lastrowid)


_ACTION_COLS = "id, sid, ts, tool, args_json, ok, error, file, backup, diff, tool_use_id"


def _action_row(r) -> dict:
    return {
        "id": r[0],
        "sid": r[1],
        "ts": r[2],
        "tool": r[3],
        "args": r[4],
        "ok": bool(r[5]),
        "error": r[6],
        "file": r[7],
        "backup": r[8],
        "diff": r[9],
        "tool_use_id": r[10],
    }


def list_actions(sid: str | None = None, limit: int = 200, include_diff: bool = False) -> list[dict]:
    q = f"SELECT {_ACTION_COLS} FROM actions"
    params: tuple = ()
    if sid:
        q += " WHERE sid=?"
        params = (sid,)
    q += " ORDER BY ts DESC LIMIT ?"
    params = params + (limit,)
    with _conn() as c:
        rows = c.execute(q, params).fetchall()
    out = []
    for r in rows:
        d = _action_row(r)
        if not include_diff:
            d.pop("diff", None)
        out.append(d)
    return out


def compute_metrics(sid: str | None = None, window_seconds: float | None = None) -> dict:
    """Aggregate stats over the `actions` table.

    If sid is None: global metrics. Else: per-session.
    If window_seconds is set: only consider rows newer than now-window.
    """
    where = []
    params: list = []
    if sid:
        where.append("sid=?")
        params.append(sid)
    if window_seconds:
        where.append("ts>?")
        params.append(time.time() - float(window_seconds))
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    with _conn() as c:
        total = c.execute(f"SELECT COUNT(*) FROM actions{wsql}", params).fetchone()[0] or 0
        ok = (
            c.execute(f"SELECT COUNT(*) FROM actions{wsql}{' AND' if wsql else ' WHERE'} ok=1", params).fetchone()[0]
            or 0
        )
        fail = total - ok
        by_tool_rows = c.execute(
            f"SELECT tool, COUNT(*) as n, SUM(ok) as okn FROM actions{wsql} GROUP BY tool ORDER BY n DESC",
            params,
        ).fetchall()
        sessions = c.execute(f"SELECT COUNT(DISTINCT sid) FROM actions{wsql}", params).fetchone()[0] or 0
        # fs_write -> verify_change ratio: for each fs_write/patch event, look
        # whether a verify_change action followed it within 10 subsequent
        # actions of the same session.
        wsql_writes = wsql + (" AND" if wsql else " WHERE") + " tool IN ('fs_write','patch')"
        writes = c.execute(f"SELECT id, sid, ts FROM actions{wsql_writes}", params).fetchall()
        verified_writes = 0
        for _wid, wsid, wts in writes:
            r = c.execute(
                "SELECT 1 FROM actions WHERE sid=? AND ts>? AND tool='verify_change' ORDER BY ts LIMIT 1",
                (wsid, wts),
            ).fetchone()
            if r:
                verified_writes += 1
        # Rollback approximation: actions where args contain '"rollback"' OR
        # post-write rollback is tracked separately by /agent/actions/{id}/rollback;
        # we don't have a flag, so we count actions tagged tool='_rollback'.
        rollbacks = (
            c.execute(
                f"SELECT COUNT(*) FROM actions{wsql}{' AND' if wsql else ' WHERE'} tool='_rollback'",
                params,
            ).fetchone()[0]
            or 0
        )
        # Hook denies: tool='_hook_deny'.
        hook_denies = (
            c.execute(
                f"SELECT COUNT(*) FROM actions{wsql}{' AND' if wsql else ' WHERE'} tool='_hook_deny'",
                params,
            ).fetchone()[0]
            or 0
        )
        # Top error tools
        top_errors = c.execute(
            f"SELECT tool, COUNT(*) as n FROM actions{wsql}{' AND' if wsql else ' WHERE'} ok=0 GROUP BY tool ORDER BY n DESC LIMIT 5",
            params,
        ).fetchall()
    return {
        "sid": sid,
        "window_seconds": window_seconds,
        "total": total,
        "ok": ok,
        "fail": fail,
        "success_rate": (ok / total) if total else None,
        "sessions": sessions,
        "by_tool": [
            {"tool": t, "count": n, "ok": (okn or 0), "success_rate": (okn or 0) / n if n else None}
            for (t, n, okn) in by_tool_rows
        ],
        "writes": len(writes),
        "writes_verified": verified_writes,
        "verify_ratio": (verified_writes / len(writes)) if writes else None,
        "rollbacks": rollbacks,
        "hook_denies": hook_denies,
        "top_errors": [{"tool": t, "count": n} for (t, n) in top_errors],
    }


def get_action(aid: int) -> dict | None:
    with _conn() as c:
        r = c.execute(
            f"SELECT {_ACTION_COLS} FROM actions WHERE id=?",
            (aid,),
        ).fetchone()
    return _action_row(r) if r else None


def set_meta(sid: str, key: str, value) -> None:
    val = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    with _conn() as c:
        c.execute(
            "INSERT INTO session_meta(sid, key, value, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(sid,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (sid, key, val, time.time()),
        )


def get_meta(sid: str, key: str, default=None):
    with _conn() as c:
        r = c.execute(
            "SELECT value FROM session_meta WHERE sid=? AND key=?",
            (sid, key),
        ).fetchone()
    if not r:
        return default
    try:
        return json.loads(r[0])
    except Exception:
        return r[0]


def get_all_meta(sid: str) -> dict:
    with _conn() as c:
        rows = c.execute(
            "SELECT key, value FROM session_meta WHERE sid=?",
            (sid,),
        ).fetchall()
    out = {}
    for k, v in rows:
        try:
            out[k] = json.loads(v)
        except Exception:
            out[k] = v
    return out
