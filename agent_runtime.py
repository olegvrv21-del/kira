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
import platform
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

import q_client
import agent_skills
import agent_hooks
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
SUBAGENT_TOOL_SPECS = [t for t in TOOL_SPECS
                      if t["toolSpecification"]["name"] != "use_subagent"]
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


SYSTEM_PROMPT = _build_system_prompt()
WORKSPACES = ROOT / "workspaces"
WORKSPACES.mkdir(exist_ok=True)

MAX_TURNS = 25


async def _llm_one_shot(api_key: str, prompt: str, model: str,
                       system: str | None = None,
                       max_tokens: int | None = None) -> str:
    """Single-turn call to Q with no tools and no history.
    Returns final assistant text. Used by the `llm_one_shot` tool.
    """
    cwd = "/workspace"
    history = []
    sys_text = system or "You are a helpful assistant. Be concise."
    history.append({"userInputMessage": {
        "content": sys_text,
        "userInputMessageContext": {"envState": _env_state(cwd)},
        "origin": "KIRO_CLI",
        "modelId": model,
    }})
    current = _user_msg(prompt, model, cwd)
    body = {"conversationState": {
        "chatTriggerType": "MANUAL",
        "conversationId": str(uuid.uuid4()),
        "agentContinuationId": str(uuid.uuid4()),
        "agentTaskType": "vibe",
        "history": history,
        "currentMessage": current,
    }}
    chunks: list[str] = []
    try:
        async for et, payload in q_client.stream_q(key_pool.current() or api_key, body, timeout=120):
            if et == "_throttle":
                continue
            if et == "assistantResponseEvent" and isinstance(payload, dict):
                c = payload.get("content", "")
                if c:
                    chunks.append(c)
    except Exception as e:
        return f"[llm_one_shot error] {type(e).__name__}: {e}"
    out = "".join(chunks).strip()
    if max_tokens and len(out) > max_tokens * 4:
        out = out[: max_tokens * 4] + "\n... [truncated]"
    return out or "(empty response)"


def _maybe_diff(name: str, args: dict, backup_path: str | None
                ) -> tuple[str | None, int]:
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
            arr.append(proj_root + p[len("/host/webchat"):])
        if p.startswith("/workspace/"):
            arr.append(_os.path.join(proj_root, "workspaces", p[len("/workspace/"):]))
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
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile="before", tofile="after", n=3,
    )
    text = "".join(diff)
    if not text:
        return None, 0
    # cap to 80KB to keep SSE payload small
    if len(text) > 80_000:
        text = text[:80_000] + "\n... [diff truncated]"
    changed = sum(1 for ln in text.splitlines()
                  if (ln.startswith("+") or ln.startswith("-"))
                  and not (ln.startswith("+++") or ln.startswith("---")))
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
                items.append({
                    "text": str(it.get("text", "")),
                    "status": it.get("status", "pending"),
                })
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
    summary = "\n".join(
        f"  [{i}] {('x' if it.get('status')=='done' else '>' if it.get('status')=='in_progress' else '-' if it.get('status')=='skipped' else ' ')}"
        f" {it.get('text','')}"
        for i, it in enumerate(items)
    ) or "(empty)"
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
        headers = msg[12:12 + headers_len]
        payload = msg[12 + headers_len:total_len - 4]
        et = _event_type(headers)
        try:
            yield et, json.loads(payload.decode("utf-8", "replace"))
        except Exception:
            yield et, None


def _event_type(headers: bytes) -> str:
    i = 0
    et = ""
    while i < len(headers):
        nlen = headers[i]; i += 1
        name = headers[i:i + nlen].decode("utf-8", "replace"); i += nlen
        htype = headers[i]; i += 1
        if htype == 7:
            vlen = struct.unpack(">H", headers[i:i + 2])[0]; i += 2
            val = headers[i:i + vlen].decode("utf-8", "replace"); i += vlen
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


