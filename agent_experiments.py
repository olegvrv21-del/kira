"""Read-only view of ~/notebook/experiments.tsv for the web UI.

autoresearch (skills/autoresearch.md) appends one row per experiment to a
tab-separated journal. Until now the only way to read it was `ssh ... cat`.
This module parses it for the /agent/experiments endpoint.

Format (8 cols, TAB-separated):
    ts<TAB>tag<TAB>idea<TAB>pr<TAB>status<TAB>ci<TAB>tests_after<TAB>notes

Hard-coded read-only path under KIRA_NOTEBOOK_DIR/experiments.tsv. No
writing from here — only the agent (via fs_write) and the autoresearch
skill ever write rows. Size cap 1 MiB, line cap 8000 rows; an autoresearch
journal larger than that means it's time to archive, not bloat the UI.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MAX_BYTES = 1 * 1024 * 1024
MAX_ROWS = 8000
COLUMNS = ("ts", "tag", "idea", "pr", "status", "ci", "tests_after", "notes")


def notebook_path() -> Path:
    root = Path(
        os.environ.get("KIRA_NOTEBOOK_DIR", str(Path.home() / "notebook"))
    ).resolve()
    return root / "experiments.tsv"


def load(path: Path | None = None) -> dict[str, Any]:
    """Parse experiments.tsv into a dict the frontend can render directly.

    Returns one of:
      {"ok": True, "rows": [...], "count": N, "path": "...", "truncated": bool}
      {"ok": False, "reason": "missing" | "too_large" | "empty" | "bad_header",
       "path": "..."}

    `rows` is a list of dicts keyed by COLUMNS. Header row is dropped.
    Lines with the wrong number of columns are skipped silently (recorded
    in the `skipped` counter). Whitespace inside fields is preserved
    except trailing newline.
    """
    p = path or notebook_path()
    info: dict[str, Any] = {"path": str(p)}

    if not p.exists():
        return {"ok": False, "reason": "missing", **info}

    try:
        size = p.stat().st_size
    except OSError as e:
        return {"ok": False, "reason": f"stat: {e}", **info}

    if size == 0:
        return {"ok": False, "reason": "empty", **info}
    if size > MAX_BYTES:
        return {"ok": False, "reason": "too_large", "size": size, **info}

    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "reason": f"read: {e}", **info}

    lines = raw.splitlines()
    if not lines:
        return {"ok": False, "reason": "empty", **info}

    header = lines[0].split("\t")
    # Be liberal: header order should match COLUMNS, but if not, fall back to
    # positional. Don't fail the whole load.
    if [c.strip() for c in header[: len(COLUMNS)]] != list(COLUMNS):
        # Still try to parse positionally so old/renamed headers don't break
        # the UI; flag it so the user knows.
        header_warning = f"unexpected header: {header!r}"
    else:
        header_warning = None

    rows: list[dict[str, str]] = []
    skipped = 0
    truncated = False
    for ln in lines[1:]:
        if len(rows) >= MAX_ROWS:
            truncated = True
            break
        if not ln.strip():
            continue
        parts = ln.split("\t")
        if len(parts) < len(COLUMNS):
            # Pad with empty strings so the row still renders — the
            # autoresearch skill sometimes writes a partial row on `opened`.
            parts = parts + [""] * (len(COLUMNS) - len(parts))
        elif len(parts) > len(COLUMNS):
            # Extra tabs in notes — join the tail back so we don't lose data.
            parts = parts[: len(COLUMNS) - 1] + ["\t".join(parts[len(COLUMNS) - 1 :])]
        rows.append(dict(zip(COLUMNS, parts)))

    out: dict[str, Any] = {
        "ok": True,
        "rows": rows,
        "count": len(rows),
        "skipped": skipped,
        "truncated": truncated,
        **info,
    }
    if header_warning:
        out["warning"] = header_warning
    return out
