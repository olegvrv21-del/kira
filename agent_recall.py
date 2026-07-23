"""Automatic memory recall — Kira starts each request already remembering.

The memory system (agent_memory) is powerful but *passive*: it only helps when
the model explicitly calls `memory_search`. That's the "10% brain" problem —
the knowledge exists but isn't integrated into thinking automatically.

This module closes that gap. At the start of an /agent call we search long-term
memory with the user's prompt and inject the most relevant snippets as a single
system message, so the model *begins* with relevant context instead of having to
remember to look. The explicit `memory_search` tool still exists for deeper,
query-specific lookups mid-task; this just guarantees a baseline of recall.

Design constraints:
- Cheap: reuses the local BM25 index (no LLM, no network).
- Bounded: at most KIRA_RECALL_K snippets, each truncated, total capped.
- Once per call: injected before the turn loop, not per turn (no token bloat).
- Fail-open: any error → no recall, request proceeds normally.
- Transparent: caller can surface a `recall` event so the user sees what was
  remembered.

Env:
  KIRA_AUTO_RECALL=1          master switch (default on)
  KIRA_RECALL_K=3             max snippets injected (default 3)
  KIRA_RECALL_MIN_SCORE=0.0   drop hits below this score (default 0.0 = keep all ranked)
  KIRA_RECALL_MAX_CHARS=1600  hard cap on the injected block
"""

from __future__ import annotations

import os


def _int(env: str, default: int) -> int:
    try:
        return int(os.environ.get(env, str(default)))
    except ValueError:
        return default


def _float(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env, str(default)))
    except ValueError:
        return default


def enabled() -> bool:
    return os.environ.get("KIRA_AUTO_RECALL", "1") not in ("", "0", "false", "False")


def recall(prompt: str, *, memory=None) -> tuple[str | None, list[dict]]:
    """Return (system_block, hits). system_block is a formatted memory context
    string to inject, or None if nothing relevant / disabled. hits is the raw
    list of memory results (for surfacing a recall event).

    `memory` is injectable for tests; defaults to the shared agent_memory index.
    """
    if not enabled():
        return None, []
    q = (prompt or "").strip()
    if len(q) < 3:
        return None, []

    if memory is None:
        try:
            from agent_memory import memory as _mem
            memory = _mem
        except Exception:
            return None, []

    k = max(1, _int("KIRA_RECALL_K", 3))
    min_score = _float("KIRA_RECALL_MIN_SCORE", 0.0)
    max_chars = _int("KIRA_RECALL_MAX_CHARS", 1600)

    try:
        hits = memory.search(q, k=k)
    except Exception:
        return None, []

    hits = [h for h in hits if float(h.get("score", 0)) > min_score]
    if not hits:
        return None, []

    lines = [
        "## Relevant memory (auto-recalled)",
        "The following notes from long-term memory may be relevant to the "
        "current request. Use them if helpful; ignore if not.",
        "",
    ]
    used = 0
    included: list[dict] = []
    for h in hits:
        snippet = (h.get("snippet") or "").strip()
        if not snippet:
            continue
        head = h.get("heading") or ""
        src = h.get("file") or "?"
        entry = f"- [{src}{(' · ' + head) if head else ''}]\n  {snippet}"
        if used + len(entry) > max_chars:
            # Trim the last entry to fit rather than dropping it entirely.
            remaining = max_chars - used
            if remaining > 120:
                entry = entry[:remaining] + " …"
                lines.append(entry)
                included.append(h)
            break
        lines.append(entry)
        included.append(h)
        used += len(entry)

    if not included:
        return None, []
    return "\n".join(lines), included
