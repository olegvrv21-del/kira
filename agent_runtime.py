"""Async agent runtime: streams Kiro Q events as SSE for the webchat /agent endpoint.

Emits SSE lines as JSON objects with a `type` discriminator:
  - {"type":"text",     "delta":"..."}
  - {"type":"tool_call","id":..., "name":..., "input":{...}}
  - {"type":"tool_result","id":..., "status":"success|error", "output":"..."}
  - {"type":"stats",    "credits":0.12, "context_pct":1.3, "turns":3}
  - {"type":"done"}
  - {"type":"error",    "message":"..."}
"""

from __future__ import annotations

import asyncio
import json
import os

import agent_freeze
import re
import platform
import struct
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import agent_hooks
import agent_skills
# Phase 3d: q_client is no longer imported here directly. All Q HTTP goes
# through llm/q_provider.py. Tests still mock q_client.stream_q at the
# module level (that module is imported by QProvider lazily).
from agent_keys import key_pool

# Optional cost-limit hook installed by app.py. Signature:
#   (session_id: str, current_turn_credits: float) -> str | None
# Returning a string aborts the turn with that error message.
_cost_limit_exceeded = None

# Per-session cancel events. App.py registers/clears them.
# Keys are session_ids; values are asyncio.Event objects. Set() = cancel requested.
_CANCEL_EVENTS: dict[str, asyncio.Event] = {}


def request_cancel(sid: str) -> bool:
    ev = _CANCEL_EVENTS.get(sid)
    if ev is None:
        return False
    ev.set()
    return True


def _register_cancel(sid: str) -> asyncio.Event:
    ev = asyncio.Event()
    _CANCEL_EVENTS[sid] = ev
    return ev


def _unregister_cancel(sid: str) -> None:
    _CANCEL_EVENTS.pop(sid, None)


USE_SANDBOX = os.environ.get("KIRA_SANDBOX", "") not in ("", "0", "false", "False")
if USE_SANDBOX:
    import sandbox_tools as toolkit
else:
    import agent_tools as toolkit

Q_URL = "https://q.us-east-1.amazonaws.com/?origin=KIRO_CLI"
ROOT = Path(__file__).parent
TOOL_SPECS = json.loads((ROOT / "agent_tool_specs.json").read_text())
# Subagents get every tool EXCEPT use_subagent itself (no recursion).
SUBAGENT_TOOL_SPECS = [t for t in TOOL_SPECS if t["toolSpecification"]["name"] != "use_subagent"]
MAX_SUBAGENT_PARALLEL = 4
MAX_SUBAGENT_TURNS = 12
_BASE_SYSTEM_PROMPT = (ROOT / "agent_system_prompt.txt").read_text()


def _build_system_prompt() -> str:
    skills_block = agent_skills.render_skills_section()
    if not skills_block:
        return _BASE_SYSTEM_PROMPT
    return (
        _BASE_SYSTEM_PROMPT.rstrip()
        + "\n\n## Skills\n\n"
        + "Skills are reusable playbooks for common task types. "
        + "When a task matches a skill's description, call the `load_skill` tool "
        + "to read its body BEFORE acting.\n\n"
        + skills_block
        + "\n"
    )


# NOTE: deliberately NOT a module-level cached snapshot. New skills created
# via POST /skills must take effect immediately for the next /agent call,
# not on next webchat restart. _build_system_prompt() reads skills/*.md
# each call (~15 file reads, sub-millisecond), so this is cheap.
WORKSPACES = ROOT / "workspaces"
WORKSPACES.mkdir(exist_ok=True)

# Session IDs are server-generated hex strings, but clients can also supply
# their own (e.g. from /agent/upload before a session exists). Validate strictly
# to prevent path traversal — anything not matching is rejected/replaced.
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _safe_sid(sid: str | None) -> str:
    if sid and _SID_RE.match(sid):
        return sid
    return uuid.uuid4().hex[:12]


# Default turn budget for /agent. Override globally via KIRA_MAX_TURNS,
# or per-request by passing max_turns in AgentRequest (capped by
# KIRA_MAX_TURNS_HARD). Bug-hunt / refactor tasks routinely need 40–60;
# everyday chat is fine at 25. See app.py for the per-request plumbing.
MAX_TURNS = int(os.environ.get('KIRA_MAX_TURNS', '25'))
MAX_TURNS_HARD = int(os.environ.get('KIRA_MAX_TURNS_HARD', '100'))


async def _llm_one_shot(
    api_key: str, prompt: str, model: str, system: str | None = None, max_tokens: int | None = None
) -> str:
    """Single-turn call with no tools and no history.

    Goes through the `llm/` provider layer (phase 3a of the provider-abstraction
    migration). Provider is chosen by KIRA_LLM_PROVIDER env (default: amazon-q).
    Tests can swap in MockProvider via `llm.register(...)` or by passing
    `extra={"provider": MockProvider(...)}` if we ever expose that hook.
    """
    from llm import Message, get_provider

    sys_text = system or "You are a helpful assistant. Be concise."
    messages = [
        Message(role="system", content=sys_text),
        Message(role="user", content=prompt),
    ]
    try:
        # api_key is passed for back-compat with tests that stub q_client at
        # the module level; QProvider will fall back to key_pool if None/empty.
        provider_name = os.environ.get("KIRA_LLM_PROVIDER", "amazon-q")
        if provider_name == "amazon-q":
            from llm.q_provider import QProvider

            provider = QProvider(api_key=key_pool.current() or api_key)
        else:
            provider = get_provider(provider_name)

        chunks: list[str] = []
        async for ev in provider.stream(messages, [], model=model, timeout=120):
            if ev.type == "text" and ev.text:
                chunks.append(ev.text)
            # throttle / usage / done events are ignored here
    except Exception as e:
        return f"[llm_one_shot error] {type(e).__name__}: {e}"
    out = "".join(chunks).strip()
    if max_tokens and len(out) > max_tokens * 4:
        out = out[: max_tokens * 4] + "\n... [truncated]"
    return out or "(empty response)"


