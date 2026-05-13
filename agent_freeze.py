"""Emergency kill-switch for the agent.

A simple file-flag based freeze. When `.frozen` exists in the webchat root:
  * POST /agent and POST /chat reject with 503 + reason
  * `agent_runtime.run_agent` aborts at turn-0 with an error SSE
  * read-only endpoints (/agent/health, /agent/self, GET /agent/sessions)
    keep working so we can still introspect

The flag is created/removed by master-token-only endpoints:
  POST /agent/freeze   {"reason": "why"}
  POST /agent/unfreeze

Master token = KIRA_MASTER_TOKEN env, falls back to KIRA_AUTH_TOKEN (first
entry). Designed so even a compromised non-master token cannot unfreeze.

Why a file (not env): survives webchat restart, visible via `ls` from SSH,
trivially scriptable from a cron/TG bot without needing the running process.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_FREEZE_FLAG = Path(os.environ.get("KIRA_FREEZE_FLAG") or "/home/exedev/webchat/.frozen")


def is_frozen() -> bool:
    """Cheap check; called on every /agent and /chat hit."""
    try:
        return _FREEZE_FLAG.exists()
    except OSError:
        return False


def freeze_info() -> dict:
    """Public snapshot of freeze state. No secrets."""
    if not is_frozen():
        return {"frozen": False}
    try:
        body = _FREEZE_FLAG.read_text(encoding="utf-8")
        data = json.loads(body) if body.strip().startswith("{") else {"reason": body.strip()}
    except Exception as e:
        data = {"reason": f"<unreadable: {type(e).__name__}>"}
    data["frozen"] = True
    return data


def freeze(reason: str = "") -> dict:
    """Create the freeze flag. Idempotent — overwrites existing reason."""
    payload = {"reason": reason or "unspecified", "at": time.time()}
    _FREEZE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    _FREEZE_FLAG.write_text(json.dumps(payload), encoding="utf-8")
    return {**payload, "frozen": True}


def unfreeze() -> dict:
    """Remove the flag. Idempotent — no-op if already missing."""
    try:
        _FREEZE_FLAG.unlink()
        return {"frozen": False, "unfrozen": True}
    except FileNotFoundError:
        return {"frozen": False, "unfrozen": False, "note": "was not frozen"}


def is_master_token(token: str | None) -> bool:
    """Master-token check for freeze/unfreeze endpoints.

    Priority: KIRA_MASTER_TOKEN env; falls back to the first comma-separated
    entry of KIRA_AUTH_TOKEN so single-token setups still work.
    Empty config = no master = freeze endpoints disabled (return False).
    """
    if not token:
        return False
    master = (os.environ.get("KIRA_MASTER_TOKEN") or "").strip()
    if not master:
        bag = (os.environ.get("KIRA_AUTH_TOKEN") or "").strip()
        master = bag.split(",")[0].strip() if bag else ""
    if not master:
        return False
    # Constant-time compare.
    import hmac
    return hmac.compare_digest(token, master)


def reason_for_sse() -> dict:
    """Shape used by run_agent SSE error frame when refusing a frozen request."""
    info = freeze_info()
    return {
        "type": "error",
        "code": "frozen",
        "message": f"Kira is frozen: {info.get('reason','no reason')}. Use POST /agent/unfreeze.",
    }
