"""Provider-agnostic LLM streaming contract.

Kira's runtime should depend only on this module, never on a vendor SDK.
A provider is anything that can:

  1. Accept a list of canonical messages + tool specs.
  2. Emit an async stream of `StreamEvent`s while a generation is in flight.
  3. Report its health and the models it supports.

Canonical message shape (OpenAI-flavoured — the de-facto interchange format):

    {
        "role": "system" | "user" | "assistant" | "tool",
        "content": str | list[ContentPart],          # text or multimodal
        "tool_calls": [ToolCall],                     # assistant turn only
        "tool_call_id": str,                          # tool turn only
        "name": str,                                  # tool turn only
    }

Provider adapters are responsible for converting this shape into whatever
the underlying API expects (e.g. Amazon Q `userInputMessage` /
`assistantResponseMessage`, Anthropic `messages`, OpenAI `messages`).

This file is intentionally tiny and dependency-free — it must be importable
from tests without pulling in httpx, boto, anthropic, etc.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Canonical types
# ---------------------------------------------------------------------------

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    """A single tool invocation emitted by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """Canonical message. Adapters convert to/from vendor shape."""

    role: Role
    content: str | list[dict] = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ToolSpec:
    """OpenAI-shape function spec. `agent_tool_specs.json` already uses this."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # Provider-specific extras (cost, cache hits, …) live in `meta`.
    meta: dict[str, Any] = field(default_factory=dict)


EventType = Literal[
    "text",          # delta of assistant text
    "tool_call",     # one complete tool call (name + args)
    "throttle",      # adapter is backing off / rotating keys
    "usage",         # token accounting
    "metering",      # credits delta (Q-specific but generally useful)
    "context_usage", # context window % (Q-specific)
    "message_id",    # provider-side message id for the in-flight assistant turn
    "error",         # non-fatal, stream continues
    "cancelled",     # cancel_event was set
    "done",          # end of stream
]


@dataclass
class StreamEvent:
    type: EventType
    text: str | None = None
    tool: ToolCall | None = None
    usage: Usage | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    """Anything that can stream a completion.

    Implementations live in `llm/<vendor>_provider.py`. See `q_provider.py`
    for the reference adapter wrapping Amazon Q.
    """

    name: str
    supported_models: list[str]

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
        """Yield events as the model generates. MUST end with `type='done'`
        unless cancelled (then `type='cancelled'`)."""
        ...

    async def health(self) -> dict[str, Any]:
        """Return a small dict surfaced in /agent/health."""
        ...


# ---------------------------------------------------------------------------
# Helpers used by adapters & tests
# ---------------------------------------------------------------------------


def message_from_dict(d: dict) -> Message:
    """Build a Message from the loose dict shape used in agent_runtime/storage."""
    tcs = []
    for tc in d.get("tool_calls") or []:
        if isinstance(tc, ToolCall):
            tcs.append(tc)
            continue
        args = tc.get("arguments") or tc.get("input") or {}
        if isinstance(args, str):
            import json

            try:
                args = json.loads(args)
            except Exception:
                args = {"_raw": args}
        tcs.append(
            ToolCall(
                id=tc.get("id") or tc.get("tool_use_id") or "",
                name=tc.get("name") or "",
                arguments=args,
            )
        )
    return Message(
        role=d.get("role", "user"),
        content=d.get("content", ""),
        tool_calls=tcs,
        tool_call_id=d.get("tool_call_id"),
        name=d.get("name"),
    )


def toolspecs_from_openai_json(specs: list[dict]) -> list[ToolSpec]:
    """Parse OpenAI-shape `[{"type":"function","function":{...}}]` or bare names."""
    out: list[ToolSpec] = []
    for s in specs:
        # Several shapes seen in the wild:
        #   {"type":"function","function":{"name":..,"description":..,"parameters":..}}
        #   {"name":..,"description":..,"inputSchema":..}        (Bedrock/Anthropic-ish)
        #   {"toolSpec":{"name":..,"description":..,"inputSchema":{"json":..}}}
        if "function" in s and isinstance(s["function"], dict):
            f = s["function"]
            out.append(ToolSpec(f["name"], f.get("description", ""), f.get("parameters", {})))
        elif "toolSpec" in s and isinstance(s["toolSpec"], dict):
            ts = s["toolSpec"]
            schema = ts.get("inputSchema", {})
            if isinstance(schema, dict) and "json" in schema:
                schema = schema["json"]
            out.append(ToolSpec(ts["name"], ts.get("description", ""), schema or {}))
        elif "name" in s:
            schema = s.get("parameters") or s.get("inputSchema") or {}
            if isinstance(schema, dict) and "json" in schema:
                schema = schema["json"]
            out.append(ToolSpec(s["name"], s.get("description", ""), schema))
    return out
