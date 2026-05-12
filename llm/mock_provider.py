"""Deterministic in-memory provider for tests.

Usage:

    from llm.mock_provider import MockProvider

    p = MockProvider(script=[
        {"type": "text", "text": "Hello "},
        {"type": "text", "text": "world"},
        {"type": "tool_call", "id": "t1", "name": "fs_read", "args": {"path": "x"}},
        {"type": "text", "text": " done"},
    ])
    async for ev in p.stream(messages, tools, model="mock-1"):
        ...

The script can also be provided as a *callable* taking the messages list,
so tests can return different scripts per turn (useful for agent loops).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from .base import LLMProvider, Message, StreamEvent, ToolCall, ToolSpec, Usage

Script = list[dict] | Callable[[list[Message]], list[dict]]


class MockProvider:
    name = "mock"

    def __init__(self, script: Script | None = None, *, models: list[str] | None = None, delay: float = 0.0):
        self._script = script if script is not None else [{"type": "text", "text": "ok"}]
        self.supported_models = models or ["mock-1"]
        self.delay = delay
        # Recorded calls for assertions.
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        model: str,
        cancel: asyncio.Event | None = None,
        timeout: float = 300.0,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        # Snapshot the messages list so later mutations by the caller (agent
        # loops typically append to the same list) don't retroactively change
        # what tests see for past calls.
        self.calls.append(
            {
                "messages": list(messages),
                "tools": [t.name for t in tools],
                "model": model,
                "extra": extra or {},
            }
        )
        script = self._script(messages) if callable(self._script) else self._script

        for step in script:
            if cancel is not None and cancel.is_set():
                yield StreamEvent(type="cancelled")
                return
            if self.delay:
                await asyncio.sleep(self.delay)

            t = step.get("type")
            if t == "text":
                yield StreamEvent(type="text", text=step["text"])
            elif t == "tool_call":
                yield StreamEvent(
                    type="tool_call",
                    tool=ToolCall(
                        id=step.get("id", "call_0"),
                        name=step["name"],
                        arguments=step.get("args", {}),
                    ),
                )
            elif t == "throttle":
                yield StreamEvent(type="throttle", meta=step.get("meta", {}))
            elif t == "usage":
                yield StreamEvent(
                    type="usage",
                    usage=Usage(
                        input_tokens=step.get("input_tokens", 0),
                        output_tokens=step.get("output_tokens", 0),
                        meta=step.get("meta", {}),
                    ),
                )
            elif t == "error":
                yield StreamEvent(type="error", meta={"message": step.get("message", "mock error")})
            elif t == "sleep":
                await asyncio.sleep(step.get("seconds", 0.01))
            elif t == "raise":
                raise RuntimeError(step.get("message", "mock raise"))
            else:
                raise ValueError(f"unknown mock step type: {t!r}")

        yield StreamEvent(type="done")

    async def usage(self) -> dict[str, Any]:
        """Mock provider doesn't track usage — surface unsupported."""
        return {"supported": False, "provider": self.name}

    async def health(self) -> dict[str, Any]:
        return {"name": self.name, "status": "ok", "models": self.supported_models}


# Sanity: MockProvider satisfies the LLMProvider Protocol.
_proto_check: LLMProvider = MockProvider()  # noqa: F841
