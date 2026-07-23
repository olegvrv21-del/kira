"""Frugality guard — 'cheap-capable first', encoded.

Why this exists
---------------
The single most useful discipline a spending agent can have is: reach for the
*cheapest model that can plausibly do the job*, and treat the expensive models
as a scarce resource — not a default. A careless burst of top-tier calls
(e.g. running claude-opus on throwaway probes) can drain a whole account's
balance in minutes. This module makes that discipline structural rather than
a matter of remembering.

What it does
------------
Tracks how many *expensive-tier* generations have run today and enforces a
daily cap. When the cap is hit, instead of spending, it **downgrades** the
request to a cheaper model and surfaces a friendly note. Cheap models are
never limited — only the pricey ones.

This is intentionally about *call counts per tier*, not credits: it's a simple,
predictable circuit-breaker that a human can reason about ("max 30 opus calls a
day") and that degrades gracefully instead of hard-failing.

Config (env)
------------
  KIRA_FRUGAL=1                       master switch (default on)
  KIRA_EXPENSIVE_MODELS=<csv-substr>  model-id substrings treated as expensive
                                      (default: opus,gpt-5.6,gpt-5.5)
  KIRA_EXPENSIVE_DAILY_CAP=40         max expensive-tier calls per UTC day (0=off)
  KIRA_FRUGAL_DOWNGRADE=gpt-5.4       model to fall back to when the cap is hit

Storage: a tiny table `tier_usage(day, bucket, n)` in the sessions DB. Counting
is best-effort — a DB hiccup must never block a real request.
"""

from __future__ import annotations

import datetime as _dt
import os

import agent_store


def _today() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d")


def enabled() -> bool:
    return os.environ.get("KIRA_FRUGAL", "1") not in ("", "0", "false", "False")


def _expensive_markers() -> list[str]:
    raw = os.environ.get("KIRA_EXPENSIVE_MODELS", "opus,gpt-5.6,gpt-5.5")
    return [m.strip().lower() for m in raw.split(",") if m.strip()]


def is_expensive(model: str) -> bool:
    m = (model or "").lower()
    return any(marker in m for marker in _expensive_markers())


def _daily_cap() -> int:
    try:
        return int(os.environ.get("KIRA_EXPENSIVE_DAILY_CAP", "40"))
    except ValueError:
        return 40


def _downgrade_model() -> str:
    return os.environ.get("KIRA_FRUGAL_DOWNGRADE", "gpt-5.4")


# --- persistence (best-effort) ---------------------------------------------


def _ensure_table() -> None:
    try:
        with agent_store._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS tier_usage("
                "day TEXT, bucket TEXT, n INTEGER DEFAULT 0, "
                "PRIMARY KEY(day, bucket))"
            )
    except Exception:
        pass


def expensive_calls_today() -> int:
    _ensure_table()
    try:
        with agent_store._conn() as c:
            row = c.execute(
                "SELECT n FROM tier_usage WHERE day=? AND bucket='expensive'",
                (_today(),),
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def _record_expensive() -> None:
    _ensure_table()
    try:
        with agent_store._conn() as c:
            c.execute(
                "INSERT INTO tier_usage(day, bucket, n) VALUES (?, 'expensive', 1) "
                "ON CONFLICT(day, bucket) DO UPDATE SET n = n + 1",
                (_today(),),
            )
    except Exception:
        pass


# --- the guard --------------------------------------------------------------


def guard(model: str) -> tuple[str, str | None]:
    """Given a model the caller wants to use, return (model_to_use, note).

    * Cheap model, or guard disabled → returned unchanged, note=None.
    * Expensive model under the daily cap → returned unchanged (and counted),
      note=None.
    * Expensive model over the cap → downgraded model + a human-readable note
      explaining the switch. The downgrade is only applied if it actually
      lands on a cheaper model (avoids an infinite/no-op swap).
    """
    if not enabled() or not is_expensive(model):
        return model, None

    cap = _daily_cap()
    if cap <= 0:
        _record_expensive()
        return model, None

    used = expensive_calls_today()
    if used < cap:
        _record_expensive()
        return model, None

    # Over cap → downgrade.
    downgrade = _downgrade_model()
    if is_expensive(downgrade) or downgrade == model:
        # Misconfigured downgrade target — don't loop; allow but warn.
        _record_expensive()
        return model, (
            f"⚠️ Дневной лимит дорогих моделей исчерпан ({used}/{cap}), "
            f"но резервная модель не настроена корректно — продолжаю на {model}."
        )
    note = (
        f"💸 Дневной лимит дорогих моделей исчерпан ({used}/{cap}). "
        f"Переключаюсь с {model} на более дешёвую {downgrade}, чтобы беречь баланс. "
        f"Лимит сбросится завтра (UTC)."
    )
    return downgrade, note


def status() -> dict:
    return {
        "enabled": enabled(),
        "expensive_calls_today": expensive_calls_today(),
        "daily_cap": _daily_cap(),
        "expensive_markers": _expensive_markers(),
        "downgrade_model": _downgrade_model(),
    }