def _user_msg(text: str, model: str, cwd: str, tool_results: list[dict] | None = None,
              tool_specs: list[dict] | None = None,
              images: list[dict] | None = None) -> dict:
    ts = datetime.now(timezone.utc).isoformat()
    wrapped = (
        f"--- CONTEXT ENTRY BEGIN ---\n"
        f"Current time: {ts}\n"
        f"--- CONTEXT ENTRY END ---\n\n"
        f"--- USER MESSAGE BEGIN ---\n{text}--- USER MESSAGE END ---"
    ) if text else ""
    ctx: dict[str, Any] = {"envState": _env_state(cwd),
                            "tools": tool_specs if tool_specs is not None else TOOL_SPECS}
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


async def _handle_dev_loop(api_key, args, model, cwd, session_id, parent_tool_id,
                            toolkit):
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
        yield _sse({"type": "dev_loop_iter", "parent_id": parent_tool_id,
                     "n": n + 1, "max": max_iters,
                     "action": "edit",
                     "summary": f"editing (iter {n+1}/{max_iters})"}), None
        try:
            sub_out = await _run_subagent_silent(
                api_key, q, model, cwd, session_id,
                relevant_context="")
        except Exception as e:
            yield None, ("error", f"dev_loop subagent failed: {e}")
            return
        history.append(f"# iter {n+1} edit summary\n{sub_out[:1200]}")
        # ---- 2) run tests ----
        yield _sse({"type": "dev_loop_iter", "parent_id": parent_tool_id,
                     "n": n + 1, "max": max_iters,
                     "action": "test",
                     "summary": f"running tests (iter {n+1}/{max_iters})"}), None
        test_args = {}
        if runner: test_args["runner"] = runner
        if target: test_args["target"] = target
        if test_path: test_args["path"] = test_path
        if USE_SANDBOX:
            t_status, t_out, _ = await asyncio.to_thread(
                toolkit.run_tool, "run_tests", test_args, cwd, session_id)
        else:
            t_status, t_out, _ = await asyncio.to_thread(
                toolkit.run_tool, "run_tests", test_args, cwd)
        last_test_output = t_out or ""
        last_status = t_status
        passed = (t_status == "success" and "TESTS=PASS" in (t_out or ""))
        yield _sse({"type": "dev_loop_test", "parent_id": parent_tool_id,
                     "n": n + 1, "status": t_status,
                     "passed": passed,
                     "summary": (t_out or "").splitlines()[0][:200] if t_out else ""}), None
        if passed:
            yield _sse({"type": "dev_loop_done", "parent_id": parent_tool_id,
                         "ok": True, "iters": n + 1,
                         "summary": f"PASS after {n+1} iter(s)"}), None
            yield None, ("success",
                f"DEV_LOOP=PASS iters={n+1}\nFinal test output:\n{(t_out or '')[:4000]}")
            return
    # exhausted
    yield _sse({"type": "dev_loop_done", "parent_id": parent_tool_id,
                 "ok": False, "iters": max_iters,
                 "summary": f"FAIL after {max_iters} iter(s)"}), None
    yield None, ("error",
        f"DEV_LOOP=FAIL iters={max_iters} last_status={last_status}\n"
        f"Last test output:\n{last_test_output[:4000]}")


