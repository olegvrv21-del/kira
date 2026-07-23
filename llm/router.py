"""Request-aware model router — pick the cheapest model that can do the job.

When the caller asks for model "auto", we run a single cheap classifier call
to bucket the request into a difficulty tier, then map the tier to a concrete
model. The philosophy: don't burn an expensive reasoning model on "hi", and
don't cripple a hard refactor with a nano model.

Tiers → models are configured via env so ops can retune without code changes:

    KIRA_ROUTE_SIMPLE=gpt-5.4-mini        greetings, short Qs, chit-chat
    KIRA_ROUTE_STANDARD=claude-sonnet-4-6 normal coding / tool work
    KIRA_ROUTE_HARD=claude-opus-4-8       architecture, deep debug, big refactor
    KIRA_ROUTE_CLASSIFIER=gpt-5.4-mini    the cheap model that does the routing
    KIRA_ROUTE_MAX_INPUT_CHARS=6000       transcript slice sent to classifier

The classifier is deliberately tiny (one short completion, no tools, ~20
output tokens) so routing overhead is a few tokens and a fraction of a second.
If the classifier fails for any reason we fall back to STANDARD — routing must
never block a real request.
"""

from __future__ import annotations

import os
import re


TIERS = ("simple", "standard", "hard")


def tier_models() -> dict[str, str]:
    return {
        "simple": os.environ.get("KIRA_ROUTE_SIMPLE", "gpt-5.4-mini"),
        "standard": os.environ.get("KIRA_ROUTE_STANDARD",
                                    os.environ.get("KIRA_DEFAULT_MODEL", "gpt-5.4")),
        "hard": os.environ.get("KIRA_ROUTE_HARD", "gpt-5.6"),
    }


def model_for_tier(tier: str) -> str:
    return tier_models().get(tier, tier_models()["standard"])


# --- fast pre-filter --------------------------------------------------------
# Cheap deterministic shortcut so we don't even spend the classifier call on
# obviously-trivial prompts (and so tests don't need a live LLM).
_TRIVIAL_RE = re.compile(
    r"^\s*(привет|здравствуй|хай|hi|hello|hey|yo|спасибо|thanks|thx|ok|ок|"
    r"да|нет|yes|no|пока|bye|как дела|how are you)[\s!.?)]*$",
    re.IGNORECASE,
)
_HARD_HINTS = (
    "архитектур", "architecture", "спроектируй", "design ", "рефактор",
    "refactor", "почему не работает", "root cause", "разберись глубоко",
    "оптимизир", "optimi", "проанализируй весь", "перепиши", "rewrite",
    "многопоточ", "concurren", "race condition", "гонк",
)

_CLASSIFIER_SYS = (
    "You are a request difficulty classifier for a coding agent. "
    "Read the user's request and reply with EXACTLY ONE word:\n"
    "  simple   — greeting, tiny factual question, chit-chat, one-liner\n"
    "  standard — normal coding/edit/file/tool task, typical question\n"
    "  hard     — architecture design, deep debugging, large refactor, "
    "multi-step reasoning, performance/concurrency work\n"
    "Reply with only the single word, nothing else."
)


def _prefilter(prompt: str) -> str | None:
    """Return a tier if we can decide without an LLM, else None."""
    p = (prompt or "").strip()
    if not p:
        return "simple"
    if _TRIVIAL_RE.match(p):
        return "simple"
    low = p.lower()
    if any(h in low for h in _HARD_HINTS):
        return "hard"
    return None


def _sanitize_tier(raw: str) -> str:
    r = (raw or "").strip().lower()
    for t in TIERS:
        if t in r:
            return t
    return "standard"


async def classify(prompt: str, *, llm_one_shot=None) -> str:
    """Classify a prompt into a tier. `llm_one_shot(prompt, model, system)`
    is an async callable returning the model's text; injected so this module
    stays dependency-light and unit-testable without a live provider.

    Never raises — defaults to 'standard' on any error.
    """
    quick = _prefilter(prompt)
    if quick is not None:
        return quick
    if llm_one_shot is None:
        return "standard"
    max_chars = int(os.environ.get("KIRA_ROUTE_MAX_INPUT_CHARS", "6000"))
    snippet = (prompt or "")[:max_chars]
    classifier_model = os.environ.get("KIRA_ROUTE_CLASSIFIER", "gpt-5.4-mini")
    try:
        out = await llm_one_shot(snippet, model=classifier_model,
                                 system=_CLASSIFIER_SYS)
        return _sanitize_tier(out or "")
    except Exception:
        return "standard"


async def route(prompt: str, *, llm_one_shot=None) -> tuple[str, str]:
    """Return (model_id, tier) for a prompt."""
    tier = await classify(prompt, llm_one_shot=llm_one_shot)
    return model_for_tier(tier), tier
