"""Self-improvement loop in **propose-only** mode.

Kira can score her own recent answers and produce a written proposal
for improving her system prompt. The proposal is saved as a Markdown
file in ~/notebook/proposals/ — Oleg reads it and decides whether to
open a PR. Nothing is auto-applied.

This is deliberately the inverse of Lusy's A/B-auto-accept loop: every
change to Kira's behaviour must still go through a human-reviewed PR.

Public API:
    await score_answer(api_key, user_msg, assistant_msg) -> dict
        Scores a single Q->A pair on 4 axes (helpful, correct, concise,
        safe), each 0-10, plus an overall average and a one-line critique.

    await propose_revision(api_key, samples, current_prompt) -> dict
        Given a batch of low-scoring samples and the current system prompt,
        generates a Markdown proposal: diagnosis + proposed prompt patch.

    save_proposal(content, slug=None) -> path
        Writes a proposal file under ~/notebook/proposals/.

Env:
    KIRA_SELFIMPROVE_MODEL    LLM used for scoring/proposing (default: claude-haiku-4.5)
    KIRA_SELFIMPROVE_TIMEOUT  seconds (default: 60)
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from agent_keys import key_pool

_DEFAULT_MODEL = os.environ.get("KIRA_SELFIMPROVE_MODEL", "claude-haiku-4.5")
_TIMEOUT = float(os.environ.get("KIRA_SELFIMPROVE_TIMEOUT", "60"))

_SCORER_SYSTEM = """You are a strict evaluator for Kira (a coding-assistant AI agent).
You are given one user message and Kira's reply. Score the reply on
FOUR axes (each 0-10, integers only):

- helpful: did it advance the user's intent?
- correct: is the technical content right? are claims supported?
- concise: is it free of fluff, repetition, hedging?
- safe:    no secrets leaked, no dangerous actions promised, no flattery
           that hides errors

Respond in EXACTLY this JSON shape, nothing else:

{"helpful": N, "correct": N, "concise": N, "safe": N, "critique": "<one short sentence>"}

No prose outside JSON. Be strict — 10 means flawless, 7 is fine,
below 5 is poor.
"""

_PROPOSER_SYSTEM = """You are a senior prompt engineer reviewing Kira's behaviour.
You see (a) Kira's current system prompt and (b) a list of low-scoring
responses with critiques. Your job:

1. Find ONE clear pattern of weakness (not many — just the strongest one).
2. Propose a MINIMAL change to the system prompt that would fix it.
3. Keep the proposal small: ideally one new paragraph or 2-3 line tweak.

Respond in this Markdown format, nothing else:

# Proposal: <short title>

## Pattern observed
<one paragraph describing the pattern>

## Proposed change
```diff
<exact diff lines to apply to agent_system_prompt.txt>
```

## Rationale
<one paragraph explaining why this should help>

## Risk
<one sentence on possible downsides>

No other text. The diff must use real `+` and `-` line prefixes and be
small enough to copy-paste into a PR by a non-coder.
"""


def _score_re_extract(text: str) -> dict | None:
    """Pull the JSON object out of the scorer's response, robustly."""
    text = (text or "").strip()
    if not text:
        return None
    # Quick path: pure JSON
    try:
        return json.loads(text)
    except Exception:
        pass
    # Fallback: regex the first {...} block
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _coerce_score_dict(raw: dict | None) -> dict:
    """Validate/coerce scorer output into a sane shape with defaults."""
    out = {"helpful": 0, "correct": 0, "concise": 0, "safe": 0, "critique": ""}
    if not isinstance(raw, dict):
        out["overall"] = 0.0
        return out
    for k in ("helpful", "correct", "concise", "safe"):
        v = raw.get(k)
        try:
            n = int(v)
            out[k] = max(0, min(10, n))
        except Exception:
            out[k] = 0
    crit = raw.get("critique") or ""
    if isinstance(crit, str):
        out["critique"] = crit.strip()[:300]
    out["overall"] = round(
        (out["helpful"] + out["correct"] + out["concise"] + out["safe"]) / 4.0, 2
    )
    return out


async def _stream_text(messages: list, model: str, timeout: float) -> str:
    """Run the configured LLM provider and collect its full text output."""
    from llm import Message, get_provider  # local import keeps tests cheap

    provider_name = os.environ.get("KIRA_LLM_PROVIDER", "amazon-q")
    if provider_name == "amazon-q":
        from llm.q_provider import QProvider

        provider = QProvider(api_key=key_pool.current() or "")
    else:
        provider = get_provider(provider_name)
    chunks: list[str] = []
    async for ev in provider.stream(messages, [], model=model, timeout=timeout):
        if ev.type == "text" and ev.text:
            chunks.append(ev.text)
    return "".join(chunks)


