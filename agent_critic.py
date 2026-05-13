"""Code critic: a second LLM agent that reviews diffs before commit.

Usage:
    verdict = await review_diff(api_key, diff, intent="add foo")
    # -> {"verdict": "OK"|"BLOCK", "reason": str, "issues": [str, ...]}

The critic uses a small/cheap model by default (env KIRA_CRITIC_MODEL,
default claude-haiku-4.5). It does NOT have tools — it is a pure text
reviewer that returns a structured verdict.

Auto-mode: if KIRA_CRITIC_AUTO=1, the agent runtime inserts a critic call
before any git_commit and denies the commit on BLOCK.
"""

from __future__ import annotations

import os
import re

from agent_keys import key_pool

_DEFAULT_MODEL = os.environ.get("KIRA_CRITIC_MODEL", "claude-haiku-4.5")
_AUTO = os.environ.get("KIRA_CRITIC_AUTO", "0") in ("1", "true", "True")
_MAX_DIFF = int(os.environ.get("KIRA_CRITIC_MAX_DIFF", "30000"))

CRITIC_SYSTEM = """You are a strict code reviewer ("the Critic") inside the Kira agent system.

You are given a code diff. Your job:
1. Look for OBVIOUS problems:
   - Secrets / API keys / passwords being committed.
   - Syntax errors or broken imports.
   - Removed tests or weakened assertions.
   - Dangerous shell ops (`rm -rf`, chmod 777, etc).
   - Hardcoded paths/URLs that look wrong for production.
   - Disabling security checks, hooks, or auth.
2. Decide a verdict.

Respond in EXACTLY this format, nothing else:

VERDICT: OK
or
VERDICT: BLOCK
REASON: <one short sentence>
ISSUES:
- <issue 1>
- <issue 2>

If VERDICT is OK you MAY still include ISSUES as advisory notes, but no REASON.
Do NOT include any explanation or chatter outside this format. Be concise.
"""


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n // 2] + "\n... [diff truncated] ...\n" + s[-n // 2 :]


def parse_verdict(text: str) -> dict:
    """Parse the critic's response into {verdict, reason, issues}."""
    t = (text or "").strip()
    m = re.search(r"VERDICT\s*:\s*(OK|BLOCK)", t, re.IGNORECASE)
    verdict = m.group(1).upper() if m else "OK"
    rm = re.search(r"REASON\s*:\s*(.+)", t)
    # Guard against whitespace-only REASON: lines. `"".splitlines()` is `[]`
    # so an unconditional `[0]` would raise IndexError and crash the caller
    # (agent_runtime's auto-critic path right before git_commit).
    reason = rm.group(1).strip().splitlines()[0] if rm and rm.group(1).strip() else ""
    issues = []
    in_issues = False
    for line in t.splitlines():
        if re.match(r"\s*ISSUES\s*:\s*$", line, re.IGNORECASE):
            in_issues = True
            continue
        if in_issues:
            s = line.strip()
            if s.startswith("-") or s.startswith("*"):
                issues.append(s.lstrip("-* ").strip())
            elif not s:
                continue
            else:
                break
    return {"verdict": verdict, "reason": reason, "issues": issues, "raw": t[:4000]}


async def review_diff(
    api_key: str, diff: str, *, intent: str = "", model: str | None = None, timeout: float = 60.0
) -> dict:
    """Run the critic on a diff. Returns parsed verdict dict.

    Routes through the `llm/` provider abstraction — KIRA_LLM_PROVIDER picks
    the backend (default: amazon-q). Previously this module imported q_client
    directly and built a Q-body by hand, which silently broke if anyone
    switched providers. Closes the last runtime-level Q-shape import.
    """
    if not diff or not diff.strip():
        return {"verdict": "OK", "reason": "empty diff", "issues": [], "raw": ""}
    model = model or _DEFAULT_MODEL
    body_diff = _truncate(diff, _MAX_DIFF)
    user_text = "Intent: " + (intent.strip() or "(not specified)")
    user_text += "\n\nDiff to review:\n```\n" + body_diff + "\n```"

    # Local imports keep `agent_critic` testable without httpx in the path.
    from llm import Message, get_provider

    provider_name = os.environ.get("KIRA_LLM_PROVIDER", "amazon-q")
    if provider_name == "amazon-q":
        # Use key_pool / passed-in key explicitly so prod key rotation still wins.
        from llm.q_provider import QProvider

        provider = QProvider(api_key=key_pool.current() or api_key)
    else:
        provider = get_provider(provider_name)

    messages = [
        Message(role="system", content=CRITIC_SYSTEM),
        Message(role="user", content=user_text),
    ]
    text_chunks: list[str] = []
    try:
        async for ev in provider.stream(messages, [], model=model, timeout=timeout):
            if ev.type == "text" and ev.text:
                text_chunks.append(ev.text)
            # throttle / usage / done / error / cancelled — ignored: the critic
            # is a best-effort advisor, not allowed to block on transient issues.
    except Exception as e:
        return {"verdict": "OK", "reason": "", "issues": [f"critic-error:{type(e).__name__}:{e}"], "raw": ""}
    full = "".join(text_chunks)
    return parse_verdict(full)


def is_auto_enabled() -> bool:
    return _AUTO


def reload_flags() -> None:
    global _AUTO, _DEFAULT_MODEL, _MAX_DIFF
    _AUTO = os.environ.get("KIRA_CRITIC_AUTO", "0") in ("1", "true", "True")
    _DEFAULT_MODEL = os.environ.get("KIRA_CRITIC_MODEL", "claude-haiku-4.5")
    _MAX_DIFF = int(os.environ.get("KIRA_CRITIC_MAX_DIFF", "30000"))