def _maybe_diff(name: str, args: dict, backup_path: str | None) -> tuple[str | None, int]:
    """Return (unified_diff, lines_changed) for fs_write edits.
    Returns (None, 0) if not applicable or files too large."""
    if name != "fs_write" or not backup_path or not isinstance(args, dict):
        return None, 0
    cur_path = args.get("path")
    if not cur_path:
        return None, 0
    # If sandbox is on, paths inside /workspace and /host/... live in container;
    # backup_path was written in the same fs space. From the host process we can
    # only read /host/webchat paths (mounted on the host as the project dir) and
    # workspaces/<sid>/* on host. Try to map.
    import os as _os

    candidates = [cur_path]
    bcandidates = [backup_path]
    # /host/webchat/foo -> <project_root>/foo
    proj_root = str(__import__("pathlib").Path(__file__).resolve().parent)
    for arr, p in ((candidates, cur_path), (bcandidates, backup_path)):
        if p.startswith("/host/webchat/"):
            arr.append(proj_root + p[len("/host/webchat") :])
        if p.startswith("/workspace/"):
            arr.append(_os.path.join(proj_root, "workspaces", p[len("/workspace/") :]))

    def _read(paths):
        for p in paths:
            try:
                if _os.path.isfile(p) and _os.path.getsize(p) < 800_000:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        return f.read()
            except Exception:
                continue
        return None

    old = _read(bcandidates)
    new = _read(candidates)
    if old is None or new is None or old == new:
        return None, 0
    import difflib

    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile="before",
        tofile="after",
        n=3,
    )
    text = "".join(diff)
    if not text:
        return None, 0
    # cap to 80KB to keep SSE payload small
    if len(text) > 80_000:
        text = text[:80_000] + "\n... [diff truncated]"
    changed = sum(
        1
        for ln in text.splitlines()
        if (ln.startswith("+") or ln.startswith("-")) and not (ln.startswith("+++") or ln.startswith("---"))
    )
    return text, changed


def _load_plan(sid: str) -> dict:
    try:
        import agent_store

        p = agent_store.get_meta(sid, "plan", None)
        return p if isinstance(p, dict) else {"items": []}
    except Exception:
        return {"items": []}


def _handle_plan(sid: str, args: dict) -> tuple[str, str]:
    """Tool handler for `plan`. Returns (status, message).
    Operations:
      set: replace whole plan; args={"items": ["step1", ...]} or [{"text":..., "status":...}]
      update: args={"index": N, "status": "done|in_progress|skipped|pending", "text": optional}
      add:    args={"text": "...", "after": optional index}
      clear:  args={}
    """
    import agent_store

    op = (args.get("op") or "set").lower()
    plan = _load_plan(sid)
    items = plan.get("items", [])
    if op == "set":
        raw = args.get("items") or []
        items = []
        for it in raw:
            if isinstance(it, str):
                items.append({"text": it, "status": "pending"})
            elif isinstance(it, dict):
                items.append(
                    {
                        "text": str(it.get("text", "")),
                        "status": it.get("status", "pending"),
                    }
                )
        if not items:
            return "error", "plan.set requires non-empty items list"
    elif op == "update":
        idx = args.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(items):
            return "error", f"invalid index {idx}; plan has {len(items)} items"
        if "status" in args:
            items[idx]["status"] = args["status"]
        if "text" in args:
            items[idx]["text"] = args["text"]
    elif op == "add":
        text = args.get("text") or ""
        if not text:
            return "error", "plan.add requires text"
        new_item = {"text": text, "status": args.get("status", "pending")}
        after = args.get("after")
        if isinstance(after, int) and 0 <= after < len(items):
            items.insert(after + 1, new_item)
        else:
            items.append(new_item)
    elif op == "clear":
        items = []
    else:
        return "error", f"unknown plan op: {op}"
    plan = {"items": items}
    agent_store.set_meta(sid, "plan", plan)
    summary = (
        "\n".join(
            f"  [{i}] {('x' if it.get('status') == 'done' else '>' if it.get('status') == 'in_progress' else '-' if it.get('status') == 'skipped' else ' ')}"
            f" {it.get('text', '')}"
            for i, it in enumerate(items)
        )
        or "(empty)"
    )
    return "success", f"PLAN ({len(items)} items):\n{summary}"


def _sse(obj: dict) -> bytes:
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


def _q_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/x-amz-json-1.0",
        "tokentype": "API_KEY",
        "X-Amz-Target": "AmazonCodeWhispererStreamingService.GenerateAssistantResponse",
        "User-Agent": "aws-sdk-rust/1.3.14 os/linux AmazonQ-For-CLI/2.2.2",
        "Accept": "application/vnd.amazon.eventstream",
    }


