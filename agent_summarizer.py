"""Summarization middleware for long sessions (DeerFlow-inspired).

When the canonical message list grows past a token budget, we replace the
*middle* part (everything except the system prompt and the last K messages)
with a single synthetic system message containing an LLM-generated summary.
This lets a session run indefinitely without blowing context.

Critical invariant: never split a tool_use / tool_result pair across the
summarization boundary. The model expects every assistant.tool_calls[X] to
be followed (eventually) by a tool message with tool_call_id=X. If we drop
the assistant turn but keep the tool result (or vice versa), the next
provider call 400s with "tool_use ids without tool_result".

Env:
  KIRA_SUMMARIZE=0           disable
  KIRA_SUMMARIZE_THRESHOLD   chars to trigger (default 32000 ≈ 8k tokens)
  KIRA_SUMMARIZE_KEEP        recent messages preserved (default 6)
  KIRA_SUMMARIZE_MODEL       summary model (default haiku)

Public API:
  estimate_chars(messages) -> int
  should_summarize(messages, threshold=None) -> bool
  pick_summarize_range(messages, keep) -> tuple[int, int] | None
  build_summary_prompt(messages, lo, hi) -> str
  async summarize(messages, llm_one_shot, model="haiku", api_key="",
                  threshold=None, keep=None) -> bool
"""

from __future__ import annotations

import os
from typing import Any
from collections.abc import Awaitable, Callable

DEFAULT_THRESHOLD = 32000  # ~8k tokens at 4 chars/token
DEFAULT_KEEP = 6


def _is_disabled() -> bool:
    return os.environ.get("KIRA_SUMMARIZE", "1") == "0"


def _msg_chars(m: Any) -> int:
    """Rough char count for one canonical Message."""
    c = getattr(m, "content", "") or ""
    n = 0
    if isinstance(c, str):
        n = len(c)
    elif isinstance(c, list):
        for part in c:
            if isinstance(part, dict):
                t = part.get("text") or part.get("content") or ""
                if isinstance(t, str):
                    n += len(t)
    # Tool call arguments + tool name
    tcs = getattr(m, "tool_calls", None) or []
    for tc in tcs:
        try:
            import json as _j
            n += len(_j.dumps(tc.arguments, ensure_ascii=False)) + len(tc.name or "")
        except Exception:
            pass
    name = getattr(m, "name", None) or ""
    n += len(name)
    return n


def estimate_chars(messages: list) -> int:
    """Total characters across all messages (rough token proxy)."""
    return sum(_msg_chars(m) for m in messages)


def should_summarize(messages: list, threshold: int | None = None) -> bool:
    if _is_disabled():
        return False
    if not messages or len(messages) < 4:
        return False
    th = threshold if threshold is not None else int(
        os.environ.get("KIRA_SUMMARIZE_THRESHOLD", str(DEFAULT_THRESHOLD))
    )
    return estimate_chars(messages) >= th


def _open_tool_call_ids(messages: list, up_to: int) -> set[str]:
    """tool_call ids that were emitted by an assistant turn in messages[:up_to]
    but do NOT yet have a matching tool message in the same slice."""
    opened: set[str] = set()
    for m in messages[:up_to]:
        if m.role == "assistant":
            for tc in (m.tool_calls or []):
                opened.add(tc.id)
        elif m.role == "tool" and m.tool_call_id:
            opened.discard(m.tool_call_id)
    return opened


def pick_summarize_range(messages: list, keep: int) -> tuple[int, int] | None:
    """Pick (lo, hi) such that messages[lo:hi] can be replaced by one summary.

    Rules:
    - lo=1 (always keep messages[0] which is the system prompt).
    - hi starts at len(messages)-keep, then we ENLARGE the window if needed
      to make sure every assistant.tool_calls inside [lo:hi] has its matching
      tool result also inside [lo:hi]. This means tool pairs are always kept
      together (either both summarized away, or both kept).
    - If hi reaches len(messages), we don't summarize this round (no recent
      window to keep). Caller should retry later.
    - If the system message itself opens tool_calls (it shouldn't, but defensive),
      we abort.

    Returns None if no valid range exists.
    """
    n = len(messages)
    if n <= keep + 1:
        return None
    if not messages or messages[0].role != "system":
        return None

    lo = 1
    hi = max(lo + 1, n - keep)

    # Walk hi forward while the slice [lo:hi] has open (un-paired) tool calls,
    # i.e. assistant emitted tool_use but the matching tool_result is at >=hi.
    while hi < n:
        opened = _open_tool_call_ids(messages, hi)
        if not opened:
            break
        hi += 1

    # If hi reached n, we ate everything — nothing recent to keep.
    if hi >= n:
        return None
    # Also: never strand a leading tool message at index hi (one whose pair
    # is in [lo:hi]). Walk hi forward over any tool-role messages whose
    # tool_call_id was already summarized away.
    summarized_call_ids: set[str] = set()
    for m in messages[lo:hi]:
        for tc in (m.tool_calls or []):
            summarized_call_ids.add(tc.id)
    while hi < n and messages[hi].role == "tool" and messages[hi].tool_call_id in summarized_call_ids:
        hi += 1
    if hi >= n:
        return None

    # Don't bother if the range is tiny.
    if hi - lo < 2:
        return None
    return (lo, hi)


