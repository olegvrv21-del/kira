"""Auto-title generator for chat sessions.

DeerFlow-inspired (TitleMiddleware). Single LLM call to a small/cheap model
asking for a 4-6 word title. Replaces `derive_title()` which only took the
first 80 chars of the user prompt — that's fine as a fallback, but for the
sidebar it's nice to have "Починили баг в guardrails" instead of "посмотри
что у нас в логах за вчера и проверь".

Behavior:
- Called once per session: when the title is empty OR still equals the raw
  derive_title() output (i.e. user did not rename manually).
- Uses _llm_one_shot via the configured provider (Haiku by default).
- Hard timeout, hard length cap; on any failure returns None so caller
  can keep the derive_title() value.
- Disabled via KIRA_AUTOTITLE=0.

Public API:
  propose_title(history, llm_one_shot, model="haiku", api_key=None) -> str | None
  should_retitle(current_title, history) -> bool
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable

import agent_store

_TITLE_PROMPT = (
    "You will be given the opening exchange of a chat. "
    "Return a 4–6 word title summarising what the conversation is about. "
    "Match the user's language (Russian or English). "
    "Plain text only — no quotes, no emojis, no punctuation at the end, "
    "no prefix like 'Title:'. "
    "Just the title itself, nothing else.\n\n"
    "Exchange:\n"
    "{exchange}\n\n"
    "Title:"
)

_MAX_TITLE_LEN = 80
_MAX_EXCHANGE_CHARS = 2000


def _extract_assistant_text(am: dict) -> str:
    """Get visible assistant text from an assistantResponseMessage dict."""
    if not isinstance(am, dict):
        return ""
    c = am.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for piece in c:
            if isinstance(piece, dict) and isinstance(piece.get("text"), str):
                parts.append(piece["text"])
            elif isinstance(piece, str):
                parts.append(piece)
        return "\n".join(parts)
    return ""


def _build_exchange(history: list[dict]) -> str | None:
    """Return 'USER: ...\\nASSISTANT: ...' from the first non-tool turn pair."""
    user_text: str | None = None
    assistant_text: str | None = None
    for m in history:
        if not isinstance(m, dict):
            continue
        uim = m.get("userInputMessage")
        if uim and user_text is None:
            ctx = uim.get("userInputMessageContext") or {}
            if ctx.get("toolResults"):
                continue
            txt = agent_store.extract_user_text(uim.get("content", ""))
            if txt:
                user_text = txt
            continue
        am = m.get("assistantResponseMessage")
        if am and user_text and assistant_text is None:
            t = _extract_assistant_text(am).strip()
            if t:
                assistant_text = t
                break
    if not user_text:
        return None
    user_text = user_text.strip()[:_MAX_EXCHANGE_CHARS // 2]
    parts = [f"USER: {user_text}"]
    if assistant_text:
        parts.append(f"ASSISTANT: {assistant_text.strip()[:_MAX_EXCHANGE_CHARS // 2]}")
    return "\n".join(parts)


_CLEAN_RE = re.compile(r'^[\s"\'«»“”„‚‘’`*_#]+|[\s"\'«»“”„‚‘’`*_#.!?,;:]+$')


def _clean(title: str) -> str:
    """Strip wrapping quotes/punct, collapse whitespace, cap length."""
    t = title.strip().splitlines()[0] if title else ""
    # Remove 'Title:' or 'Заголовок:' prefix if model added it.
    t = re.sub(r"^(title|заголовок|название)\s*[:\-—]\s*", "", t, flags=re.IGNORECASE)
    t = _CLEAN_RE.sub("", t)
    t = re.sub(r"\s+", " ", t)
    return t[:_MAX_TITLE_LEN].strip()


def should_retitle(current_title: str | None, history: list[dict]) -> bool:
    """Auto-retitle if title is empty or still equals the raw first-prompt
    fallback (derive_title). User-renamed titles are left alone.
    """
    if os.environ.get("KIRA_AUTOTITLE", "1") == "0":
        return False
    derived = agent_store.derive_title(history)
    if not current_title:
        return bool(derived)
    if derived and current_title.strip() == derived.strip():
        return True
    return False


async def propose_title(
    history: list[dict],
    llm_one_shot: Callable[..., Awaitable[str]],
    model: str = "haiku",
    api_key: str = "",
) -> str | None:
    """Ask the small model for a 4-6 word title. Returns None on any error."""
    if os.environ.get("KIRA_AUTOTITLE", "1") == "0":
        return None
    exchange = _build_exchange(history)
    if not exchange:
        return None
    prompt = _TITLE_PROMPT.format(exchange=exchange[:_MAX_EXCHANGE_CHARS])
    try:
        raw = await llm_one_shot(api_key, prompt, model, system=None, max_tokens=32)
    except Exception:
        return None
    if not raw or raw.startswith("[llm_one_shot error]") or "(empty response)" in raw:
        return None
    title = _clean(raw)
    if not title or len(title) < 3:
        return None
    return title
