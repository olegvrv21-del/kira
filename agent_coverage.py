"""Read coverage.json (produced by pytest-cov) and expose a summary endpoint.

The project itself runs pytest --cov in CI (and manually). Result is
`coverage.json` at repo root. We don't run the tests inside the server
process — we just surface whatever was last written, so Kira can introspect
her own testing gaps.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
COVERAGE_JSON = ROOT / "coverage.json"


def _load() -> dict[str, Any] | None:
    if not COVERAGE_JSON.is_file():
        return None
    try:
        return json.loads(COVERAGE_JSON.read_text())
    except Exception:
        return None


def status() -> dict[str, Any]:
    """Return a UI-friendly summary of the last coverage run."""
    raw = _load()
    if not raw:
        return {"ok": False, "error": "coverage.json not found — run `make coverage`"}
    meta = raw.get("meta") or {}
    totals = raw.get("totals") or {}
    files_raw = raw.get("files") or {}
    files = []
    for path, info in files_raw.items():
        s = info.get("summary") or {}
        stmts = int(s.get("num_statements") or 0)
        missing = int(s.get("missing_lines") or 0)
        covered = int(s.get("covered_lines") or 0)
        pct = float(s.get("percent_covered") or 0.0)
        files.append(
            {
                "path": path,
                "statements": stmts,
                "covered": covered,
                "missing": missing,
                "percent": round(pct, 1),
            }
        )
    files.sort(key=lambda f: (f["percent"], -f["statements"]))
    return {
        "ok": True,
        "timestamp": meta.get("timestamp"),
        "age_seconds": max(0, int(time.time() - COVERAGE_JSON.stat().st_mtime)),
        "total_percent": round(float(totals.get("percent_covered") or 0.0), 1),
        "total_statements": int(totals.get("num_statements") or 0),
        "total_covered": int(totals.get("covered_lines") or 0),
        "total_missing": int(totals.get("missing_lines") or 0),
        "files": files,
    }


def file_detail(path: str) -> dict[str, Any]:
    raw = _load()
    if not raw:
        return {"ok": False, "error": "coverage.json not found"}
    info = (raw.get("files") or {}).get(path)
    if not info:
        return {"ok": False, "error": f"no coverage entry for {path!r}"}
    s = info.get("summary") or {}
    return {
        "ok": True,
        "path": path,
        "executed_lines": info.get("executed_lines") or [],
        "missing_lines": info.get("missing_lines") or [],
        "excluded_lines": info.get("excluded_lines") or [],
        "summary": {
            "statements": int(s.get("num_statements") or 0),
            "covered": int(s.get("covered_lines") or 0),
            "missing": int(s.get("missing_lines") or 0),
            "percent": round(float(s.get("percent_covered") or 0.0), 1),
        },
    }


def run(timeout: int = 120) -> dict[str, Any]:
    """Run pytest with coverage, refresh coverage.json. Blocks until done.

    Disabled by default unless KIRA_COVERAGE_ALLOW_RUN=1 because running
    the whole suite synchronously inside the request thread is rude.
    """
    if os.environ.get("KIRA_COVERAGE_ALLOW_RUN") != "1":
        return {"ok": False, "error": "set KIRA_COVERAGE_ALLOW_RUN=1 to enable"}
    venv = ROOT / ".venv" / "bin" / "pytest"
    pytest_bin = str(venv) if venv.is_file() else "pytest"
    cmd = [pytest_bin, "--cov=.", "--cov-report=json:coverage.json", "--cov-report=term", "-q"]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "duration_seconds": round(time.time() - t0, 2),
        "stdout_tail": (proc.stdout or "").splitlines()[-25:],
        "stderr_tail": (proc.stderr or "").splitlines()[-10:],
    }
