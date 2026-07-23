"""Per-model endpoint resolution.

Different models live behind different API keys / gateways. On Unity2, for
example, the OpenAI-family models (gpt-5.x) are reachable with one key while
the Claude family (claude-*) needs a second key bound to the "claude" group.
This module maps a *model id* to the `(base_url, api_key)` that can serve it,
so the rest of the stack just passes model ids around and never worries about
which credential to use.

Configuration
-------------
Explicit (future-proof, supports Groq/Gemini/etc.) via JSON env KIRA_ENDPOINTS:

    KIRA_ENDPOINTS='[
      {"match":"^claude","base_url":"https://unity2.ai/v1","key_env":"KIRA_CLAUDE_KEY"},
      {"match":".*","base_url":"https://unity2.ai/v1","key_env":"OPENROUTER_API_KEY"}
    ]'

Each rule: `match` (regex on model id, first match wins), and either
`key` (literal) or `key_env` (name of an env var holding the key). `base_url`
optional — defaults to OPENROUTER_BASE_URL.

Zero-config default (what prod uses if KIRA_ENDPOINTS is unset):
  * model matching ^claude  → base=OPENROUTER_BASE_URL, key=KIRA_CLAUDE_KEY
                              (falls back to OPENROUTER_API_KEY if unset)
  * everything else         → base=OPENROUTER_BASE_URL, key=OPENROUTER_API_KEY

So enabling Claude routing on prod is just: set KIRA_CLAUDE_KEY. Nothing else.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass


@dataclass
class Endpoint:
    base_url: str
    api_key: str


def _default_base() -> str:
    return (os.environ.get("OPENROUTER_BASE_URL")
            or "https://openrouter.ai/api/v1").rstrip("/")


def _parse_rules() -> list[dict]:
    raw = os.environ.get("KIRA_ENDPOINTS", "").strip()
    if not raw:
        return []
    try:
        rules = json.loads(raw)
        return rules if isinstance(rules, list) else []
    except Exception:
        return []


def _key_for(rule: dict) -> str:
    if rule.get("key"):
        return str(rule["key"]).strip()
    env = rule.get("key_env")
    if env:
        return os.environ.get(env, "").strip()
    return ""


def resolve(model: str) -> Endpoint | None:
    """Return the Endpoint able to serve `model`, or None to signal 'use the
    provider's own default credentials' (backward-compatible no-op)."""
    rules = _parse_rules()
    if rules:
        for rule in rules:
            pat = rule.get("match", ".*")
            try:
                if re.search(pat, model or ""):
                    base = (rule.get("base_url") or _default_base()).rstrip("/")
                    return Endpoint(base, _key_for(rule))
            except re.error:
                continue
        return None

    # --- zero-config default -------------------------------------------
    base = _default_base()
    if (model or "").startswith("claude"):
        claude_key = os.environ.get("KIRA_CLAUDE_KEY", "").strip()
        if claude_key:
            return Endpoint(base, claude_key)
        # No dedicated claude key configured — fall through to default key.
    default_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if default_key:
        return Endpoint(base, default_key)
    return None


def is_configured() -> bool:
    """True if per-model routing might send different models to different keys
    (i.e. a Claude key or explicit rules exist). Used for introspection only."""
    return bool(os.environ.get("KIRA_ENDPOINTS", "").strip()
                or os.environ.get("KIRA_CLAUDE_KEY", "").strip())