async def _handle_subagent(api_key, args, model, cwd, session_id, parent_tool_id):
    """Generator: yields (sse_bytes_or_None, (status, output)_or_None).

    Emits intermediate `subagent_progress` events to the client and finally a
    single (status, output) tuple to be wrapped as the parent tool_result.
    """
    cmd = (args or {}).get("command", "")
    if cmd == "ListAgents":
        # Single default agent available; mirror the format the model expects.
        info = {
            "agents": [{
                "name": "default",
                "description": "Default Kiro CLI agent. Has the same tools as the parent but without use_subagent.",
                "tools": [t["toolSpecification"]["name"] for t in SUBAGENT_TOOL_SPECS],
            }]
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

    yield _sse({"type": "subagent_start", "parent_id": parent_tool_id,
                "count": len(subs),
                "queries": [s.get("query", "")[:200] for s in subs]}), None

    async def _one(idx, spec):
        q = spec.get("query", "")
        ctx = spec.get("relevant_context", "") or ""
        try:
            text = await _run_subagent_silent(api_key, q, model, cwd,
                                              session_id, ctx)
            return idx, "success", text
        except Exception as e:
            return idx, "error", f"{type(e).__name__}: {e}"

    tasks = [asyncio.create_task(_one(i, s)) for i, s in enumerate(subs)]
    results: list[tuple[int, str, str]] = []
    for fut in asyncio.as_completed(tasks):
        idx, status, text = await fut
        results.append((idx, status, text))
        yield _sse({"type": "subagent_done", "parent_id": parent_tool_id,
                    "index": idx, "status": status,
                    "preview": text[:300]}), None

    results.sort(key=lambda r: r[0])
    blocks = []
    for idx, status, text in results:
        q = subs[idx].get("query", "")
        blocks.append(f"=== Subagent #{idx+1} [{status}] ===\nquery: {q}\n\n{text}")
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
    """
    conv_id = str(uuid.uuid4())
    cont_id = str(uuid.uuid4())
    sub_prompt = query
    if relevant_context:
        sub_prompt = f"{query}\n\nAdditional context:\n{relevant_context}"

    history = [{
        "userInputMessage": {
            "content": SYSTEM_PROMPT,
            "userInputMessageContext": {"envState": _env_state(cwd)},
            "origin": "KIRO_CLI",
            "modelId": model,
        }
    }]
    current = _user_msg(sub_prompt, model, cwd, tool_specs=SUBAGENT_TOOL_SPECS)
    final_text: list[str] = []

    for _ in range(MAX_SUBAGENT_TURNS):
        body = {"conversationState": {
                "chatTriggerType": "MANUAL",
                "conversationId": conv_id,
                "agentContinuationId": cont_id,
                "agentTaskType": "vibe",
            "history": history,
            "currentMessage": current,
        }}
        text_chunks: list[str] = []
        tool_uses: dict[str, dict] = {}
        tool_order: list[str] = []
        message_id = None
        try:
            async for et, payload in q_client.stream_q(key_pool.current() or api_key, body):
                if et == "_throttle":
                    continue
                if not isinstance(payload, dict):
                    continue
                if et == "assistantResponseEvent":
                    c = payload.get("content", "")
                    if c:
                        text_chunks.append(c)
                    mid = payload.get("messageId")
                    if mid:
                        message_id = mid
                elif et == "toolUseEvent":
                    tid = payload.get("toolUseId")
                    if not tid:
                        continue
                    if tid not in tool_uses:
                        tool_uses[tid] = {"toolUseId": tid,
                                          "name": payload.get("name", ""),
                                          "_input_str": ""}
                        tool_order.append(tid)
                    inp = payload.get("input")
                    if inp:
                        tool_uses[tid]["_input_str"] += inp
                    if payload.get("stop"):
                        raw = tool_uses[tid]["_input_str"]
                        try:
                            tool_uses[tid]["input"] = json.loads(raw) if raw else {}
                        except Exception as e:
                            tool_uses[tid]["input"] = {"_parse_error": str(e), "_raw": raw}
        except Exception as e:
            return f"[subagent error] {type(e).__name__}: {e}"

        final_text.append("".join(text_chunks))
        message_id = message_id or uuid.uuid4().hex
        if not tool_order:
            return "".join(final_text).strip() or "(subagent produced no output)"
        results = []
        for tid in tool_order:
            tu = tool_uses[tid]
            name = tu["name"]
            args = tu.get("input", {})
            if USE_SANDBOX:
                status, out, _imgs = await asyncio.to_thread(
                    toolkit.run_tool, name, args, cwd, session_id)
            else:
                status, out, _imgs = await asyncio.to_thread(
                    toolkit.run_tool, name, args, cwd)
            try:
                import agent_store as _st
                bak = None
                if isinstance(out, str) and "[BACKUP=" in out:
                    bak = out.split("[BACKUP=",1)[1].split("]",1)[0]
                _st.log_action(session_id, name, args,
                               ok=(status == "success"),
                               error=None if status == "success" else (out or "")[:500],
                               file=(args.get("path") if isinstance(args, dict) else None),
                               backup=bak)
            except Exception:
                pass
            # (subagent path: no per-tool diff stream; only main loop streams diffs.)
            results.append({"toolUseId": tid,
                            "content": [{"text": out}],
                            "status": status})
        history.append(current)
        history.append({"assistantResponseMessage": {
            "messageId": message_id,
            "content": "".join(text_chunks),
            "toolUses": [{
                "toolUseId": tool_uses[tid]["toolUseId"],
                "name": tool_uses[tid]["name"],
                "input": tool_uses[tid].get("input", {}),
            } for tid in tool_order],
        }})
        current = _user_msg("", model, cwd, tool_results=results,
                             tool_specs=SUBAGENT_TOOL_SPECS)
    return "".join(final_text).strip() + "\n[subagent: max turns reached]"


async def run_agent(
    api_key: str,
    prompt: str,
    model: str = "claude-opus-4.7",
    session_id: str | None = None,
    history: list[dict] | None = None,
    images: list[dict] | None = None,
) -> AsyncIterator[bytes]:
    session_id = session_id or uuid.uuid4().hex[:12]
    cwd_path = (WORKSPACES / session_id).resolve()
    cwd_path.mkdir(parents=True, exist_ok=True)
    # Inside sandbox the agent sees /workspace; outside it sees the host path.
    cwd = "/workspace" if USE_SANDBOX else str(cwd_path)

    conv_id = str(uuid.uuid4())
    cont_id = str(uuid.uuid4())

    if history is None:
        history = []
    if not history:
        history.append({
            "userInputMessage": {
                "content": SYSTEM_PROMPT,
                "userInputMessageContext": {"envState": _env_state(cwd)},
                "origin": "KIRO_CLI",
                "modelId": model,
            }
        })

    yield _sse({"type": "meta", "session_id": session_id, "cwd": cwd, "model": model})

    # Safety-net: if the last assistant turn in history has toolUses that were
    # never followed by a user toolResults (e.g. a previous turn died with 500),
    # inject synthetic 'interrupted' tool_results before sending the new prompt.
    # Otherwise Bedrock returns 400: tool_use ids were found without tool_result.
    if history:
        last = history[-1]
        arm = last.get("assistantResponseMessage") or {}
        orphan = [tu for tu in (arm.get("toolUses") or []) if tu.get("toolUseId")]
        if orphan:
            stub_results = [{
                "toolUseId": tu["toolUseId"],
                "content": [{"text": "[interrupted: previous turn aborted; tool was not executed]"}],
                "status": "error",
            } for tu in orphan]
            history.append(_user_msg("", model, cwd, tool_results=stub_results))

    credits = 0.0
    context_pct = 0.0
    pending_images: list[dict] = list(images) if images else []
    current = _user_msg(prompt, model, cwd, images=pending_images or None)
    pending_images = []

    cancel_ev = _register_cancel(session_id)

    def _is_cancelled() -> bool:
        return cancel_ev.is_set()

    try:
        for turn in range(MAX_TURNS):
            if _is_cancelled():
                yield _sse({"type": "cancelled"})
                yield _sse({"type": "stats", "credits": credits,
                            "context_pct": context_pct, "turns": turn})
                yield _sse({"type": "done"})
                return
            body = {"conversationState": {
                "chatTriggerType": "MANUAL",
                "conversationId": conv_id,
                "agentContinuationId": cont_id,
                "agentTaskType": "vibe",
                "history": history,
                "currentMessage": current,
            }}
            text_chunks: list[str] = []
            tool_uses: dict[str, dict] = {}
            tool_order: list[str] = []
            message_id = None

            cancelled_mid_stream = False
            try:
                async for et, payload in q_client.stream_q(key_pool.current() or api_key, body, cancel_event=cancel_ev):
                    if et == "_cancelled":
                        cancelled_mid_stream = True
                        break
                    if et == "_throttle":
                        yield _sse({"type": "throttle",
                                    "reason": payload.get("reason"),
                                    "attempt": payload.get("attempt"),
                                    "sleep": payload.get("sleep")})
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if et == "assistantResponseEvent":
                        c = payload.get("content", "")
                        if c:
                            text_chunks.append(c)
                            yield _sse({"type": "text", "delta": c})
                        mid = payload.get("messageId")
                        if mid:
                            message_id = mid
                    elif et == "toolUseEvent":
                        tid = payload.get("toolUseId")
                        if not tid:
                            continue
                        if tid not in tool_uses:
                            tool_uses[tid] = {
                                "toolUseId": tid,
                                "name": payload.get("name", ""),
                                "_input_str": "",
                            }
                            tool_order.append(tid)
                        inp = payload.get("input")
                        if inp:
                            tool_uses[tid]["_input_str"] += inp
                        if payload.get("stop"):
                            raw = tool_uses[tid]["_input_str"]
                            try:
                                tool_uses[tid]["input"] = json.loads(raw) if raw else {}
                            except Exception as e:
                                tool_uses[tid]["input"] = {"_parse_error": str(e), "_raw": raw}
                    elif et == "meteringEvent":
                        credits += float(payload.get("usage", 0))
                    elif et == "contextUsageEvent":
                        context_pct = float(payload.get("contextUsagePercentage", 0))
            except Exception as e:
                # If `current` carries toolResults (continuation of a previous
                # assistant.toolUses), we MUST persist it before returning.
                # Otherwise next request sees orphan toolUses and Bedrock 400s.
                try:
                    ctx = (current.get("userInputMessage") or {}).get(
                        "userInputMessageContext") or {}
                    if ctx.get("toolResults"):
                        history.append(current)
                except Exception:
                    pass
                yield _sse({"type": "error",
                            "message": f"{type(e).__name__}: {e}"})
                return

            if cancelled_mid_stream or _is_cancelled():
                # Persist whatever the assistant produced so history stays consistent.
                ctx = (current.get("userInputMessage") or {}).get(
                    "userInputMessageContext") or {}
                carries_tool_results = bool(ctx.get("toolResults"))
                if text_chunks or tool_order or carries_tool_results:
                    history.append(current)
                    history.append({
                        "assistantResponseMessage": {
                            "messageId": message_id or uuid.uuid4().hex,
                            "content": "".join(text_chunks),
                            "toolUses": [],  # tools weren't executed
                        }
                    })
                yield _sse({"type": "cancelled"})
                yield _sse({"type": "stats", "credits": credits,
                            "context_pct": context_pct, "turns": turn + 1})
                yield _sse({"type": "done"})
                return

            message_id = message_id or uuid.uuid4().hex
            if not tool_order:
                history.append(current)
                history.append({
                    "assistantResponseMessage": {
                        "messageId": message_id,
                        "content": "".join(text_chunks),
                        "toolUses": [],
                    }
                })
                yield _sse({"type": "stats", "credits": credits,
                            "context_pct": context_pct, "turns": turn + 1})
                yield _sse({"type": "done"})
                return

            # cost limit check (after each assistant turn before tools)
            if _cost_limit_exceeded is not None:
                hit = _cost_limit_exceeded(session_id, credits)
                if hit:
                    yield _sse({"type": "error", "message": hit})
                    yield _sse({"type": "stats", "credits": credits,
                                "context_pct": context_pct, "turns": turn + 1})
                    yield _sse({"type": "done"})
                    return

            # execute tools
            results = []
            for tid in tool_order:
                tu = tool_uses[tid]
                name = tu["name"]
                args = tu.get("input", {})
                yield _sse({"type": "tool_call", "id": tid,
                            "name": name, "input": args})
                if name == "llm_one_shot":
                    sub_model = (args.get("model") or "claude-haiku-4.5").strip()
                    if sub_model.startswith("q/"):
                        sub_model = sub_model[2:]
                    sub_prompt = args.get("prompt") or ""
                    sub_system = args.get("system")
                    sub_max = args.get("max_tokens")
                    out = await _llm_one_shot(api_key, sub_prompt, sub_model,
                                              system=sub_system, max_tokens=sub_max)
                    status = "error" if out.startswith("[llm_one_shot error]") else "success"
                    try:
                        import agent_store as _st
                        _st.log_action(session_id, name, args,
                                       ok=(status == "success"),
                                       error=None if status == "success" else out[:500],
                                       tool_use_id=tid)
                    except Exception:
                        pass
                    yield _sse({"type": "tool_result", "id": tid,
                                "status": status, "output": out})
                    results.append({"toolUseId": tid,
                                    "content": [{"text": out}],
                                    "status": status})
                    continue
                if name == "output_iframe":
                    html = args.get("html") or ""
                    title = args.get("title") or ""
                    if not html:
                        status, out = "error", "html is required"
                    else:
                        status, out = "success", f"Rendered iframe '{title}' ({len(html)} bytes)"
                    try:
                        import agent_store as _st
                        _st.log_action(session_id, name, {"title": title,
                                                          "html_len": len(html)},
                                       ok=(status == "success"),
                                       error=None if status == "success" else out,
                                       tool_use_id=tid)
                    except Exception:
                        pass
                    # The frontend renders this via the special 'iframe' SSE event.
                    yield _sse({"type": "iframe", "id": tid,
                                "title": title, "html": html})
                    yield _sse({"type": "tool_result", "id": tid,
                                "status": status, "output": out})
                    results.append({"toolUseId": tid,
                                    "content": [{"text": out}],
                                    "status": status})
                    continue
                if name == "plan":
                    status, out = _handle_plan(session_id, args)
                    try:
                        import agent_store as _st
                        _st.log_action(session_id, name, args,
                                       ok=(status == "success"),
                                       error=None if status == "success" else out[:500])
                    except Exception:
                        pass
                    yield _sse({"type": "plan",
                                "plan": _load_plan(session_id)})
                    yield _sse({"type": "tool_result", "id": tid,
                                "status": status, "output": out})
                    results.append({"toolUseId": tid,
                                    "content": [{"text": out}],
                                    "status": status})
                    continue
                if name == "use_subagent":
                    async for ev_b, out in _handle_subagent(
                            api_key, args, model, cwd, session_id, tid):
                        if ev_b is not None:
                            yield ev_b
                        if out is not None:
                            status, out_text = out
                            yield _sse({"type": "tool_result", "id": tid,
                                        "status": status, "output": out_text})
                            results.append({"toolUseId": tid,
                                            "content": [{"text": out_text}],
                                            "status": status})
                    continue
                if name == "dev_loop":
                    async for ev_b, out in _handle_dev_loop(
                            api_key, args, model, cwd, session_id, tid, toolkit):
                        if ev_b is not None:
                            yield ev_b
                        if out is not None:
                            status, out_text = out
                            try:
                                import agent_store as _st
                                _st.log_action(session_id, name, args,
                                               ok=(status == "success"),
                                               error=None if status == "success" else out_text[:500],
                                               tool_use_id=tid)
                            except Exception:
                                pass
                            yield _sse({"type": "tool_result", "id": tid,
                                        "status": status, "output": out_text})
                            results.append({"toolUseId": tid,
                                            "content": [{"text": out_text}],
                                            "status": status})
                    continue
                # ---- pre_tool hooks ----
                pre_events = []
                deny_msg = None
                try:
                    pre_events = agent_hooks.run_pre_tool(session_id, name, args)
                except Exception as e:
                    pre_events = [{"hook_id": "_error", "event": "pre_tool",
                                     "type": "log",
                                     "message": f"hook error: {e}", "tool": name}]
                for ev_hook in pre_events:
                    yield _sse({**ev_hook, "type": "hook", "id": tid,
                                 "action_type": ev_hook.get("type")})
                    if ev_hook.get("type") == "deny":
                        deny_msg = ev_hook.get("message") or "denied by hook"
                if deny_msg is not None:
                    out = f"HOOK_DENY: {deny_msg}"
                    status, imgs = "error", None
                    try:
                        import agent_store as _st
                        _st.log_action(session_id, "_hook_deny",
                                       {"tool": name, "message": deny_msg,
                                        "args": args},
                                       ok=False, error=deny_msg[:500],
                                       tool_use_id=tid)
                    except Exception:
                        pass
                elif USE_SANDBOX:
                    status, out, imgs = await asyncio.to_thread(
                        toolkit.run_tool, name, args, cwd, session_id)
                else:
                    status, out, imgs = await asyncio.to_thread(
                        toolkit.run_tool, name, args, cwd)
                # ---- post_tool hooks ----
                try:
                    post_events = agent_hooks.run_post_tool(
                        session_id, name, args, status, out)
                except Exception as e:
                    post_events = [{"hook_id": "_error", "event": "post_tool",
                                     "type": "log",
                                     "message": f"hook error: {e}", "tool": name}]
                for ev_hook in post_events:
                    yield _sse({**ev_hook, "type": "hook", "id": tid,
                                 "action_type": ev_hook.get("type")})
                if imgs:
                    pending_images.extend(imgs)
                bak = None
                if isinstance(out, str) and "[BACKUP=" in out:
                    bak = out.split("[BACKUP=",1)[1].split("]",1)[0]
                diff_text, diff_lines = (None, 0)
                try:
                    if status == "success":
                        diff_text, diff_lines = _maybe_diff(name, args, bak)
                except Exception:
                    pass
                action_id = None
                try:
                    import agent_store as _st
                    action_id = _st.log_action(
                        session_id, name, args,
                        ok=(status == "success"),
                        error=None if status == "success" else (out or "")[:500],
                        file=(args.get("path") if isinstance(args, dict) else None),
                        backup=bak, diff=diff_text, tool_use_id=tid)
                except Exception:
                    pass
                ev = {"type": "tool_result", "id": tid,
                      "status": status, "output": out,
                      "has_image": bool(imgs)}
                if action_id is not None:
                    ev["action_id"] = action_id
                if bak:
                    ev["backup"] = bak
                if diff_text:
                    ev["diff"] = diff_text
                    ev["diff_lines"] = diff_lines
                yield _sse(ev)
                results.append({
                    "toolUseId": tid,
                    "content": [{"text": out}],
                    "status": status,
                })

            history.append(current)
            history.append({
                "assistantResponseMessage": {
                    "messageId": message_id,
                    "content": "".join(text_chunks),
                    "toolUses": [{
                        "toolUseId": tool_uses[tid]["toolUseId"],
                        "name": tool_uses[tid]["name"],
                        "input": tool_uses[tid].get("input", {}),
                    } for tid in tool_order],
                }
            })
            yield _sse({"type": "stats", "credits": credits,
                        "context_pct": context_pct, "turns": turn + 1})
            next_images = pending_images or None
            pending_images = []
            current = _user_msg("", model, cwd, tool_results=results,
                                 images=next_images)

        yield _sse({"type": "error", "message": "max turns reached"})
    except asyncio.CancelledError:
        # Client TCP drop. Don't yield further (response gone). Just clean up.
        raise
    except Exception as e:
        yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}"})
    finally:
        _unregister_cancel(session_id)
