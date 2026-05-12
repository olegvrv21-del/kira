"""Pluggable LLM provider layer for Kira.

Select a provider via the `KIRA_LLM_PROVIDER` env var (default: `amazon-q`),
or pass `name=` directly to `get_provider()`. Adapters live in this package.

Writing your own adapter (~100 LOC):

    class MyProvider:
        name = "my-llm"
        supported_models = ["my-model-1"]
        async def stream(self, messages, tools, *, model, cancel=None,
                         timeout=300, extra=None):
            ...                              # yield StreamEvent objects
            yield StreamEvent(type="done")
        async def health(self): return {"name": self.name, "status": "ok"}

    from llm import register
    register("my-llm", lambda: MyProvider())
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .base import (
    LLMProvider,
    Message,
    StreamEvent,
    ToolCall,
    ToolSpec,
    Usage,
    message_from_dict,
    toolspecs_from_openai_json,
)
from .mock_provider import MockProvider

__all__ = [
    "LLMProvider",
    "Message",
    "StreamEvent",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "MockProvider",
    "message_from_dict",
    "toolspecs_from_openai_json",
    "get_provider",
    "register",
    "available",
]

_REGISTRY: dict[str, Callable[[], LLMProvider]] = {}


def register(name: str, factory: Callable[[], LLMProvider]) -> None:
    """Register a provider factory. Idempotent."""
    _REGISTRY[name] = factory


def _default_registry() -> None:
    # Lazy-construct so importing `llm` doesn't pull in httpx until needed.
    if "amazon-q" not in _REGISTRY:
        def _q():
            from .q_provider import QProvider

            return QProvider()

        _REGISTRY["amazon-q"] = _q
    if "mock" not in _REGISTRY:
        _REGISTRY["mock"] = lambda: MockProvider()

    # Stubs — documented extension points. They raise NotImplementedError
    # until a client opts in and provides credentials/SDK config.
    if "anthropic" not in _REGISTRY:
        def _anthropic_stub():
            raise NotImplementedError(
                "Anthropic provider not implemented yet — see llm/q_provider.py "
                "for the reference adapter pattern, or contact the maintainer."
            )

        _REGISTRY["anthropic"] = _anthropic_stub
    if "openai" not in _REGISTRY:
        def _openai_stub():
            raise NotImplementedError(
                "OpenAI provider not implemented yet — see llm/q_provider.py for "
                "the reference adapter pattern, or contact the maintainer."
            )

        _REGISTRY["openai"] = _openai_stub


def get_provider(name: str | None = None, **kwargs: Any) -> LLMProvider:
    """Return a provider instance by name.

    Order of resolution: explicit arg → `KIRA_LLM_PROVIDER` env → "amazon-q".
    """
    _default_registry()
    n = name or os.environ.get("KIRA_LLM_PROVIDER", "amazon-q")
    if n not in _REGISTRY:
        raise KeyError(f"unknown LLM provider {n!r}; available: {sorted(_REGISTRY)}")
    return _REGISTRY[n]()


def available() -> list[str]:
    """List registered provider names (for /agent/health and CLI)."""
    _default_registry()
    return sorted(_REGISTRY)