def _parse_frames(buf: bytearray):
    while len(buf) >= 12:
        total_len, headers_len = struct.unpack(">II", bytes(buf[:8]))
        if total_len < 16 or total_len > 16 * 1024 * 1024:
            buf.clear()
            return
        if len(buf) < total_len:
            return
        msg = bytes(buf[:total_len])
        del buf[:total_len]
        headers = msg[12 : 12 + headers_len]
        payload = msg[12 + headers_len : total_len - 4]
        et = _event_type(headers)
        try:
            yield et, json.loads(payload.decode("utf-8", "replace"))
        except Exception:
            yield et, None


def _event_type(headers: bytes) -> str:
    i = 0
    et = ""
    while i < len(headers):
        nlen = headers[i]
        i += 1
        name = headers[i : i + nlen].decode("utf-8", "replace")
        i += nlen
        htype = headers[i]
        i += 1
        if htype == 7:
            vlen = struct.unpack(">H", headers[i : i + 2])[0]
            i += 2
            val = headers[i : i + vlen].decode("utf-8", "replace")
            i += vlen
            if name == ":event-type":
                et = val
        else:
            break
    return et


def _env_state(cwd: str) -> dict[str, str]:
    return {
        "operatingSystem": "linux" if platform.system() == "Linux" else platform.system().lower(),
        "currentWorkingDirectory": cwd,
    }


def _wrap_user_text(text: str) -> str:
    """Wrap raw user text in the CONTEXT ENTRY / USER MESSAGE markers Q expects.

    Exposed so the llm/ adapter path can produce the exact same wire content.
    Empty text → empty string (used for tool_results-only continuations).
    """
    if not text:
        return ""
    ts = datetime.now(UTC).isoformat()
    return (
        f"--- CONTEXT ENTRY BEGIN ---\n"
        f"Current time: {ts}\n"
        f"--- CONTEXT ENTRY END ---\n\n"
        f"--- USER MESSAGE BEGIN ---\n{text}--- USER MESSAGE END ---"
    )


def _user_msg(
    text: str,
    model: str,
    cwd: str,
    tool_results: list[dict] | None = None,
    tool_specs: list[dict] | None = None,
    images: list[dict] | None = None,
) -> dict:
    wrapped = _wrap_user_text(text)
    ctx: dict[str, Any] = {"envState": _env_state(cwd), "tools": tool_specs if tool_specs is not None else TOOL_SPECS}
    if tool_results is not None:
        ctx["toolResults"] = tool_results
    msg: dict[str, Any] = {
        "content": wrapped,
        "userInputMessageContext": ctx,
        "origin": "KIRO_CLI",
        "modelId": model,
    }
    if images:
        msg["images"] = images
    return {"userInputMessage": msg}


async def _handle_dev_loop(api_key, args, model, cwd, session_id, parent_tool_id, toolkit):
    """Implement the dev_loop tool: write code -> run tests -> fix -> repeat.

    Streams SSE events:
      dev_loop_iter   {parent_id, n, action: 'edit'|'test', summary}
      dev_loop_done   {parent_id, ok, iters, last_test, summary}
    Yields (sse_bytes_or_None, (status, output)_or_None).
    """
    task = (args or {}).get("task", "").strip()
    if not task:
        yield None, ("error", "task is required")
        return
    max_iters = int((args or {}).get("max_iters") or 5)
    max_iters = max(1, min(max_iters, 10))
    runner = (args or {}).get("runner")  # pytest / jest / go / None=auto
    target = (args or {}).get("target") or None
    test_path = (args or {}).get("path") or None
    relevant = (args or {}).get("relevant_context") or ""

    history: list[str] = []
    last_test_output = "(no run yet)"
    last_status = "unknown"

    def _build_subagent_query(iter_n: int) -> str:
        if iter_n == 0:
            head = (
                "You are inside a dev_loop. Your job: implement / fix the task "
                "below, then verify by running tests yourself if needed.\n\n"
                "After you finish editing, the dev_loop will automatically run "
                "the project tests and report the result back to you. Make "
                "focused edits; don't write long explanations.\n\n"
                f"TASK:\n{task}\n"
            )
        else:
            head = (
                f"You are inside a dev_loop, iteration {iter_n + 1}/{max_iters}. "
                "Previous edits did NOT make the tests pass. Read the test "
                "output below carefully, then make MINIMAL targeted edits to "
                "fix it. Do NOT rewrite unrelated code.\n\n"
                f"ORIGINAL TASK:\n{task}\n\n"
                f"TEST OUTPUT (latest):\n{last_test_output[:6000]}\n"
            )
        if relevant:
            head += f"\nAdditional context provided by caller:\n{relevant}\n"
        return head

    for n in range(max_iters):
        # ---- 1) ask subagent to edit ----
        q = _build_subagent_query(n)
        yield (
            _sse(
                {
                    "type": "dev_loop_iter",
                    "parent_id": parent_tool_id,
                    "n": n + 1,
                    "max": max_iters,
                    "action": "edit",
                    "summary": f"editing (iter {n + 1}/{max_iters})",
                }
            ),
            None,
        )
        try:
            sub_out = await _run_subagent_silent(api_key, q, model, cwd, session_id, relevant_context="")
        except Exception as e:
            yield None, ("error", f"dev_loop subagent failed: {e}")
            return
        history.append(f"# iter {n + 1} edit summary\n{sub_out[:1200]}")
        # ---- 2) run tests ----
        yield (
            _sse(
                {
                    "type": "dev_loop_iter",
                    "parent_id": parent_tool_id,
                    "n": n + 1,
                    "max": max_iters,
                    "action": "test",
                    "summary": f"running tests (iter {n + 1}/{max_iters})",
                }
            ),
            None,
        )
        test_args = {}
        if runner:
            test_args["runner"] = runner
        if target:
            test_args["target"] = target
        if test_path:
            test_args["path"] = test_path
        if USE_SANDBOX:
            t_status, t_out, _ = await asyncio.to_thread(toolkit.run_tool, "run_tests", test_args, cwd, session_id)
        else:
            t_status, t_out, _ = await asyncio.to_thread(toolkit.run_tool, "run_tests", test_args, cwd)
        last_test_output = t_out or ""
        last_status = t_status
        passed = t_status == "success" and "TESTS=PASS" in (t_out or "")
        yield (
            _sse(
                {
                    "type": "dev_loop_test",
                    "parent_id": parent_tool_id,
                    "n": n + 1,
                    "status": t_status,
                    "passed": passed,
                    "summary": (t_out or "").splitlines()[0][:200] if t_out else "",
                }
            ),
            None,
        )
        if passed:
            yield (
                _sse(
                    {
                        "type": "dev_loop_done",
                        "parent_id": parent_tool_id,
                        "ok": True,
                        "iters": n + 1,
                        "summary": f"PASS after {n + 1} iter(s)",
                    }
                ),
                None,
            )
            yield None, ("success", f"DEV_LOOP=PASS iters={n + 1}\nFinal test output:\n{(t_out or '')[:4000]}")
            return
    # exhausted
    yield (
        _sse(
            {
                "type": "dev_loop_done",
                "parent_id": parent_tool_id,
                "ok": False,
                "iters": max_iters,
                "summary": f"FAIL after {max_iters} iter(s)",
            }
        ),
        None,
    )
    yield (
        None,
        (
            "error",
            f"DEV_LOOP=FAIL iters={max_iters} last_status={last_status}\nLast test output:\n{last_test_output[:4000]}",
        ),
    )


