"""Hooks system for Кира.

Lifecycle events:
  - on_session_start: fired the first time a session ID is seen in a request
  - pre_tool:         before a tool runs; can DENY (skip + synth error result)
  - post_tool:        after a tool runs; sees status + output (truncated)
  - on_session_end:   (not used yet)

Config file: $KIRA_HOOKS_CONFIG (default: ./hooks.json). Format:

  {
    "hooks": [
      {
        "id": "deny-etc-write",         # optional, for UI
        "event": "pre_tool",
        "match": {"tool": "fs_write",
                  "args_regex": {"path": "^/etc/"}},
        "action": {"type": "deny", "message": "editing /etc is forbidden"}
      },
      {
        "event": "post_tool",
        "match": {"tool": "git_commit", "status": "success"},
        "action": {"type": "log", "message": "commit by tool"}
      },
      {
        "event": "pre_tool",
        "match": {"tool": "execute_bash",
                  "args_regex": {"command": "rm\\s+-rf\\s+/"}},
        "action": {"type": "deny", "message": "rm -rf / blocked"}
      },
      {
        "event": "post_tool",
        "match": {"tool": "fs_write"},
        "action": {"type": "shell",
                   "cmd": "echo $KIRA_HOOK_TOOL >> /tmp/kira-fs.log"}
      }
    ]
  }

Matching rules (all must hold):
  tool         exact string OR list of strings
  status       "success" | "error"            (post_tool only)
  args_regex   {arg_name: python_regex}        regex must match repr(value)
  output_regex python regex applied to output  (post_tool only)

Actions:
  deny    {message}            — pre_tool only; tool is skipped, an error
                                  result containing `message` is returned to
                                  the model. Sets a DENY badge on the action.
  log     {message}            — emits an SSE "hook" event and a notebook line.
  shell   {cmd, timeout?}      — runs bash -c <cmd> with KIRA_HOOK_* env vars.
                                  Off unless KIRA_HOOKS_ALLOW_SHELL=1.
                                  If shell exits non-zero on pre_tool, that's
                                  treated as deny (stderr -> message).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.environ.get("KIRA_HOOKS_CONFIG", str(Path(__file__).parent / "hooks.json")))
ALLOW_SHELL = os.environ.get("KIRA_HOOKS_ALLOW_SHELL", "0") in ("1", "true", "True")

_CACHE: dict[str, Any] = {"mtime": 0.0, "hooks": []}


def _load() -> list[dict]:
    p = CONFIG_PATH
    if not p.exists():
        _CACHE["mtime"], _CACHE["hooks"] = 0.0, []
        return []
    mt = p.stat().st_mtime
    if _CACHE["mtime"] == mt and _CACHE["hooks"] is not None:
        return _CACHE["hooks"]
    try:
        data = json.loads(p.read_text("utf-8"))
        hooks = list(data.get("hooks", []))
    except Exception as e:
        # bad config = no hooks; surface via log_message side-channel.
        _CACHE["err"] = f"hooks.json parse error: {e}"
        hooks = []
    _CACHE["mtime"], _CACHE["hooks"] = mt, hooks
    return hooks


def _match(h: dict, event: str, tool: str, args: dict, status: str | None = None, output: str | None = None) -> bool:
    if h.get("event") != event:
        return False
    m = h.get("match") or {}
    t = m.get("tool")
    if t is not None:
        if isinstance(t, list):
            if tool not in t:
                return False
        elif tool != t:
            return False
    if "status" in m and m["status"] != status:
        return False
    ar = m.get("args_regex") or {}
    if ar:
        if not isinstance(args, dict):
            return False
        for k, pat in ar.items():
            v = args.get(k, "")
            try:
                if not re.search(pat, str(v)):
                    return False
            except re.error:
                return False
    if "output_regex" in m and output is not None:
        try:
            if not re.search(m["output_regex"], str(output)):
                return False
        except re.error:
            return False
    return True


def _run_shell(cmd: str, env_extra: dict[str, str], timeout: int = 10) -> tuple[int, str, str]:
    env = {**os.environ, **{k: str(v)[:4000] for k, v in env_extra.items()}}
    try:
        r = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"hook shell timeout after {timeout}s"
    except Exception as e:
        return 1, "", str(e)


def _trim(s: str | None, n: int = 4000) -> str:
    if not s:
        return ""
    s = str(s)
    return s if len(s) <= n else s[: n // 2] + "\n...[trimmed]...\n" + s[-n // 2 :]


def run_pre_tool(sid: str, tool: str, args: dict) -> list[dict]:
    """Return list of events. If any event has type='deny', caller must skip the
    tool call and synthesize an error result with the given message.
    """
    out = []
    for h in _load():
        if not _match(h, "pre_tool", tool, args):
            continue
        action = h.get("action") or {}
        atype = action.get("type")
        hid = h.get("id") or f"{atype}:{tool}"
        if atype == "deny":
            msg = action.get("message") or "denied by hook"
            out.append({"hook_id": hid, "event": "pre_tool", "type": "deny", "message": msg, "tool": tool})
            return out  # short-circuit
        if atype == "log":
            out.append(
                {"hook_id": hid, "event": "pre_tool", "type": "log", "message": action.get("message", ""), "tool": tool}
            )
        if atype == "shell":
            if not ALLOW_SHELL:
                out.append(
                    {
                        "hook_id": hid,
                        "event": "pre_tool",
                        "type": "log",
                        "message": "shell hook skipped (KIRA_HOOKS_ALLOW_SHELL=0)",
                        "tool": tool,
                    }
                )
                continue
            rc, so, se = _run_shell(
                action["cmd"],
                {
                    "KIRA_HOOK_EVENT": "pre_tool",
                    "KIRA_HOOK_TOOL": tool,
                    "KIRA_HOOK_SID": sid,
                    "KIRA_HOOK_ARGS": json.dumps(args)[:4000],
                },
                timeout=int(action.get("timeout", 10)),
            )
            if rc != 0:
                out.append(
                    {
                        "hook_id": hid,
                        "event": "pre_tool",
                        "type": "deny",
                        "message": (se or so or "shell hook denied").strip()[:500],
                        "tool": tool,
                    }
                )
                return out
            out.append(
                {
                    "hook_id": hid,
                    "event": "pre_tool",
                    "type": "shell",
                    "message": (so or "").strip()[:500],
                    "tool": tool,
                }
            )
    return out


def run_post_tool(sid: str, tool: str, args: dict, status: str, output: str | None) -> list[dict]:
    out = []
    for h in _load():
        if not _match(h, "post_tool", tool, args, status=status, output=output):
            continue
        action = h.get("action") or {}
        atype = action.get("type")
        hid = h.get("id") or f"{atype}:{tool}"
        if atype == "log":
            out.append(
                {
                    "hook_id": hid,
                    "event": "post_tool",
                    "type": "log",
                    "message": action.get("message", ""),
                    "tool": tool,
                    "status": status,
                }
            )
        elif atype == "shell":
            if not ALLOW_SHELL:
                out.append(
                    {
                        "hook_id": hid,
                        "event": "post_tool",
                        "type": "log",
                        "message": "shell hook skipped (KIRA_HOOKS_ALLOW_SHELL=0)",
                        "tool": tool,
                        "status": status,
                    }
                )
                continue
            rc, so, se = _run_shell(
                action["cmd"],
                {
                    "KIRA_HOOK_EVENT": "post_tool",
                    "KIRA_HOOK_TOOL": tool,
                    "KIRA_HOOK_SID": sid,
                    "KIRA_HOOK_ARGS": json.dumps(args)[:4000],
                    "KIRA_HOOK_STATUS": status,
                    "KIRA_HOOK_OUTPUT": _trim(output, 4000),
                },
                timeout=int(action.get("timeout", 10)),
            )
            out.append(
                {
                    "hook_id": hid,
                    "event": "post_tool",
                    "type": "shell",
                    "rc": rc,
                    "message": (so or se or "").strip()[:500],
                    "tool": tool,
                    "status": status,
                }
            )
    return out


def run_session_start(sid: str) -> list[dict]:
    out = []
    for h in _load():
        if h.get("event") != "on_session_start":
            continue
        action = h.get("action") or {}
        atype = action.get("type")
        hid = h.get("id") or f"{atype}:session_start"
        if atype == "log":
            out.append(
                {"hook_id": hid, "event": "on_session_start", "type": "log", "message": action.get("message", "")}
            )
        elif atype == "shell" and ALLOW_SHELL:
            rc, so, se = _run_shell(
                action["cmd"],
                {"KIRA_HOOK_EVENT": "on_session_start", "KIRA_HOOK_SID": sid},
                timeout=int(action.get("timeout", 10)),
            )
            out.append(
                {
                    "hook_id": hid,
                    "event": "on_session_start",
                    "type": "shell",
                    "rc": rc,
                    "message": (so or se).strip()[:500],
                }
            )
    return out


def list_hooks() -> list[dict]:
    """Return loaded hook definitions for inspection (UI endpoint)."""
    return _load()


def hooks_status() -> dict:
    _load()
    return {
        "config": str(CONFIG_PATH),
        "exists": CONFIG_PATH.exists(),
        "count": len(_CACHE.get("hooks", [])),
        "allow_shell": ALLOW_SHELL,
        "error": _CACHE.get("err"),
        "mtime": _CACHE.get("mtime", 0.0),
    }
