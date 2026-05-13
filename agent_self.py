"""Self-introspection utilities for Kira.

Exposes a single `status()` function that returns a snapshot of *what
this Kira is right now*: the deployed git SHA, last few commits, pytest
coverage summary (from coverage.json), in-flight agent sessions, process
uptime, RU/EN system prompt fragment, and the list of available tools.

Designed to be cheap (no network, no shelling out beyond `git log`) and
safe to call from inside the sandbox via the `self_status` tool, as well
as from the HTTP layer for /agent/self.

We deliberately do NOT expose secrets (no env vars, no auth tokens, no
key-pool contents — those live in /agent/health for the operator).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _git(args: list[str], timeout: int = 5) -> str:
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=timeout
        )
        if r.returncode != 0:
            return ""
        return r.stdout.strip()
    except Exception:
        return ""


def _git_log(n: int = 5) -> list[dict[str, str]]:
    out = _git(["log", f"-{n}", "--pretty=format:%h\x1f%s\x1f%cr"])
    if not out:
        return []
    entries = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            entries.append({"sha": parts[0], "subject": parts[1], "when": parts[2]})
    return entries


def _coverage_brief() -> dict[str, Any]:
    try:
        import agent_coverage

        st = agent_coverage.status()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not st.get("ok"):
        return {"ok": False, "error": st.get("error")}
    return {
        "ok": True,
        "total_percent": st["total_percent"],
        "total_statements": st["total_statements"],
        "total_covered": st["total_covered"],
        "age_seconds": st["age_seconds"],
        "files_count": len(st.get("files", [])),
    }


def _test_count() -> int | None:
    """Try to count tests cheaply by globbing test_*.py and counting def test_*."""
    try:
        import re

        total = 0
        pat = re.compile(r"^\s*(?:async\s+)?def\s+test_\w+", re.MULTILINE)
        for p in (ROOT / "tests").rglob("test_*.py"):
            try:
                total += len(pat.findall(p.read_text(encoding="utf-8", errors="ignore")))
            except Exception:
                pass
        return total
    except Exception:
        return None


def _in_flight() -> dict[str, Any]:
    try:
        import agent_runtime

        sids = list(agent_runtime._CANCEL_EVENTS.keys())
        return {"count": len(sids), "sids": sids[:10]}
    except Exception:
        return {"count": 0, "sids": []}


def _tool_names() -> list[str]:
    """Return the list of tool names known to the host runtime."""
    try:
        import agent_tools

        return sorted(agent_tools.TOOLS.keys())
    except Exception:
        return []


def status(start_ts: float | None = None) -> dict[str, Any]:
    """Snapshot of this Kira instance. Cheap (~few ms).

    start_ts is the process start unix time; if None, uptime is omitted.
    """
    now = time.time()
    head = _git(["rev-parse", "--short", "HEAD"])
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    dirty = bool(_git(["status", "--porcelain"]))
    cov = _coverage_brief()
    tests = _test_count()
    in_flight = _in_flight()
    res: dict[str, Any] = {
        "name": "Kira",
        "host": os.uname().nodename if hasattr(os, "uname") else "",
        "root": str(ROOT),
        "git": {
            "branch": branch or "?",
            "head": head or "?",
            "dirty": dirty,
            "recent": _git_log(5),
        },
        "tests": {
            "count": tests,
        },
        "coverage": cov,
        "in_flight": in_flight,
        "tools": _tool_names(),
        "sandbox": os.environ.get("KIRA_SANDBOX") == "1",
        "self_edit": os.environ.get("KIRA_SELF_EDIT", "1") not in ("", "0", "false", "False"),
    }
    if start_ts is not None:
        res["uptime_seconds"] = int(now - start_ts)
    return res


def status_text(start_ts: float | None = None) -> str:
    """Human-readable single-shot summary for LLM/tool output."""
    s = status(start_ts=start_ts)
    g = s["git"]
    cov = s["coverage"]
    lines = [
        f"I am Kira on {s['host']} ({s['root']})",
        f"  git: branch={g['branch']} head={g['head']}{' (dirty)' if g['dirty'] else ''}",
        f"  tests: {s['tests']['count']}",
    ]
    if cov.get("ok"):
        lines.append(
            f"  coverage: {cov['total_percent']}% ({cov['total_covered']}/{cov['total_statements']} stmts, age {cov['age_seconds']}s, {cov['files_count']} files)"
        )
    else:
        lines.append(f"  coverage: {cov.get('error')}")
    lines.append(f"  in_flight: {s['in_flight']['count']} sessions")
    if s.get("uptime_seconds") is not None:
        up = s["uptime_seconds"]
        h, rem = divmod(up, 3600)
        m, ss = divmod(rem, 60)
        lines.append(f"  uptime: {h}h{m}m{ss}s")
    lines.append(f"  sandbox={s['sandbox']} self_edit={s['self_edit']} tools={len(s['tools'])}")
    if g["recent"]:
        lines.append("  recent commits:")
        for c in g["recent"]:
            lines.append(f"    {c['sha']}  {c['subject']}  ({c['when']})")
    return "\n".join(lines)
