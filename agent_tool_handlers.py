"""Tool-handler registry for the runtime's main loop.

Background
----------
Before this module the agent loop had a 250-LOC if-chain dispatching tool
calls by name: built-ins (llm_one_shot, output_iframe, plan), delegated
generators (use_subagent, dev_loop), the auto-critic + hooks + sandbox
toolkit path. Every branch duplicated SSE emission + agent_store.log_action +
tool-result message construction. Adding a tool meant patching the same
hot function in a fifth place.

Contract
--------
Each handler is an async generator that yields one of two things:

  * `bytes` — already-encoded SSE frame, written to the client as-is.
  * `ToolResult` — terminator object. Carries the canonical `tool` Message
                   plus action_id / backup / diff metadata for SSE.

The terminator MUST be yielded exactly once and last. The runtime then:
  1. emits the standardised `tool_result` SSE frame from the terminator,
  2. appends `result.tool_msg` to canonical history.

This keeps the main loop tiny (it dispatches and threads SSE), while every
new tool is one self-contained ~30-line async function registered via
`register("name", handler)`.

The `_default_handler` covers everything not in the registry: critic-on-commit
+ pre/post hooks + sandbox toolkit invocation + diff capture + log_action.
That branch is the one most tools land in.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from llm.base import Message as _M


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ToolContext:
    """Everything a handler needs. Keeps signatures stable as the loop evolves."""

    api_key: str
    model: str
    cwd: str
    session_id: str
    tool_use_id: str
    name: str
    args: dict
    use_sandbox: bool
    toolkit: Any
    # Lazily-loaded helpers — passed in to avoid circular imports.
    handle_subagent: Callable[..., AsyncIterator[tuple]]
    handle_dev_loop: Callable[..., AsyncIterator[tuple]]
    llm_one_shot: Callable[..., Any]
    handle_plan: Callable[[str, dict], tuple[str, str]]
    load_plan: Callable[[str], dict]
    sse: Callable[[dict], bytes]
    maybe_diff: Callable[[str, dict, str | None], tuple[str | None, int]]


@dataclass
class ToolResult:
    """Terminator yielded by every handler.

    The runtime turns this into the standardised tool_result SSE frame
    plus an appended canonical `tool` Message in conversation history.
    """

    status: str  # "success" | "error"
    output: str
    images: list[dict] | None = None
    action_id: int | None = None
    backup: str | None = None
    diff: str | None = None
    diff_lines: int = 0
    extra_sse: dict = field(default_factory=dict)  # optional fields merged into the frame


# ---------------------------------------------------------------------------
# Tiny helper — every handler logs to the action journal
# ---------------------------------------------------------------------------


def _log(ctx: ToolContext, *, ok: bool, error: str | None, **kw) -> int | None:
    try:
        import agent_store as _st

        return _st.log_action(
            ctx.session_id, kw.pop("override_name", ctx.name), kw.pop("override_args", ctx.args),
            ok=ok, error=error, tool_use_id=ctx.tool_use_id, **kw,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------


async def _h_llm_one_shot(ctx: ToolContext) -> AsyncIterator[bytes | ToolResult]:
    sub_model = (ctx.args.get("model") or "claude-haiku-4.5").strip()
    if sub_model.startswith("q/"):
        sub_model = sub_model[2:]
    out = await ctx.llm_one_shot(
        ctx.api_key,
        ctx.args.get("prompt") or "",
        sub_model,
        system=ctx.args.get("system"),
        max_tokens=ctx.args.get("max_tokens"),
    )
    status = "error" if out.startswith("[llm_one_shot error]") else "success"
    _log(ctx, ok=(status == "success"), error=None if status == "success" else out[:500])
    yield ToolResult(status=status, output=out)


async def _h_output_iframe(ctx: ToolContext) -> AsyncIterator[bytes | ToolResult]:
    html = ctx.args.get("html") or ""
    title = ctx.args.get("title") or ""
    if not html:
        status, out = "error", "html is required"
    else:
        status, out = "success", f"Rendered iframe '{title}' ({len(html)} bytes)"
    _log(
        ctx, ok=(status == "success"), error=None if status == "success" else out,
        override_args={"title": title, "html_len": len(html)},
    )
    yield ctx.sse({"type": "iframe", "id": ctx.tool_use_id, "title": title, "html": html})
    yield ToolResult(status=status, output=out)


async def _h_plan(ctx: ToolContext) -> AsyncIterator[bytes | ToolResult]:
    status, out = ctx.handle_plan(ctx.session_id, ctx.args)
    _log(ctx, ok=(status == "success"), error=None if status == "success" else out[:500])
    yield ctx.sse({"type": "plan", "plan": ctx.load_plan(ctx.session_id)})
    yield ToolResult(status=status, output=out)


async def _h_use_subagent(ctx: ToolContext) -> AsyncIterator[bytes | ToolResult]:
    status, out_text = "success", ""
    async for ev_b, out in ctx.handle_subagent(
        ctx.api_key, ctx.args, ctx.model, ctx.cwd, ctx.session_id, ctx.tool_use_id
    ):
        if ev_b is not None:
            yield ev_b
        if out is not None:
            status, out_text = out
    yield ToolResult(status=status, output=out_text)


async def _h_dev_loop(ctx: ToolContext) -> AsyncIterator[bytes | ToolResult]:
    status, out_text = "success", ""
    async for ev_b, out in ctx.handle_dev_loop(
        ctx.api_key, ctx.args, ctx.model, ctx.cwd, ctx.session_id, ctx.tool_use_id, ctx.toolkit
    ):
        if ev_b is not None:
            yield ev_b
        if out is not None:
            status, out_text = out
            _log(ctx, ok=(status == "success"), error=None if status == "success" else out_text[:500])
    yield ToolResult(status=status, output=out_text)


# ---------------------------------------------------------------------------
# Default handler: critic + hooks + toolkit + diff capture
# ---------------------------------------------------------------------------


async def _critic_on_git_commit(ctx: ToolContext) -> AsyncIterator[bytes | str]:
    """Yield SSE 'critic' frames, terminate with a `str` deny-reason or empty."""
    if ctx.name != "git_commit" or os.environ.get("KIRA_CRITIC_AUTO", "0") not in ("1", "true", "True"):
        yield ""  # no critic, no block
        return
    try:
        import agent_critic

        commit_repo = (ctx.args.get("path") if isinstance(ctx.args, dict) else None) or "/workspace"
        diff_text = ""
        if ctx.use_sandbox:
            try:
                import sandbox_runtime as sb_rt

                await asyncio.to_thread(
                    sb_rt.exec_argv, ctx.session_id,
                    ["git", "-c", "safe.directory=*", "add", "-A"], commit_repo, 30,
                )
                r = await asyncio.to_thread(
                    sb_rt.exec_argv, ctx.session_id,
                    ["git", "-c", "safe.directory=*", "-c", "core.pager=cat", "diff", "--cached"],
                    commit_repo, 30,
                )
                diff_text = r[1]
            except Exception:
                diff_text = ""
        else:
            import subprocess

            repo_cwd = commit_repo if (commit_repo and commit_repo != "/workspace") else (ctx.cwd or ".")
            subprocess.run(
                ["git", "-c", "safe.directory=*", "-C", repo_cwd, "add", "-A"],
                capture_output=True, text=True, timeout=30,
            )
            r = subprocess.run(
                ["git", "-c", "safe.directory=*", "-c", "core.pager=cat", "-C", repo_cwd, "diff", "--cached"],
                capture_output=True, text=True, timeout=30,
            )
            diff_text = r.stdout
        verdict = await agent_critic.review_diff(
            ctx.api_key, diff_text,
            intent=(ctx.args.get("message") if isinstance(ctx.args, dict) else "") or "",
        )
        yield ctx.sse({
            "type": "critic", "id": ctx.tool_use_id,
            "verdict": verdict.get("verdict"), "reason": verdict.get("reason", ""),
            "issues": verdict.get("issues", []),
        })
        yield (verdict.get("reason") or "critic blocked the commit") if verdict.get("verdict") == "BLOCK" else ""
    except Exception as e:
        yield ctx.sse({
            "type": "critic", "id": ctx.tool_use_id,
            "verdict": "OK", "reason": f"critic-error: {e}", "issues": [],
        })
        yield ""


async def _default_handler(ctx: ToolContext) -> AsyncIterator[bytes | ToolResult]:
    import agent_hooks

    # ---- critic gate ----
    deny_msg: str | None = None
    async for ev_or_str in _critic_on_git_commit(ctx):
        if isinstance(ev_or_str, bytes):
            yield ev_or_str
        else:
            deny_msg = ev_or_str or None

    # ---- pre_tool hooks ----
    try:
        pre_events = agent_hooks.run_pre_tool(ctx.session_id, ctx.name, ctx.args)
    except Exception as e:
        pre_events = [{
            "hook_id": "_error", "event": "pre_tool", "type": "log",
            "message": f"hook error: {e}", "tool": ctx.name,
        }]
    for ev_hook in pre_events:
        yield ctx.sse({**ev_hook, "type": "hook", "id": ctx.tool_use_id, "action_type": ev_hook.get("type")})
        if ev_hook.get("type") == "deny":
            deny_msg = ev_hook.get("message") or "denied by hook"

    if deny_msg is not None:
        out = f"HOOK_DENY: {deny_msg}"
        _log(
            ctx, ok=False, error=deny_msg[:500],
            override_name="_hook_deny",
            override_args={"tool": ctx.name, "message": deny_msg, "args": ctx.args},
        )
        yield ToolResult(status="error", output=out)
        return

    # ---- run the actual tool ----
    if ctx.use_sandbox:
        status, out, imgs = await asyncio.to_thread(
            ctx.toolkit.run_tool, ctx.name, ctx.args, ctx.cwd, ctx.session_id
        )
    else:
        status, out, imgs = await asyncio.to_thread(ctx.toolkit.run_tool, ctx.name, ctx.args, ctx.cwd)

    # ---- post_tool hooks ----
    try:
        post_events = agent_hooks.run_post_tool(ctx.session_id, ctx.name, ctx.args, status, out)
    except Exception as e:
        post_events = [{
            "hook_id": "_error", "event": "post_tool", "type": "log",
            "message": f"hook error: {e}", "tool": ctx.name,
        }]
    for ev_hook in post_events:
        yield ctx.sse({**ev_hook, "type": "hook", "id": ctx.tool_use_id, "action_type": ev_hook.get("type")})

    # ---- diff capture + action logging ----
    bak: str | None = None
    if isinstance(out, str) and "[BACKUP=" in out:
        bak = out.split("[BACKUP=", 1)[1].split("]", 1)[0]
    diff_text, diff_lines = (None, 0)
    try:
        if status == "success":
            diff_text, diff_lines = ctx.maybe_diff(ctx.name, ctx.args, bak)
    except Exception:
        pass
    action_id = _log(
        ctx,
        ok=(status == "success"),
        error=None if status == "success" else (out or "")[:500],
        file=(ctx.args.get("path") if isinstance(ctx.args, dict) else None),
        backup=bak, diff=diff_text,
    )
    extra: dict = {"has_image": bool(imgs)}
    if action_id is not None:
        extra["action_id"] = action_id
    if bak:
        extra["backup"] = bak
    if diff_text:
        extra["diff"] = diff_text
        extra["diff_lines"] = diff_lines
    yield ToolResult(
        status=status, output=out or "", images=imgs,
        action_id=action_id, backup=bak, diff=diff_text, diff_lines=diff_lines,
        extra_sse=extra,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_HANDLERS: dict[str, Callable[[ToolContext], AsyncIterator[Any]]] = {
    "llm_one_shot": _h_llm_one_shot,
    "output_iframe": _h_output_iframe,
    "plan": _h_plan,
    "use_subagent": _h_use_subagent,
    "dev_loop": _h_dev_loop,
}


def register(name: str, handler: Callable[[ToolContext], AsyncIterator[Any]]) -> None:
    """Register a tool handler. Overwrites if name already registered."""
    _HANDLERS[name] = handler


def get(name: str) -> Callable[[ToolContext], AsyncIterator[Any]]:
    """Return the handler for `name`, falling back to the default
    (critic + hooks + toolkit + diff capture)."""
    return _HANDLERS.get(name, _default_handler)


def names() -> list[str]:
    """Inspection helper."""
    return sorted(_HANDLERS)


__all__ = ["ToolContext", "ToolResult", "register", "get", "names"]
