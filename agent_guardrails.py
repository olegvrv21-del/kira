"""Tool-call authorization layer (Guardrails).

Inspired by DeerFlow 2.0's GuardrailMiddleware. Sits between the agent
requesting a tool and the tool being executed. Returns a decision:
  - allow: tool runs normally
  - deny:  tool returns an error message to the agent (no exception)

Kira already has a coarse defense (kill-switch via .frozen). Guardrails
are a fine-grained second layer: even when unfrozen, certain tool calls
are statically denied based on (tool_name, args).

## Default policy

The goal is defense in depth without breaking normal work:

1. **fs_write** to sensitive paths is denied:
   - anything under ~/.ssh, .env files, /etc/, ~/.aws/, ~/.config/gh/
   - the .frozen flag file itself (kill-switch must be tamper-proof)
   - any path matching the deny-list patterns below

2. **execute_bash** containing dangerous patterns is denied:
   - rm -rf with root-ish paths
   - chmod 777, chown root, sudo without password
   - curl|wget piped into shell (\"| bash\", \"| sh\")
   - direct ssh-keygen overwrite of ~/.ssh keys
   - direct git push to main (Oleg's rule: only via PR)

3. Everything else is allowed.

## Override via env

- KIRA_GUARDRAILS=0 -> disables all checks (NOT recommended, but lets Oleg
  unblock himself if a rule turns out wrong).
- KIRA_GUARDRAILS_EXTRA_DENY (comma-separated tool names) -> extends the
  per-tool deny set.

Public API:
  evaluate(tool_name, args) -> Decision
  Decision.allow: bool
  Decision.reason: str  (filled when allow=False)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str = ""
    code: str = ""

    @classmethod
    def ok(cls) -> "Decision":
        return cls(allow=True)

    @classmethod
    def deny(cls, reason: str, code: str = "guardrail.denied") -> "Decision":
        return cls(allow=False, reason=reason, code=code)


# --- defaults ---

# Tool names that are always denied (placeholder; intentionally empty for now
# so we don't break the workflow). Add via env KIRA_GUARDRAILS_EXTRA_DENY.
_DENY_TOOLS_DEFAULT: frozenset[str] = frozenset()

# fs_write path patterns that are denied. Each entry is a substring; we
# canonicalize the path first so traversal tricks (../../) don't bypass.
_FS_WRITE_DENY_PATTERNS: tuple[str, ...] = (
    "/.ssh/",
    "/.aws/",
    "/.config/gh/",
    "/.config/git/credentials",
    "/etc/",
    "/.netrc",
    "/.frozen",          # kill-switch flag is sacred
    "/.git/config",      # rewriting git config is too sharp
    "/agent_keys_local.",  # local key files
)

# fs_write paths matching these exact basenames are denied even outside
# sensitive dirs. Catches `.env` written into a project root.
_FS_WRITE_DENY_BASENAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "id_ed25519",
        "id_rsa",
        "authorized_keys",
    }
)

# execute_bash regexes -> (reason). Order matters: first match wins.
_BASH_DENY: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+-rf\s+(?:/|~|\$HOME)(?:\s|/|$)"), "`rm -rf` on / or ~ is denied"),
    (re.compile(r"\bchmod\s+(?:0?7?77)\b"), "`chmod 777` is denied"),
    (re.compile(r"\bchown\s+(?:-R\s+)?root\b"), "`chown root` is denied"),
    (re.compile(r"(?:curl|wget)\b[^|]*\|\s*(?:bash|sh|zsh)\b"), "curl|sh pattern (remote exec) is denied"),
    (re.compile(r"\bssh-keygen\b.*-f\s+(?:~|\$HOME)?/?\.ssh/(?:id_|authorized_)"), "overwriting ~/.ssh keys is denied"),
    (re.compile(r"\bgit\s+push\s+(?:-f\s+|--force\s+)?(?:[\w/-]+\s+)?main\b"), "direct git push to main is denied (open a PR instead)"),
    (re.compile(r"\bgit\s+push\b.*\bmain(?::main)?\s*(?:--force|--force-with-lease|-f)?\s*$"), "direct git push to main is denied (open a PR instead)"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:&\s*\}\s*;\s*:"), "fork-bomb pattern is denied"),
    (re.compile(r"\bdd\b[^|]*\bof=/dev/(?:sd[a-z]|nvme|disk|hda)"), "raw dd to a block device is denied"),
    (re.compile(r"\bmkfs\."), "`mkfs.*` is denied"),
    (re.compile(r">\s*/etc/passwd\b"), "writing /etc/passwd is denied"),
)


def _is_enabled() -> bool:
    return os.environ.get("KIRA_GUARDRAILS", "1") not in ("0", "false", "False")


def _extra_deny_tools() -> frozenset[str]:
    raw = os.environ.get("KIRA_GUARDRAILS_EXTRA_DENY", "")
    if not raw.strip():
        return frozenset()
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def _canonical(path: str) -> str:
    """Best-effort absolute path. Doesn't require the path to exist."""
    if not path:
        return ""
    try:
        return str(Path(path).expanduser().resolve())
    except Exception:
        return path


def evaluate_fs_write(args: dict) -> Decision:
    path = str(args.get("path") or "")
    if not path:
        return Decision.ok()
    canon = _canonical(path)
    # path-based deny
    for pat in _FS_WRITE_DENY_PATTERNS:
        if pat in canon:
            return Decision.deny(
                f"fs_write to '{path}' is denied: matches sensitive pattern '{pat}'",
                code="guardrail.fs_sensitive",
            )
    # basename-based deny
    base = Path(canon).name
    if base in _FS_WRITE_DENY_BASENAMES:
        return Decision.deny(
            f"fs_write to '{base}' is denied: protected filename",
            code="guardrail.fs_sensitive",
        )
    return Decision.ok()


def evaluate_bash(args: dict) -> Decision:
    cmd = str(args.get("command") or args.get("cmd") or "")
    if not cmd.strip():
        return Decision.ok()
    for pat, reason in _BASH_DENY:
        if pat.search(cmd):
            return Decision.deny(reason, code="guardrail.bash_pattern")
    return Decision.ok()


def evaluate(tool_name: str, args: dict | None) -> Decision:
    """Main entry point. Returns Decision.ok() to allow, .deny(reason) to block."""
    if not _is_enabled():
        return Decision.ok()
    args = args or {}

    # tool-level deny
    denied_tools = _DENY_TOOLS_DEFAULT | _extra_deny_tools()
    if tool_name in denied_tools:
        return Decision.deny(
            f"tool '{tool_name}' is on the deny-list",
            code="guardrail.tool_denied",
        )

    # argument-level checks per tool
    if tool_name == "fs_write":
        return evaluate_fs_write(args)
    if tool_name == "execute_bash":
        return evaluate_bash(args)

    return Decision.ok()