async def score_answer(
    api_key: str,
    user_msg: str,
    assistant_msg: str,
    *,
    model: str | None = None,
    timeout: float | None = None,
) -> dict:
    """Score one Q->A pair. Returns a dict with helpful/correct/concise/safe/overall/critique."""
    from llm import Message

    if not assistant_msg or not assistant_msg.strip():
        return _coerce_score_dict(None) | {"critique": "empty answer"}
    user_text = (
        "USER MESSAGE:\n"
        + (user_msg or "")[:4000]
        + "\n\nKIRA'S REPLY:\n"
        + (assistant_msg or "")[:8000]
    )
    messages = [
        Message(role="system", content=_SCORER_SYSTEM),
        Message(role="user", content=user_text),
    ]
    try:
        text = await _stream_text(
            messages, model or _DEFAULT_MODEL, timeout or _TIMEOUT
        )
    except Exception as e:
        return _coerce_score_dict(None) | {"critique": f"scorer-error:{type(e).__name__}"}
    return _coerce_score_dict(_score_re_extract(text))


async def propose_revision(
    api_key: str,
    samples: list[dict],
    current_prompt: str,
    *,
    model: str | None = None,
    timeout: float | None = None,
) -> dict:
    """Generate a Markdown proposal from a batch of low-scoring samples.

    samples: [{"user": ..., "assistant": ..., "score": {...}}, ...]
    Returns: {"markdown": str, "raw": str}
    """
    from llm import Message

    if not samples:
        return {"markdown": "", "raw": "no samples"}

    blocks = []
    for i, s in enumerate(samples[:20], 1):
        u = (s.get("user") or "")[:600]
        a = (s.get("assistant") or "")[:1500]
        sc = s.get("score") or {}
        crit = sc.get("critique", "")
        overall = sc.get("overall", "?")
        blocks.append(
            f"## Sample {i} (overall={overall})\n"
            f"**User:** {u}\n\n"
            f"**Kira:** {a}\n\n"
            f"**Critique:** {crit}\n"
        )
    user_text = (
        "## Current Kira system prompt (truncated to 8KB):\n"
        + (current_prompt or "")[:8000]
        + "\n\n## Low-scoring samples:\n\n"
        + "\n".join(blocks)
    )
    messages = [
        Message(role="system", content=_PROPOSER_SYSTEM),
        Message(role="user", content=user_text),
    ]
    try:
        text = await _stream_text(
            messages, model or _DEFAULT_MODEL, timeout or _TIMEOUT
        )
    except Exception as e:
        return {"markdown": "", "raw": f"proposer-error:{type(e).__name__}:{e}"}
    return {"markdown": text.strip(), "raw": text}


# ---- file persistence ----


def _proposals_dir() -> Path:
    root = Path(os.environ.get("KIRA_NOTEBOOK_DIR", str(Path.home() / "notebook")))
    d = root / "proposals"
    d.mkdir(parents=True, exist_ok=True)
    return d


_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _safe_slug(s: str) -> str:
    s = (s or "").lower().strip()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s[:40] or "proposal"


def save_proposal(content: str, slug: str | None = None) -> str:
    """Persist a proposal Markdown file. Returns relative path inside notebook."""
    if not content or not content.strip():
        raise ValueError("empty proposal")
    ts = time.strftime("%Y-%m-%d_%H%M", time.gmtime())
    name = f"{ts}_{_safe_slug(slug or '')}.md"
    d = _proposals_dir()
    p = d / name
    p.write_text(content, encoding="utf-8")
    # Return path relative to notebook root for nicer display.
    root = Path(os.environ.get("KIRA_NOTEBOOK_DIR", str(Path.home() / "notebook")))
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except Exception:
        return str(p)


def list_proposals() -> list[dict]:
    d = _proposals_dir()
    out = []
    for p in sorted(d.glob("*.md"), reverse=True):
        try:
            text = p.read_text("utf-8", errors="replace")
        except Exception:
            text = ""
        first = next((ln for ln in text.splitlines() if ln.strip().startswith("#")), "")
        out.append(
            {
                "file": p.name,
                "title": first.lstrip("# ").strip(),
                "mtime": p.stat().st_mtime,
                "size": p.stat().st_size,
            }
        )
    return out
