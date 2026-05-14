"""Read-only production observation endpoints + helpers.

Exposes a tiny, whitelisted set of host commands so Kira can answer
operator-level questions ("are there errors in the journal?", "how much
disk free?", "what was the last commit?") without anyone wiring up SSH
or giving her sudo.

Whitelisted subprocesses only — no shell, no user input is interpolated
into argv (only numeric `lines` / `n`, validated). Every call has a hard
timeout and a hard output cap.

Used by:
- HTTP endpoints `/agent/prod/*` (auth-gated by KIRA_AUTH_TOKEN)
- the `prod_observe` tool, which fetches those endpoints from the sandbox
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
MAX_OUTPUT = 64_000  # bytes
TIMEOUT = 10  # seconds


def _run(argv: list[str], timeout: int = TIMEOUT) -> dict[str, Any]:
    t0 = time.time()
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s", "argv": argv}
    out = (r.stdout or "")[:MAX_OUTPUT]
    err = (r.stderr or "")[:4_000]
    return {
        "ok": r.returncode == 0,
        "returncode": r.returncode,
        "stdout": out,
        "stderr": err,
        "duration_seconds": round(time.time() - t0, 3),
    }


# ---------------------------------------------------------------------------
# Whitelisted observations
# ---------------------------------------------------------------------------


def uptime() -> dict[str, Any]:
    """Combine `uptime` + `free -h` for a one-shot host pulse."""
    a = _run(["uptime"])
    b = _run(["free", "-h"])
    return {"uptime": a, "memory": b}


def df() -> dict[str, Any]:
    """Disk free for root and /home."""
    return _run(["df", "-h", "/", "/home"])


def systemctl_status(unit: str = "webchat") -> dict[str, Any]:
    if unit not in {"webchat", "kira-vault-sync.timer", "kira-disk-clean.timer"}:
        return {"ok": False, "error": f"unit {unit!r} not in whitelist"}
    return _run(["systemctl", "status", unit, "--no-pager", "-n", "20"])


def journalctl(lines: int = 50, grep: str | None = None) -> dict[str, Any]:
    """Tail of webchat journal. `grep` filters case-insensitively in-process."""
    lines = max(1, min(int(lines), 500))
    r = _run(
        ["journalctl", "-u", "webchat", "--no-pager", "-n", str(lines)],
        timeout=15,
    )
    if grep and r.get("ok"):
        g = grep.lower()
        r["stdout"] = "\n".join(
            line for line in r["stdout"].splitlines() if g in line.lower()
        )
    return r


def git_log(n: int = 10) -> dict[str, Any]:
    n = max(1, min(int(n), 50))
    return _run(
        ["git", "-C", str(ROOT), "log", f"-{n}", "--pretty=format:%h %s (%cr)"]
    )


def git_diff(ref: str = "HEAD~1") -> dict[str, Any]:
    """Diff against a ref. Ref must be alnum/. only to avoid shell injection,
    even though we don't use a shell (defence in depth)."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_./~^-]{1,80}", ref or ""):
        return {"ok": False, "error": f"invalid ref {ref!r}"}
    return _run(["git", "-C", str(ROOT), "diff", "--stat", ref])


REPO = "olegvrv21-del/kira"


def ci_status(pr: int) -> dict[str, Any]:
    """Read-only CI status for a Kira PR.

    Returns a compact dict the agent can reason about:
      {
        "ok": True,
        "pr": 26,
        "title": "...",
        "state": "OPEN" | "MERGED" | "CLOSED",
        "rollup": "green" | "red" | "pending" | "mixed" | "none",
        "checks": [{"name": ..., "conclusion": ..., "status": ..., "url": ...}],
        "n_pass": 5, "n_fail": 0, "n_pending": 0,
      }

    Conclusion strings from GitHub: SUCCESS / FAILURE / CANCELLED / SKIPPED /
    TIMED_OUT / NEUTRAL / ACTION_REQUIRED / null (pending).

    Mapping for the "rollup" field:
      - any FAILURE / CANCELLED / TIMED_OUT / ACTION_REQUIRED → "red"
      - any pending (status != COMPLETED) → "pending"
      - all SUCCESS / SKIPPED / NEUTRAL → "green"
      - empty checks list → "none"
      - otherwise → "mixed"

    Argument is strictly int — no shell interpolation possible.
    """
    import json as _j
    import re

    try:
        n = int(pr)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"pr must be int, got {pr!r}"}
    if n <= 0 or n > 10_000_000:
        return {"ok": False, "error": f"pr out of range: {n}"}

    # Defence in depth: REPO is a constant, but verify the shape.
    if not re.fullmatch(r"[A-Za-z0-9_./-]{3,80}", REPO):
        return {"ok": False, "error": "REPO constant tampered"}

    r = _run(
        ["gh", "pr", "view", str(n),
         "--repo", REPO,
         "--json", "number,title,state,statusCheckRollup"],
        timeout=15,
    )
    if not r.get("ok"):
        return {"ok": False, "error": r.get("stderr") or r.get("error") or "gh failed", "raw": r}

    try:
        data = _j.loads(r["stdout"])
    except Exception as e:
        return {"ok": False, "error": f"json parse: {e}", "raw_stdout": r["stdout"][:500]}

    checks_raw = data.get("statusCheckRollup") or []
    n_pass = n_fail = n_pending = n_skip = 0
    compact_checks = []
    for c in checks_raw:
        name = c.get("name") or c.get("workflowName") or "?"
        concl = (c.get("conclusion") or "").upper()
        status = (c.get("status") or "").upper()
        compact_checks.append({
            "name": name,
            "conclusion": concl or None,
            "status": status or None,
            "url": c.get("detailsUrl") or c.get("targetUrl") or "",
        })
        if status and status != "COMPLETED":
            n_pending += 1
        elif concl in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"):
            n_fail += 1
        elif concl in ("SUCCESS",):
            n_pass += 1
        elif concl in ("SKIPPED", "NEUTRAL"):
            n_skip += 1
        else:
            n_pending += 1

    if not checks_raw:
        rollup = "none"
    elif n_fail > 0:
        rollup = "red"
    elif n_pending > 0:
        rollup = "pending"
    elif n_pass > 0 and (n_pass + n_skip == len(checks_raw)):
        rollup = "green"
    else:
        rollup = "mixed"

    return {
        "ok": True,
        "pr": n,
        "title": data.get("title", ""),
        "state": data.get("state", ""),
        "rollup": rollup,
        "n_pass": n_pass,
        "n_fail": n_fail,
        "n_pending": n_pending,
        "n_skip": n_skip,
        "checks": compact_checks,
    }
