"""Amazon Q adapter — thin wrapper around the existing `q_client.stream_q`.

This is the reference adapter for the `LLMProvider` contract. It converts:

    canonical Message[]   →  Q `conversationState.history` + `currentMessage`
    canonical ToolSpec[]  →  Q `userInputMessageContext.tools`
    Q stream events       →  canonical StreamEvent

The heavy machinery (HTTP, retries, key rotation, AWS event-stream frame
parsing) stays in `q_client.py`. This file just speaks the dialect.

Limitations (intentional, to be lifted in Phase 3):
  - Does NOT yet handle the runtime's plumbing for envState, images,
    sandbox cwd, dev_loop, subagent recursion. Those are wired in when
    `agent_runtime.py` migrates to call this adapter (Phase 3 of the plan).
  - This Phase-1 adapter is sufficient for unit tests and Mock-parity, not
    yet a drop-in replacement for the runtime's direct q_client usage.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from .base import LLMProvider, Message, Role, StreamEvent, ToolCall, ToolSpec, Usage

# Imported lazily so this module is testable without httpx.
_q_client = None


def _get_q_client():
    global _q_client
    if _q_client is None:
        import q_client  # noqa: WPS433 (intentional lazy import)

        _q_client = q_client
    return _q_client


# ---------------------------------------------------------------------------
# Canonical → Q conversion
# ---------------------------------------------------------------------------


def _tool_specs_to_q(tools: list[ToolSpec]) -> list[dict]:
    """Q expects `[{"toolSpec": {"name":.., "description":.., "inputSchema":{"json": <schema>}}}]`."""
    out = []
    for t in tools:
        out.append(
            {
                "toolSpec": {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": {"json": t.parameters or {"type": "object", "properties": {}}},
                }
            }
        )
    return out


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Multimodal parts: pick text parts, ignore others (images handled separately).
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


USER_MSG_BEGIN = "--- USER MESSAGE BEGIN ---"
USER_MSG_END = "--- USER MESSAGE END ---"


def wrap_user_text(text: str) -> str:
    """Wrap raw user text in the CONTEXT ENTRY / USER MESSAGE markers Q expects.

    Empty text -> empty string (for tool_results-only continuations).
    """
    if not text:
        return ""
    import datetime

    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return (
        f"--- CONTEXT ENTRY BEGIN ---\n"
        f"Current time: {ts}\n"
        f"--- CONTEXT ENTRY END ---\n\n"
        f"{USER_MSG_BEGIN}\n{text}{USER_MSG_END}"
    )


def _unwrap_user_text(content: str) -> str:
    """Inverse of wrap_user_text: pull the raw text out of the markers.
    Falls back to the full content if markers are absent."""
    if not isinstance(content, str):
        return ""
    i = content.find(USER_MSG_BEGIN)
    if i < 0:
        return content
    i += len(USER_MSG_BEGIN)
    if i < len(content) and content[i] == "\n":
        i += 1
    j = content.find(USER_MSG_END, i)
    if j < 0:
        return content[i:].strip()
    return content[i:j].strip()


def messages_to_q_body(
    messages: list[Message],
    tools: list[ToolSpec],
    *,
    model: str,
    conversation_id: str | None = None,
    continuation_id: str | None = None,
    env_state: dict | None = None,
    images: list[dict] | None = None,
    wrap_text: bool = False,
) -> dict:
    """Build the JSON body Bedrock-Q expects.

    Splits `messages` into `history` (everything but the last user turn) + a
    `currentMessage` (the trailing user turn, possibly carrying tool_results).

    `env_state` (if given) is injected into every userInputMessageContext as
    Q expects (`{operatingSystem, currentWorkingDirectory}`). `images`, if any,
    are attached to the `currentMessage`.
    """
    q_tools = _tool_specs_to_q(tools)

    def _ctx() -> dict:
        c: dict = {"tools": q_tools}
        if env_state is not None:
            c["envState"] = env_state
        return c

    def _user_content(text: str) -> str:
        return wrap_user_text(text) if wrap_text else text

    history: list[dict] = []
    current: dict | None = None

    # Walk messages, pairing user→assistant turns. The last user message becomes `current`.
    pending_tool_results: list[dict] = []
    last_user_idx = -1
    for i, m in enumerate(messages):
        if m.role == "user":
            last_user_idx = i

    for i, m in enumerate(messages):
        if m.role == "system":
            # Q has no separate system role — prepend as initial user turn with
            # system content. System prompt is NEVER wrapped in user markers.
            history.append(
                {
                    "userInputMessage": {
                        "content": _content_to_text(m.content),
                        "userInputMessageContext": _ctx(),
                        "origin": "KIRO_CLI",
                        "modelId": model,
                    }
                }
            )
        elif m.role == "tool":
            # Buffer tool results to attach to the next user message.
            status = (m.name or "success").lower() if m.name in ("error", "success") else "success"
            pending_tool_results.append(
                {
                    "toolUseId": m.tool_call_id or "",
                    "content": [{"text": _content_to_text(m.content)}],
                    "status": status,
                }
            )
        elif m.role == "user":
            ctx = _ctx()
            if pending_tool_results:
                ctx["toolResults"] = pending_tool_results
                pending_tool_results = []
            text = _content_to_text(m.content)
            msg = {
                # tool_results-only continuations have empty text; don't wrap those.
                "content": _user_content(text) if text else "",
                "userInputMessageContext": ctx,
                "origin": "KIRO_CLI",
                "modelId": model,
            }
            # Per-message image attachment, falling back to call-level `images`.
            msg_images = None
            if isinstance(m.content, list):
                msg_images = [p for p in m.content if isinstance(p, dict) and p.get("format")]
            if i == last_user_idx:
                if msg_images:
                    msg["images"] = msg_images
                elif images:
                    msg["images"] = images
                current = {"userInputMessage": msg}
            else:
                if msg_images:
                    msg["images"] = msg_images
                history.append({"userInputMessage": msg})
        elif m.role == "assistant":
            arm: dict = {
                "messageId": (m.name or uuid.uuid4().hex),
                "content": _content_to_text(m.content),
            }
            if m.tool_calls:
                arm["toolUses"] = [
                    {"toolUseId": tc.id, "name": tc.name, "input": tc.arguments} for tc in m.tool_calls
                ]
            else:
                # Q requires the field to exist (even empty) for assistant turns
                # so the server's tool-pair validator is happy.
                arm["toolUses"] = []
            history.append({"assistantResponseMessage": arm})

    if current is None:
        # No user message at all — synthesise an empty one carrying tool_results.
        ctx = _ctx()
        if pending_tool_results:
            ctx["toolResults"] = pending_tool_results
        current = {
            "userInputMessage": {
                "content": "",
                "userInputMessageContext": ctx,
                "origin": "KIRO_CLI",
                "modelId": model,
            }
        }

    return {
        "conversationState": {
            "chatTriggerType": "MANUAL",
            "conversationId": conversation_id or str(uuid.uuid4()),
            "agentContinuationId": continuation_id or str(uuid.uuid4()),
            "agentTaskType": "vibe",
            "history": history,
            "currentMessage": current,
        }
    }


# ---------------------------------------------------------------------------
# Q-dict history <-> canonical Message[]
# ---------------------------------------------------------------------------


def q_history_to_messages(history: list[dict]) -> list[Message]:
    """Convert a Q-protocol history (list of dicts with userInputMessage /
    assistantResponseMessage) to canonical Message[]. Tool results inside a
    user turn become preceding `role='tool'` messages.

    This is the inverse of `messages_to_q_body(..., wrap_text=True)` over the
    `history` portion. Used by `run_agent` to bridge SQLite-stored sessions
    (still in Q shape for back-compat) with the provider-agnostic loop.
    """
    out: list[Message] = []
    if not history:
        return out
    for i, m in enumerate(history):
        if "userInputMessage" in m:
            uim = m["userInputMessage"]
            ctx = uim.get("userInputMessageContext") or {}
            # tool_results -> separate tool messages first
            for tr in ctx.get("toolResults") or []:
                parts = tr.get("content") or []
                txt = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
                out.append(
                    Message(
                        role="tool",
                        content=txt,
                        tool_call_id=tr.get("toolUseId") or "",
                        name=tr.get("status") or "success",
                    )
                )
            text = _unwrap_user_text(uim.get("content", "") or "")
            # The very first user turn is the system prompt (no markers, no
            # tool_results) — promote it to role='system' so adapters that
            # have a native system slot can use it.
            role: Role = "system" if (i == 0 and not ctx.get("toolResults") and text) else "user"
            content: Any = text
            imgs = uim.get("images")
            if imgs:
                # Carry images alongside text as a list-content message.
                content = ([{"type": "text", "text": text}] if text else []) + list(imgs)
            out.append(Message(role=role, content=content))
        elif "assistantResponseMessage" in m:
            arm = m["assistantResponseMessage"]
            tcs = []
            for tu in arm.get("toolUses") or []:
                tcs.append(
                    ToolCall(
                        id=tu.get("toolUseId") or "",
                        name=tu.get("name") or "",
                        arguments=tu.get("input") if isinstance(tu.get("input"), dict) else {},
                    )
                )
            out.append(
                Message(
                    role="assistant",
                    content=arm.get("content", "") or "",
                    tool_calls=tcs,
                    # stash messageId in `name` so round-trip preserves it.
                    name=arm.get("messageId"),
                )
            )
    return out


def messages_to_q_history(messages: list[Message], *, wrap_text: bool = False) -> list[dict]:
    """Inverse of `q_history_to_messages`: render a Message[] back to the
    Q-dict shape SQLite stores. Drops nothing, preserves toolUseIds.

    `wrap_text=True` reapplies the CONTEXT/USER markers around raw user text
    so older transcripts (which still expect markers when re-read via
    `agent_store.extract_user_text`) round-trip cleanly.
    """
    out: list[dict] = []
    pending_tool_results: list[dict] = []
    for i, m in enumerate(messages):
        if m.role == "tool":
            text = _content_to_text(m.content)
            pending_tool_results.append(
                {
                    "toolUseId": m.tool_call_id or "",
                    "content": [{"text": text}],
                    "status": (m.name or "success"),
                }
            )
            continue
        if m.role in ("system", "user"):
            ctx: dict = {}
            if pending_tool_results:
                ctx["toolResults"] = pending_tool_results
                pending_tool_results = []
            text = _content_to_text(m.content)
            content = text
            if m.role == "user" and wrap_text and text:
                content = wrap_user_text(text)
            uim = {"content": content, "userInputMessageContext": ctx, "origin": "KIRO_CLI"}
            if isinstance(m.content, list):
                imgs = [p for p in m.content if isinstance(p, dict) and p.get("format")]
                if imgs:
                    uim["images"] = imgs
            out.append({"userInputMessage": uim})
        elif m.role == "assistant":
            arm: dict = {
                "messageId": m.name or uuid.uuid4().hex,
                "content": _content_to_text(m.content),
                "toolUses": [
                    {"toolUseId": tc.id, "name": tc.name, "input": tc.arguments}
                    for tc in (m.tool_calls or [])
                ],
            }
            out.append({"assistantResponseMessage": arm})
    # Trailing tool_results without a user turn — attach to a synthetic empty user.
    if pending_tool_results:
        out.append(
            {
                "userInputMessage": {
                    "content": "",
                    "userInputMessageContext": {"toolResults": pending_tool_results},
                    "origin": "KIRO_CLI",
                }
            }
        )
    return out


# ---------------------------------------------------------------------------
# Q events → canonical StreamEvent
# ---------------------------------------------------------------------------


class _ToolAccumulator:
    """Q streams tool input as JSON fragments; reassemble until `stop=True`."""

    def __init__(self) -> None:
        self.buf: dict[str, dict[str, Any]] = {}

    def feed(self, payload: dict) -> ToolCall | None:
        tid = payload.get("toolUseId")
        if not tid:
            return None
        slot = self.buf.setdefault(tid, {"name": "", "raw": ""})
        if payload.get("name"):
            slot["name"] = payload["name"]
        if payload.get("input"):
            slot["raw"] += payload["input"]
        if payload.get("stop"):
            try:
                args = json.loads(slot["raw"]) if slot["raw"] else {}
            except Exception as e:
                args = {"_parse_error": str(e), "_raw": slot["raw"]}
            return ToolCall(id=tid, name=slot["name"], arguments=args)
        return None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class QProvider:
    name = "amazon-q"

    # Kept loose on purpose — Q routes by modelId and accepts a moving target.
    supported_models = [
        "claude-opus-4.7",
        "claude-opus-4.6",
        "claude-opus-4.5",
        "claude-sonnet-4.6",
        "claude-sonnet-4.5",
        "claude-haiku-4.5",
    ]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    def _resolve_key(self) -> str:
        if self.api_key:
            return self.api_key
        from agent_keys import key_pool  # local import: tests may not have it set up

        k = key_pool.current()
        if not k:
            raise RuntimeError("no Q API key available")
        return k

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
        body = messages_to_q_body(
            messages,
            tools,
            model=model,
            conversation_id=(extra or {}).get("conversation_id"),
            continuation_id=(extra or {}).get("continuation_id"),
            env_state=(extra or {}).get("env_state"),
            images=(extra or {}).get("images"),
        )
        api_key = self._resolve_key()
        acc = _ToolAccumulator()
        qc = _get_q_client()

        async for et, payload in qc.stream_q(api_key, body, timeout=timeout, cancel_event=cancel):
            if et == "_throttle":
                yield StreamEvent(type="throttle", meta=payload or {})
                continue
            if et == "_cancelled":
                yield StreamEvent(type="cancelled")
                return
            if not isinstance(payload, dict):
                continue
            if et == "assistantResponseEvent":
                content = payload.get("content")
                if content:
                    yield StreamEvent(type="text", text=content)
                mid = payload.get("messageId")
                if mid:
                    yield StreamEvent(type="message_id", meta={"message_id": mid})
            elif et == "toolUseEvent":
                tc = acc.feed(payload)
                if tc is not None:
                    yield StreamEvent(type="tool_call", tool=tc)
            elif et == "meteringEvent":
                yield StreamEvent(
                    type="metering",
                    meta={"credits": float(payload.get("usage", 0) or 0)},
                )
            elif et == "contextUsageEvent":
                yield StreamEvent(
                    type="context_usage",
                    meta={"context_pct": float(payload.get("contextUsagePercentage", 0) or 0)},
                )
            elif et == "messageMetadataEvent":
                u = payload.get("usage") or {}
                if u:
                    yield StreamEvent(
                        type="usage",
                        usage=Usage(
                            input_tokens=int(u.get("inputTokens", 0)),
                            output_tokens=int(u.get("outputTokens", 0)),
                            meta={k: v for k, v in u.items() if k not in ("inputTokens", "outputTokens")},
                        ),
                    )
        yield StreamEvent(type="done")

    async def stream_raw_body(
        self,
        body: dict,
        *,
        timeout: float = 300.0,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[tuple]:
        """Escape hatch: stream a pre-built Q body and yield raw (event_type, payload).

        Used by `run_agent` which manages its history as Q dicts directly (for
        DB back-compat and orphan-tool_use handling). Other adapters can either
        translate the Q body to their wire format or raise NotImplementedError.
        """
        api_key = self._resolve_key()
        qc = _get_q_client()
        async for et, payload in qc.stream_q(api_key, body, timeout=timeout, cancel_event=cancel):
            yield et, payload

    async def health(self) -> dict[str, Any]:
        try:
            from agent_keys import key_pool

            pool_size = len(getattr(key_pool, "keys", []) or [])
            current = key_pool.current()
            return {
                "name": self.name,
                "status": "ok" if current else "no_key",
                "pool_size": pool_size,
                "models": self.supported_models,
            }
        except Exception as e:
            return {"name": self.name, "status": "error", "error": str(e)}


_proto_check: LLMProvider = QProvider()  # noqa: F841