async def _handle_subagent(api_key, args, model, cwd, session_id, parent_tool_id):
    """Generator: yields (sse_bytes_or_None, (status, output)_or_None).

    Emits intermediate `subagent_progress` events to the client and finally a
    single (status, output) tuple to be wrapped as the parent tool_result.
    """
    cmd = (args or {}).get("command", "")
    if cmd == "ListAgents":
        # Single default agent available; mirror the format the model expects.
        info = {
            "agents": [
                {
                    "name": "default",
                    "description": "Default Kiro CLI agent. Has the same tools as the parent but without use_subagent.",
                    "tools": [t["toolSpecification"]["name"] for t in SUBAGENT_TOOL_SPECS],
                }
            ]
        }
        yield None, ("success", json.dumps(info, ensure_ascii=False, indent=2))
        return
    if cmd != "InvokeSubagents":
        yield None, ("error", f"unknown use_subagent command: {cmd!r}")
        return
    content = (args or {}).get("content") or {}
    subs = content.get("subagents") or []
    if not subs:
        yield None, ("error", "no subagents specified")
        return
    subs = subs[:MAX_SUBAGENT_PARALLEL]

    yield (
        _sse(
            {
                "type": "subagent_start",
                "parent_id": parent_tool_id,
                "count": len(subs),
                "queries": [s.get("query", "")[:200] for s in subs],
            }
        ),
        None,
    )

    async def _one(idx, spec):
        q = spec.get("query", "")
        ctx = spec.get("relevant_context", "") or ""
        try:
            text = await _run_subagent_silent(api_key, q, model, cwd, session_id, ctx)
            return idx, "success", text
        except Exception as e:
            return idx, "error", f"{type(e).__name__}: {e}"

    tasks = [asyncio.create_task(_one(i, s)) for i, s in enumerate(subs)]
    results: list[tuple[int, str, str]] = []
    for fut in asyncio.as_completed(tasks):
        idx, status, text = await fut
        results.append((idx, status, text))
        yield (
            _sse(
                {
                    "type": "subagent_done",
                    "parent_id": parent_tool_id,
                    "index": idx,
                    "status": status,
                    "preview": text[:300],
                }
            ),
            None,
        )

    results.sort(key=lambda r: r[0])
    blocks = []
    for idx, status, text in results:
        q = subs[idx].get("query", "")
        blocks.append(f"=== Subagent #{idx + 1} [{status}] ===\nquery: {q}\n\n{text}")
    combined = "\n\n".join(blocks)
    overall = "success" if all(r[1] == "success" for r in results) else "error"
    yield None, (overall, combined)


