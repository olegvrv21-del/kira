"""Create a GitHub branch + PR on behalf of Kira.

Uses the operator's `gh` CLI (already authenticated as olegvrv21-del with
scope `repo,workflow`). We deliberately do NOT shell out to git push from
the working tree — instead we work on a freshly-cloned scratch repo inside
/tmp, drop the files Kira wants, commit, push, and open the PR via `gh`.

Safety rails:
- Branch name is forced to `kira/<slug>` — Kira cannot push to main directly.
- Any file under `.github/workflows/` is rejected (workflow injection).
- Any file outside the repo (path traversal: ../) is rejected.
- File count cap (20) and total bytes cap (256 KiB).
- The PR is opened against `main`; merge requires a human.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = "olegvrv21-del/kira"
DEFAULT_BRANCH = "main"
MAX_FILES = 20
MAX_BYTES = 256 * 1024
ALLOWED_BRANCH = re.compile(r"^kira/[a-z0-9][a-z0-9._-]{0,60}$")


def _run(argv: list[str], cwd: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    r = subprocess.run(
        argv, capture_output=True, text=True, cwd=cwd, timeout=timeout
    )
    return r.returncode, r.stdout, r.stderr


def _validate(branch: str, files: dict[str, str]) -> str | None:
    if not ALLOWED_BRANCH.fullmatch(branch):
        return f"branch must match {ALLOWED_BRANCH.pattern} (got {branch!r})"
    if not files:
        return "files must not be empty"
    if len(files) > MAX_FILES:
        return f"too many files (max {MAX_FILES})"
    total = 0
    for path, content in files.items():
        if not isinstance(path, str) or not isinstance(content, str):
            return "each file entry must be {path: str, content: str}"
        if path.startswith("/") or ".." in path.split("/"):
            return f"invalid path {path!r}"
        if path.startswith(".github/workflows/") or path == ".github/workflows":
            return f"workflow files not allowed ({path})"
        b = content.encode("utf-8", errors="replace")
        total += len(b)
        if total > MAX_BYTES:
            return f"total size exceeds {MAX_BYTES} bytes"
    return None


def open_pr(
    *,
    branch: str,
    title: str,
    body: str,
    files: dict[str, str],
    base: str = DEFAULT_BRANCH,
) -> dict[str, Any]:
    """Create branch on origin and open a PR against `base`.

    Returns a dict with at least: ok, url (when ok), error (when not).
    """
    err = _validate(branch, files)
    if err:
        return {"ok": False, "error": err}

    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "title required"}

    # Pull token + clone URL via gh (no plain credentials on disk).
    rc, tok, terr = _run(["gh", "auth", "token"], timeout=5)
    if rc != 0 or not tok.strip():
        return {"ok": False, "error": f"gh auth token failed: {terr.strip() or rc}"}
    token = tok.strip()
    clone_url = f"https://x-access-token:{token}@github.com/{REPO}.git"

    tmp = tempfile.mkdtemp(prefix="kira-pr-")
    try:
        rc, _o, e = _run(
            ["git", "clone", "--depth", "1", "-b", base, clone_url, tmp], timeout=60
        )
        if rc != 0:
            return {"ok": False, "error": f"clone failed: {e.strip()[:300]}"}

        rc, _o, e = _run(["git", "checkout", "-b", branch], cwd=tmp)
        if rc != 0:
            return {"ok": False, "error": f"checkout failed: {e.strip()[:300]}"}

        # write files
        for relpath, content in files.items():
            p = Path(tmp) / relpath
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        _run(
            ["git", "-C", tmp, "config", "user.email", "kira@disk-photon.exe.xyz"]
        )
        _run(["git", "-C", tmp, "config", "user.name", "Kira Agent"])

        rc, _o, e = _run(["git", "-C", tmp, "add", "-A"])
        if rc != 0:
            return {"ok": False, "error": f"git add failed: {e.strip()[:300]}"}

        rc, out, e = _run(["git", "-C", tmp, "status", "--porcelain"])
        if rc == 0 and not out.strip():
            return {"ok": False, "error": "no changes — nothing to commit"}

        msg_first = title.splitlines()[0][:72]
        rc, _o, e = _run(
            ["git", "-C", tmp, "commit", "-m", msg_first, "-m", body or ""]
        )
        if rc != 0:
            return {"ok": False, "error": f"commit failed: {e.strip()[:300]}"}

        rc, _o, e = _run(
            ["git", "-C", tmp, "push", "-u", "origin", branch], timeout=60
        )
        if rc != 0:
            return {"ok": False, "error": f"push failed: {e.strip()[:300]}"}

        rc, out, e = _run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                REPO,
                "--head",
                branch,
                "--base",
                base,
                "--title",
                title,
                "--body",
                body or "_opened by Kira agent_",
                "--label",
                "kira-authored",
            ],
            timeout=30,
        )
        if rc != 0:
            return {"ok": False, "error": f"pr create failed: {e.strip()[:300]}"}
        url = out.strip().splitlines()[-1] if out.strip() else ""
        import re as _re
        m = _re.search(r"/pull/(\d+)", url)
        pr_number = int(m.group(1)) if m else None
        return {
            "ok": True,
            "url": url,
            "pr": pr_number,
            "branch": branch,
            "base": base,
            "duration_seconds": None,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