def _msg_preview(m: Any, limit: int = 600) -> str:
    role = getattr(m, "role", "?")
    c = getattr(m, "content", "") or ""
    if isinstance(c, list):
        parts = []
        for p in c:
            if isinstance(p, dict):
                t = p.get("text") or p.get("content") or ""
                if isinstance(t, str):
                    parts.append(t)
        c = "\n".join(parts)
    text = c if isinstance(c, str) else str(c)
    tcs = getattr(m, "tool_calls", None) or []
    if tcs:
        tnames = ", ".join(f"{tc.name}({_short_args(tc.arguments)})" for tc in tcs)
        text = (text + "\n" if text else "") + f"[tool_calls: {tnames}]"
    if role == "tool":
        text = f"[tool_result name={getattr(m, 'name', '?')}] " + text
    text = text.strip()
    if len(text) > limit:
        text = text[: limit - 20] + "... [truncated]"
    return f"{role.upper()}: {text}"


def _short_args(args: dict) -> str:
    try:
        import json as _j
        s = _j.dumps(args, ensure_ascii=False)
    except Exception:
        s = str(args)
    return s[:120] + ("..." if len(s) > 120 else "")


def build_summary_prompt(messages: list, lo: int, hi: int) -> str:
    """Build the prompt fed to the summarizer LLM."""
    parts = [_msg_preview(m) for m in messages[lo:hi]]
    joined = "\n\n".join(parts)
    return (
        "Summarise the following chat segment in 150-250 words. "
        "Match the original language (Russian or English). "
        "Preserve concrete facts: file paths, function names, SHA hashes, "
        "command names, numeric results, decisions made, files modified. "
        "Omit chit-chat, emojis, repeated boilerplate. "
        "Do NOT invent details. "
        "Write a single dense paragraph, no bullet list.\n\n"
        "Segment:\n"
        f"{joined}\n\n"
        "Summary:"
    )


async def summarize(
    messages: list,
    llm_one_shot: Callable[..., Awaitable[str]],
    model: str | None = None,
    api_key: str = "",
    threshold: int | None = None,
    keep: int | None = None,
) -> bool:
    """If messages exceed threshold, replace the middle with one summary message.

    Returns True if a summarization happened (messages was mutated), False
    otherwise. Safe to call every turn — does nothing if disabled or below
    threshold.
    """
    if _is_disabled():
        return False
    if not should_summarize(messages, threshold):
        return False
    keep_n = keep if keep is not None else int(
        os.environ.get("KIRA_SUMMARIZE_KEEP", str(DEFAULT_KEEP))
    )
    rng = pick_summarize_range(messages, keep_n)
    if rng is None:
        return False
    lo, hi = rng
    prompt = build_summary_prompt(messages, lo, hi)
    mdl = model or os.environ.get("KIRA_SUMMARIZE_MODEL", "haiku")
    try:
        summary = await llm_one_shot(api_key, prompt, mdl, system=None, max_tokens=512)
    except Exception:
        return False
    if not summary or summary.startswith("[llm_one_shot error]") or "(empty response)" in summary:
        return False
    summary = summary.strip()
    if not summary or len(summary) < 30:
        return False

    # Build the replacement message in-place. We import lazily to avoid a
    # hard cycle: llm.base is the lighter module but we still want to keep
    # this file dependency-free for tests that mock Message-shaped objects.
    from llm.base import Message
    n_summarized = hi - lo
    synthetic = Message(
        role="system",
        content=(
            f"[Earlier conversation summary covering {n_summarized} prior messages — "
            f"generated by {mdl}]:\n\n{summary}"
        ),
    )
    messages[lo:hi] = [synthetic]
    return True
