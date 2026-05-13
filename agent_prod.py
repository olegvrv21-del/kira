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