async def _run_subagent_silent(
    api_key: str,
    query: str,
    model: str,
    cwd: str,
    session_id: str,
    relevant_context: str = "",
) -> str:
    """Run a subagent loop in isolation; return final text reply.

    Reuses the same sandbox session/workspace but a fresh conversation
    (separate conversationId, fresh history). Subagents do NOT see
    use_subagent in their tool list, preventing recursion.

    Phase 3b: routes through `llm.get_provider()` instead of q_client directly.
    The provider receives canonical messages (`list[Message]`); QProvider
    converts to Q wire format internally. KIRA_LLM_PROVIDER env switches
    backends. Default = amazon-q (== old behaviour, bit-exact wire format).
    """
    from llm import Message, get_provider, toolspecs_from_openai_json
    from llm.q_provider import QProvider

    conv_id = str(uuid.uuid4())
    cont_id = str(uuid.uuid4())
    sub_prompt = query
    if relevant_context:
        sub_prompt = f"{query}\n\nAdditional context:\n{relevant_context}"

    # Canonical message history. System role gets the agent system prompt;
    # the first user message carries the (wrapped) query. We keep the same
    # CONTEXT ENTRY wrapping the old _user_msg used so the model sees the
    # exact same prompt format.
    messages: list[Message] = [
        Message(role="system", content=_build_system_prompt()),
        Message(role="user", content=_wrap_user_text(sub_prompt)),
    ]
    # Subagent tools (no use_subagent recursion) in canonical shape.
    # We pass raw Q-shape specs as-is; toolspecs_from_openai_json handles both.
    sub_tool_specs = toolspecs_from_openai_json(SUBAGENT_TOOL_SPECS)

    provider_name = os.environ.get("KIRA_LLM_PROVIDER", "amazon-q")
    if provider_name == "amazon-q":
        provider = QProvider(api_key=key_pool.current() or api_key)
    else:
        provider = get_provider(provider_name)

    final_text: list[str] = []

    for _ in range(MAX_SUBAGENT_TURNS):
        text_chunks: list[str] = []
        tool_calls_seen: list = []  # list[ToolCall]
        try:
            async for ev in provider.stream(
                messages,
                sub_tool_specs,
                model=model,
                extra={
                    "conversation_id": conv_id,
                    "continuation_id": cont_id,
                    "env_state": _env_state(cwd),
                },
            ):
                if ev.type == "text" and ev.text:
                    text_chunks.append(ev.text)
                elif ev.type == "tool_call" and ev.tool is not None:
                    tool_calls_seen.append(ev.tool)
                # throttle / usage / error events: ignored in silent subagent
        except Exception as e:
            return f"[subagent error] {type(e).__name__}: {e}"

        final_text.append("".join(text_chunks))
        if not tool_calls_seen:
            return "".join(final_text).strip() or "(subagent produced no output)"

        # Persist assistant turn (text + tool_calls) into canonical history.
        messages.append(
            Message(
                role="assistant",
                content="".join(text_chunks),
                tool_calls=list(tool_calls_seen),
            )
        )

        # Execute each tool, append a tool-result message.
        for tc in tool_calls_seen:
            name = tc.name
            args = tc.arguments
            if USE_SANDBOX:
                status, out, _imgs = await asyncio.to_thread(toolkit.run_tool, name, args, cwd, session_id)
            else:
                status, out, _imgs = await asyncio.to_thread(toolkit.run_tool, name, args, cwd)
            try:
                import agent_store as _st

                bak = None
                if isinstance(out, str) and "[BACKUP=" in out:
                    bak = out.split("[BACKUP=", 1)[1].split("]", 1)[0]
                _st.log_action(
                    session_id,
                    name,
                    args,
                    ok=(status == "success"),
                    error=None if status == "success" else (out or "")[:500],
                    file=(args.get("path") if isinstance(args, dict) else None),
                    backup=bak,
                )
            except Exception:
                pass
            messages.append(
                Message(
                    role="tool",
                    content=out or "",
                    tool_call_id=tc.id,
                    name=name,
                )
            )
        # An empty user turn carries the tool_results forward (QProvider
        # attaches buffered tool messages to the next user turn's context).
        messages.append(Message(role="user", content=""))
    return "".join(final_text).strip() + "\n[subagent: max turns reached]"


