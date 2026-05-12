# `llm/` — provider abstraction layer

Kira's runtime is **vendor-agnostic**. It speaks one canonical dialect of
messages, tool calls, and stream events; vendor adapters translate to/from
their wire formats. Adding a new provider does not touch `agent_runtime.py`.

## Layers

```
┌───────────────────────────────────────────────────────────────┐
│  agent_runtime.py        (run_agent, _llm_one_shot,           │
│  app.py / ops/tg_bot.py   _run_subagent_silent)               │
│                                                               │
│        ▼  list[Message]  +  list[ToolSpec]  ▼                 │
├───────────────────────────────────────────────────────────────┤
│  llm/base.py    Message · ToolCall · ToolSpec · StreamEvent · │
│                 Usage · LLMProvider (Protocol)                │
├───────────────────────────────────────────────────────────────┤
│  llm/__init__.py    get_provider(name=None)  ← KIRA_LLM_PROVIDER │
├───────────────────────────────────────────────────────────────┤
│  llm/q_provider.py    │ llm/mock_provider.py │ (future:        │
│  Amazon Q (Bedrock)   │ deterministic tests  │  anthropic_…,   │
│  + Q-dict converters  │                      │  openai_…,      │
│                       │                      │  groq_…)        │
├───────────────────────────────────────────────────────────────┤
│  q_client.py     (raw HTTP, AWS event-stream framing,         │
│                   retries, key rotation)                      │
└───────────────────────────────────────────────────────────────┘
```

The runtime depends only on `llm.base` and on a concrete provider via
`get_provider()`. No vendor SDK is imported above the provider line.

## Canonical types (`llm/base.py`)

```python
@dataclass
class Message:
    role: "system" | "user" | "assistant" | "tool"
    content: str | list[dict]          # text or multimodal parts
    tool_calls: list[ToolCall] = []    # assistant turns only
    tool_call_id: str | None = None    # tool turns only
    name: str | None = None            # tool status / assistant messageId

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass
class ToolSpec:                         # OpenAI-shape function spec
    name: str
    description: str
    parameters: dict                    # JSON schema

@dataclass
class StreamEvent:
    type: "text" | "tool_call" | "throttle" | "usage" |
          "metering" | "context_usage" | "message_id" |
          "error" | "cancelled" | "done"
    text: str | None = None
    tool: ToolCall | None = None
    usage: Usage | None = None
    meta: dict = {}                     # provider-specific extras
```

The `LLMProvider` protocol:

```python
class LLMProvider(Protocol):
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
        extra: dict | None = None,
    ) -> AsyncIterator[StreamEvent]: ...

    async def health(self) -> dict: ...
```

## Selecting a provider

```python
from llm import get_provider
provider = get_provider()              # honours KIRA_LLM_PROVIDER (def. "amazon-q")
provider = get_provider("mock")        # for tests
```

Environment variable: `KIRA_LLM_PROVIDER=amazon-q | mock`.

## Q-dict ↔ canonical converters (`q_provider.py`)

Kira's SQLite still stores history in Amazon Q's native shape
(`userInputMessage` / `assistantResponseMessage`) so `/agent/sessions/{sid}`
can parse it directly for the transcript view. The runtime works on
canonical messages and converts at the boundary:

| Direction                 | Function                            |
|---------------------------|-------------------------------------|
| Load from DB              | `q_history_to_messages(history)`    |
| Save back to DB           | `messages_to_q_history(msgs)`       |
| Build a wire request body | `messages_to_q_body(msgs, tools, …)`|

Conventions:

- The **first** user-turn without `toolResults` and without `--- USER MESSAGE`
  markers is promoted to `role="system"` (Kira's system prompt).
- `wrap_user_text(text)` (and `wrap_text=True` flag) adds the Q-specific
  `--- CONTEXT ENTRY / USER MESSAGE ---` markers. The inverse strips them.
- Assistant `messageId` round-trips via `Message.name`.
- Tool results are emitted as separate `role="tool"` messages and re-collapsed
  into the next user turn's `toolResults` when serialised back.

## Adding a new provider

1. Drop in `llm/<vendor>_provider.py` exporting a class that satisfies the
   `LLMProvider` protocol.
2. Implement two things:
   - `messages_to_<vendor>_body(messages, tools, model, **extras)` — builds
     the wire payload from canonical messages.
   - A streaming loop that yields `StreamEvent`s as deltas arrive.
3. Register the name in `llm/__init__.py::get_provider`.
4. (Optional) Provide `<vendor>_history_to_messages` if you want SQLite
   sessions created on this provider to be readable.

No edits to `agent_runtime.py`, `app.py`, or the SSE/UI layer are required.

## Testing

- `tests/test_llm_base.py` — canonical types + helpers
- `tests/test_llm_mock.py` — deterministic MockProvider behaviour
- `tests/test_llm_q_provider.py` — Q adapter wire format + streaming
- `tests/test_phase3c2_history.py` — bidirectional Q-dict↔Message[]
  round-trip, edge cases (empty history, trailing tool_results, markerless
  content, messageId preservation)
- `tests/test_runtime_subagent_via_mock.py`, `tests/test_llm_one_shot_via_mock.py`
  — guards that the canonical paths in `agent_runtime` accept any provider

Total: ~710 tests pass; `llm/` coverage 91–96% per module.

## Migration history

| Phase | Status | Commits                                    | What                                                                |
|------:|:------:|--------------------------------------------|---------------------------------------------------------------------|
|   1   |   ✅   | `1ae7476`                                   | `base.py` + `QProvider` skeleton                                    |
|   2   |   ✅   | `1ae7476`                                   | `MockProvider`                                                      |
|  3a   |   ✅   | `9891313`                                   | `_llm_one_shot` migrated to canonical messages                      |
|  3b   |   ✅   | `c862eaa`                                   | `_run_subagent_silent` migrated                                     |
| 3c.1  |   ✅   | `8f0b325`                                   | Main `run_agent` routed through `QProvider.stream_raw_body` (escape hatch) |
|  3d   |   ✅   | `8974a45`                                   | `q_client` import removed from `agent_runtime`                      |
| 3c.2  |   ✅   | `fe862b1`                                   | Full canonical `list[Message]` inside `run_agent` + Q-dict converters |
|   4   |   🔵   | —                                           | `/agent/health` shows active provider, `/models` filters by provider |
|   5   |   ✅   | this commit                                 | This document                                                       |
|   6   |   🔵   | —                                           | Per-session provider, fallback chain, cost-aware routing            |
