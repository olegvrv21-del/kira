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

import q_client
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
    reason = (rm.group(1).strip() if rm else "").splitlines()[0] if rm else ""
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
    """Run the critic on a diff. Returns parsed verdict dict."""
    if not diff or not diff.strip():
        return {"verdict": "OK", "reason": "empty diff", "issues": [], "raw": ""}
    model = model or _DEFAULT_MODEL
    body_diff = _truncate(diff, _MAX_DIFF)
    user = "Intent: " + (intent.strip() or "(not specified)")
    user += "\n\nDiff to review:\n```\n" + body_diff + "\n```"
    body = {
        "conversationState": {
            "chatTriggerType": "MANUAL",
            "currentMessage": {
                "userInputMessage": {
                    "content": user,
                    "userInputMessageContext": {},
                    "origin": "KIRO_CLI",
                    "modelId": model,
                }
            },
            "history": [
                {
                    "userInputMessage": {
                        "content": CRITIC_SYSTEM,
                        "userInputMessageContext": {},
                        "origin": "KIRO_CLI",
                        "modelId": model,
                    }
                }
            ],
        }
    }
    text_chunks: list[str] = []
    try:
        async for et, payload in q_client.stream_q(key_pool.current() or api_key, body, timeout=timeout):
            if et == "_throttle" or et == "_cancelled":
                continue
            if isinstance(payload, dict) and et == "assistantResponseEvent":
                c = payload.get("content", "")
                if c:
                    text_chunks.append(c)
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