async def run_agent(
    api_key: str,
    prompt: str,
    model: str = "claude-opus-4.7",
    session_id: str | None = None,
    history: list[dict] | None = None,
    images: list[dict] | None = None,
    max_turns: int | None = None,
) -> AsyncIterator[bytes]:
    """Phase 3c.3: main agent loop is provider-agnostic.

    Internally we keep a single source of truth — a canonical `list[Message]`
    built from the caller's Q-dict `history`. Each turn calls
    `provider.stream(messages, tools, model=...)` which yields `StreamEvent`s
    (text / tool_call / throttle / metering / context_usage / usage / done).

    On the way out we sync `history` (the Q-dict the caller / app.py keeps
    cached and persists in SQLite) by rewriting it in-place from
    `messages_to_q_history(...)`. SQLite still stores Q dicts so existing
    transcripts open unchanged; the loop itself no longer touches Q-shape
    events anywhere.

    Switching `KIRA_LLM_PROVIDER` now actually swaps the wire format end-to-end.
    """
    session_id = _safe_sid(session_id)
    # Effective turn budget: caller wins, clamped to [1, MAX_TURNS_HARD],
    # default = MAX_TURNS (KIRA_MAX_TURNS env). Negative / None falls back.
    effective_max_turns = MAX_TURNS
    if max_turns is not None and max_turns > 0:
        effective_max_turns = min(int(max_turns), MAX_TURNS_HARD)
    # Kill-switch: if the freeze flag is set, refuse before allocating workspace
    # or touching the LLM. Emit a single SSE error frame + done.
    if agent_freeze.is_frozen():
        yield _sse({"type": "meta", "session_id": session_id, "model": model})
        yield _sse(agent_freeze.reason_for_sse())
        yield _sse({"type": "done"})
        return
    cwd_path = (WORKSPACES / session_id).resolve()
    if not str(cwd_path).startswith(str(WORKSPACES.resolve()) + os.sep):
        raise ValueError("invalid session_id")
    cwd_path.mkdir(parents=True, exist_ok=True)
    cwd = "/workspace" if USE_SANDBOX else str(cwd_path)

    conv_id = str(uuid.uuid4())
    cont_id = str(uuid.uuid4())

    from llm import get_provider
    from llm.base import Message as _M
    from llm.base import ToolCall as _TC
    from llm.base import toolspecs_from_openai_json
    from llm.q_provider import QProvider, messages_to_q_history, q_history_to_messages

    provider_name = os.environ.get("KIRA_LLM_PROVIDER", "amazon-q")
    if provider_name == "amazon-q":
        # Honour live key rotation in prod.
        _main_provider = QProvider(api_key=key_pool.current() or api_key)
    else:
        _main_provider = get_provider(provider_name)
    _canon_tools = toolspecs_from_openai_json(TOOL_SPECS)

    if history is None:
        history = []

    # Build canonical message list from caller's Q-dict history.
    messages: list = q_history_to_messages(history) if history else []
    if not messages:
        messages.append(_M(role="system", content=_build_system_prompt()))

    # Helper: keep the caller-visible Q-dict history in sync with `messages`.
    # We rewrite it in place so cached references (app.py's _AGENT_SESSIONS)
    # and save_session() observe the new state.
    def _sync_history_dict() -> None:
        new = messages_to_q_history(messages, wrap_text=True)
        history.clear()
        history.extend(new)

    yield _sse({"type": "meta", "session_id": session_id, "cwd": cwd, "model": model})

    # Safety-net: if the last assistant turn left tool_calls unfulfilled (the
    # previous turn died mid-flight), inject synthetic error tool messages so
    # the next request doesn't 400 with "tool_use ids without tool_result".
    if messages and messages[-1].role == "assistant" and messages[-1].tool_calls:
        for tc in messages[-1].tool_calls:
            messages.append(
                _M(
                    role="tool",
                    content="[interrupted: previous turn aborted; tool was not executed]",
                    tool_call_id=tc.id,
                    name="error",
                )
            )
        # Empty user turn carries the buffered tool messages to QProvider.
        messages.append(_M(role="user", content=""))
        _sync_history_dict()

    credits = 0.0
    context_pct = 0.0
    pending_images: list[dict] = list(images) if images else []
    pending_images_for_provider: list[dict] | None = pending_images or None

    # The "current" turn the model is about to consume. We append it to
    # `messages` only after the request succeeds (so a mid-stream failure
    # doesn't poison history with a duplicate user prompt).
    current_user = _M(role="user", content=prompt or "")
    pending_images = []

    cancel_ev = _register_cancel(session_id)

    def _is_cancelled() -> bool:
        return cancel_ev.is_set()

    # Bug 1 workaround: Sonnet/Opus frequently emit a single plan.update tool_call
    # then end_turn with no further tools, which trips the "no tool_calls -> done"
    # branch below and leaves the actual task untouched. If the previous round
    # executed ONLY the plan tool and the model now emits zero tools, inject a
    # one-shot nudge and continue the loop instead of returning done.
    last_round_only_plan = False
    consecutive_plan_only_rounds = 0  # «plan→plan→plan» loop guard
    plan_nudge_budget = 2  # at most two automatic nudges per /agent call

    try:
        for turn in range(effective_max_turns):
            if _is_cancelled():
                yield _sse({"type": "cancelled"})
                yield _sse({"type": "stats", "credits": credits, "context_pct": context_pct, "turns": turn})
                yield _sse({"type": "done"})
                return

            text_chunks: list[str] = []
            tool_calls_emitted: list = []  # list[ToolCall]
            message_id: str | None = None
            cancelled_mid_stream = False
            extra = {
                "conversation_id": conv_id,
                "continuation_id": cont_id,
                "env_state": _env_state(cwd),
            }
            if pending_images_for_provider:
                extra["images"] = pending_images_for_provider

            try:
                async for ev in _main_provider.stream(
                    messages + [current_user],
                    _canon_tools,
                    model=model,
                    cancel=cancel_ev,
                    extra=extra,
                ):
                    if ev.type == "cancelled":
                        cancelled_mid_stream = True
                        break
                    if ev.type == "text" and ev.text:
                        text_chunks.append(ev.text)
                        yield _sse({"type": "text", "delta": ev.text})
                    elif ev.type == "tool_call" and ev.tool is not None:
                        tool_calls_emitted.append(ev.tool)
                    elif ev.type == "throttle":
                        meta = ev.meta or {}
                        yield _sse(
                            {
                                "type": "throttle",
                                "reason": meta.get("reason"),
                                "attempt": meta.get("attempt"),
                                "sleep": meta.get("sleep"),
                            }
                        )
                    elif ev.type == "metering":
                        credits += float((ev.meta or {}).get("credits", 0) or 0)
                    elif ev.type == "context_usage":
                        context_pct = float((ev.meta or {}).get("context_pct", 0) or 0)
                    elif ev.type == "message_id":
                        mid = (ev.meta or {}).get("message_id")
                        if mid:
                            message_id = mid
                    elif ev.type == "error":
                        # Non-fatal provider errors — log via SSE, continue stream.
                        yield _sse(
                            {
                                "type": "error",
                                "message": str((ev.meta or {}).get("message", "provider error")),
                            }
                        )
                    # 'usage' / 'done' events: nothing to do here (loop ends naturally).
            except Exception as e:
                # If `current_user` was an empty turn carrying tool_results (the
                # continuation after a previous assistant.tool_calls), they're
                # already in `messages` as role='tool' entries — keep them so
                # the next request doesn't see orphan tool_uses.
                _sync_history_dict()
                err_evt: dict = {
                    "type": "error",
                    "message": f"{type(e).__name__}: {e}",
                }
                # If this is a QHttpError, surface the full upstream body so
                # ValidationException-style 400s aren't blind (Tokyo-card demo
                # bug). The frontend renders `body` as a code block when set.
                status = getattr(e, "status", None)
                body = getattr(e, "body", None)
                if status is not None:
                    err_evt["status"] = status
                if body:
                    err_evt["body"] = body[:4000]
                yield _sse(err_evt)
                return

            pending_images_for_provider = None  # one-shot per turn

            if cancelled_mid_stream or _is_cancelled():
                # Persist whatever the assistant produced so history stays consistent.
                if text_chunks or tool_calls_emitted or current_user.content:
                    messages.append(current_user)
                    messages.append(
                        _M(
                            role="assistant",
                            content="".join(text_chunks),
                            name=message_id,
                            # Tools weren't executed → drop the tool_calls so we
                            # don't leave orphans for the next turn.
                            tool_calls=[],
                        )
                    )
                    _sync_history_dict()
                yield _sse({"type": "cancelled"})
                yield _sse({"type": "stats", "credits": credits, "context_pct": context_pct, "turns": turn + 1})
                yield _sse({"type": "done"})
                return

            message_id = message_id or uuid.uuid4().hex

            # ----- final answer (no tools): commit + return ------------------
            if not tool_calls_emitted:
                full_text = "".join(text_chunks)
                short_text = len(full_text.strip()) < 240
                # Detect 'I will save / I created / let me write ...' style
                # promises that were not followed by an actual tool call.
                # Covers EN + RU + fenced code blocks (model pasted code in chat).
                _promise_re = re.compile(
                    r"(?i)(\bI'?ll (?:now |just )?(?:save|create|write|append|run|commit)\b"
                    r"|\blet me (?:save|create|write|append|run|now)\b"
                    r"|\bsaving (?:it|this|the file) (?:now|next)\b"
                    r"|\b(?:taking|grabbing) (?:a |the )?screenshot\b"
                    r"|\bcontinuing\b|\bproceeding\b"
                    r"|сохран[яюёе][юте]?\b|создам\b|создаю\b|напишу\b|сейчас\s+сохран"
                    r"|записываю\b|записать\b|сейчас\s+создам"
                    # 2026-05-13: демо-баг «Cyberpunk wiki» — агент писал
                    # «продолжаю — делаю скриншот» и обрывал ход без вызова.
                    r"|делаю\b|сделаю\b|продолжаю\b|открываю\b|открою\b"
                    r"|читаю\b|прочитаю\b|захожу\b|зайду\b|проверяю\b"
                    r"|сейчас\s+(?:сделаю|запиш|открою|зайду|прочитаю))"
                )
                has_code_block = full_text.count("```") >= 2
                promised_action = bool(_promise_re.search(full_text)) or has_code_block
                should_nudge = plan_nudge_budget > 0 and (
                    (last_round_only_plan and short_text)
                    or promised_action
                )
                if should_nudge:
                    plan_nudge_budget -= 1
                    last_round_only_plan = False
                    if last_round_only_plan or short_text:
                        reason = "plan-only round produced no follow-up tool"
                    else:
                        reason = "text-only answer described an action without performing it"
                    nudge = (
                        "Continue. You described an action but did not call the matching "
                        "tool. Perform it NOW: if you wrote file contents in chat, call "
                        "fs_write / patch to actually save them. If you said you would run "
                        "a command, call execute_bash. If you said the plan is set, mark the "
                        "first step in_progress and call the tool that step requires. Do "
                        "NOT just acknowledge -- emit the tool call. If the task is genuinely "
                        "complete, mark every plan item done and reply with one short line."
                    )
                    messages.append(current_user)
                    messages.append(
                        _M(role="assistant", content=full_text, name=message_id, tool_calls=[])
                    )
                    _sync_history_dict()
                    yield _sse({"type": "plan_nudge", "reason": reason})
                    current_user = _M(role="user", content=nudge)
                    pending_images_for_provider = None
                    continue
                messages.append(current_user)
                messages.append(
                    _M(role="assistant", content="".join(text_chunks), name=message_id, tool_calls=[])
                )
                _sync_history_dict()
                yield _sse({"type": "stats", "credits": credits, "context_pct": context_pct, "turns": turn + 1})
                yield _sse({"type": "done"})
                return

            # Cost-limit check between assistant turn and tool execution.
            if _cost_limit_exceeded is not None:
                hit = _cost_limit_exceeded(session_id, credits)
                if hit:
                    # Still commit the assistant turn (without the un-executed tool_calls)
                    # so the next turn doesn't see orphans.
                    messages.append(current_user)
                    messages.append(
                        _M(role="assistant", content="".join(text_chunks), name=message_id, tool_calls=[])
                    )
                    _sync_history_dict()
                    yield _sse({"type": "error", "message": hit})
                    yield _sse({"type": "stats", "credits": credits, "context_pct": context_pct, "turns": turn + 1})
                    yield _sse({"type": "done"})
                    return

            # ----- execute tools, stream tool_results ------------------------
            # Tool-call dispatch goes through agent_tool_handlers's registry.
            # Each handler is a self-contained async generator that yields:
            #   * bytes — raw SSE frames written straight to the client;
            #   * ToolResult — terminator (status / output / images / diff /
            #     action_id / extra_sse fields merged into the tool_result frame).
            # The loop body here only builds the per-call context, drives the
            # handler, emits the standardised tool_result, and appends a
            # canonical `tool` Message to history. Adding a new specialised
            # built-in is now a single function + a `register("name", fn)` call.
            from agent_tool_handlers import ToolContext, ToolResult, get as _get_handler

            tool_results_for_history: list[_M] = []

            for tc in tool_calls_emitted:
                tid = tc.id
                name = tc.name
                args = tc.arguments or {}
                yield _sse({"type": "tool_call", "id": tid, "name": name, "input": args})

                ctx = ToolContext(
                    api_key=api_key,
                    model=model,
                    cwd=cwd,
                    session_id=session_id,
                    tool_use_id=tid,
                    name=name,
                    args=args,
                    use_sandbox=USE_SANDBOX,
                    toolkit=toolkit,
                    handle_subagent=_handle_subagent,
                    handle_dev_loop=_handle_dev_loop,
                    llm_one_shot=_llm_one_shot,
                    handle_plan=_handle_plan,
                    load_plan=_load_plan,
                    sse=_sse,
                    maybe_diff=_maybe_diff,
                )

                result: ToolResult | None = None
                async for item in _get_handler(name)(ctx):
                    if isinstance(item, (bytes, bytearray)):
                        yield item
                    elif isinstance(item, ToolResult):
                        result = item
                if result is None:
                    # Defensive: a handler that forgets the terminator would
                    # leave an orphan tool_use behind. Surface it.
                    result = ToolResult(
                        status="error",
                        output=f"handler for {name!r} returned no result",
                    )

                if result.images:
                    pending_images.extend(result.images)

                yield _sse({
                    "type": "tool_result", "id": tid,
                    "status": result.status, "output": result.output,
                    **result.extra_sse,
                })
                tool_results_for_history.append(
                    _M(role="tool", content=result.output or "", tool_call_id=tid, name=result.status)
                )

            # ----- commit the round-trip into canonical history --------------
            messages.append(current_user)
            messages.append(
                _M(
                    role="assistant",
                    content="".join(text_chunks),
                    name=message_id,
                    tool_calls=[
                        _TC(id=tc.id, name=tc.name, arguments=tc.arguments or {})
                        for tc in tool_calls_emitted
                    ],
                )
            )
            messages.extend(tool_results_for_history)
            _sync_history_dict()

            # Track "plan-only" rounds for the nudge above.
            executed_names = {tc.name for tc in tool_calls_emitted}
            last_round_only_plan = executed_names == {"plan"}
            if last_round_only_plan:
                consecutive_plan_only_rounds += 1
            else:
                consecutive_plan_only_rounds = 0

            # 2026-05-13 demo bug — plan→plan→plan→… loops bypass the
            # text-only nudge branch because each round technically *did*
            # emit a tool call (`plan`). Inject a user-level nudge after the
            # second consecutive plan-only round so the model is forced to
            # execute the FIRST step instead of replanning forever. Cheap:
            # consumes the same nudge budget the text-only branch uses.
            if (
                consecutive_plan_only_rounds >= 2
                and plan_nudge_budget > 0
                and turn < effective_max_turns - 1
            ):
                plan_nudge_budget -= 1
                consecutive_plan_only_rounds = 0
                yield _sse({
                    "type": "plan_nudge",
                    "reason": "plan-only loop — forcing action on next turn",
                })
                current_user = _M(
                    role="user",
                    content=(
                        "You have called `plan` repeatedly without any other tool "
                        "call. Stop replanning. Execute the FIRST step from your "
                        "current plan NOW by calling the matching tool "
                        "(browser_screenshot, fs_write, execute_bash, mem_remember, "
                        "etc.). Do not call plan again on this turn."
                    ),
                )
                pending_images_for_provider = None
                continue

            yield _sse({"type": "stats", "credits": credits, "context_pct": context_pct, "turns": turn + 1})

            # Next turn: empty user message; the buffered tool_results above
            # get attached to it inside QProvider.messages_to_q_body.
            next_images = pending_images or None
            pending_images = []
            current_user = _M(role="user", content="")
            pending_images_for_provider = next_images

        yield _sse({"type": "error", "message": "max turns reached"})
    except asyncio.CancelledError:
        raise
    except Exception as e:
        yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}"})
    finally:
        _unregister_cancel(session_id)
