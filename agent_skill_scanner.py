"""Static security scanner for skill content (DeerFlow-inspired, lite).

When Kira (or Oleg) creates a new skill via the UI or the agent_skills.create_skill
API, the content is run through this scanner before being written to disk. The
scanner returns one of:
  - allow: looks fine
  - warn:  borderline content (external URL, secret-related keyword) — caller decides
  - block: clear injection / exfiltration / destructive code — caller MUST refuse

The goal is defense in depth around a real threat: an agent that can write
its own skills can in principle write itself a backdoor. We catch the most
obvious shapes statically. A deeper LLM-based scan is left as future work
behind a feature flag.

Design notes (vs DeerFlow's security_scanner.py):
- DeerFlow uses an LLM judge with a YAML rubric. We use static regex.
  Reason: we want this scanner to run synchronously inside create_skill
  with zero token cost. The trade-off is recall (we miss novel attacks)
  for cost+latency+determinism.
- For "warn" cases we still allow the write but log the reason. Oleg can
  read the proposal/skill afterwards.

Env overrides:
  KIRA_SKILL_SCANNER=0          disable scanner (escape hatch)
  KIRA_SKILL_SCAN_WARN_BLOCKS=1 treat warnings as blocks (paranoid mode)

Public API:
  scan(name, description, body) -> ScanResult
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ScanResult:
    decision: str  # "allow" | "warn" | "block"
    reason: str = ""
    code: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision != "block"


# Patterns that block the write outright.
# (regex, human reason, machine code) — first match wins.
_BLOCK_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"(?i)ignore\s+(?:all\s+|the\s+|any\s+|your\s+)?(?:previous|prior|earlier|above|previous-?turn)\s+(?:instructions|prompts|rules|system)"),
        "prompt injection: 'ignore previous instructions'",
        "skill.prompt_injection",
    ),
    (
        re.compile(r"(?i)forget\s+(?:all|everything|your|the\s+above|previous)"),
        "prompt injection: 'forget' directive",
        "skill.prompt_injection",
    ),
    (
        re.compile(r"(?i)\byou\s+are\s+(?:now\s+)?(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:admin|root|owner|developer|operator|maintainer|sudoer|super[- ]?user)\b"),
        "prompt injection: role elevation",
        "skill.role_injection",
    ),
    (
        re.compile(r"(?im)^\s*system\s*prompt\s*[:=]"),
        "system-prompt redefinition",
        "skill.system_redef",
    ),
    (
        re.compile(r"<\|im_start\|>\s*system|<<SYS>>|\[INST\]"),
        "chat-template role injection (ChatML / Llama)",
        "skill.template_injection",
    ),
    (
        re.compile(r"(?i)(?:curl|wget|fetch)\b[^|`\n]*\|\s*(?:bash|sh|zsh|python3?)\b"),
        "remote-code-execution via piped shell",
        "skill.rce",
    ),
    (
        re.compile(r"(?i)\brm\s+-rf\s+(?:/|~|\$HOME)(?:\s|/|$)"),
        "destructive `rm -rf` on /, ~ or $HOME",
        "skill.destructive",
    ),
    (
        re.compile(r"(?i)(?:webhook\.site|requestbin|burpcollaborator|interactsh\.com|pipedream\.net/?\?|attacker[-_]?(?:com|net|org))"),
        "known exfiltration / out-of-band receiver host",
        "skill.exfiltration",
    ),
    (
        re.compile(r"(?i)(?:cat|less|more|head|tail)\s+(?:[^&|;\n]*?)(?:~/\.ssh/|/etc/shadow|/etc/passwd|\.env(?:\b|\s)|\.aws/credentials|\.netrc)"),
        "read of secret file location",
        "skill.secret_read",
    ),
    (
        re.compile(r"(?i)\b(?:echo|printf|cat)\s+[^|;\n]*>\s*~?/?\.ssh/(?:authorized_keys|id_)"),
        "write to ~/.ssh — backdoor pattern",
        "skill.ssh_backdoor",
    ),
    (
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:&\s*\}\s*;\s*:"),
        "fork-bomb",
        "skill.forkbomb",
    ),
)

# Patterns that warn but don't block.
_WARN_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"https?://(?!(?:github\.com|githubusercontent\.com|docs\.github\.com|stackoverflow\.com|disk-photon\.exe\.xyz|raw\.githubusercontent\.com|pypi\.org|exe\.dev)\b)[^\s)\"']+"),
        "external URL outside known-good allowlist",
        "skill.external_url",
    ),
    (
        re.compile(r"(?i)\b(?:api[_\s-]?key|secret_?key|access_?token|private_?key|bearer\s+token)\b"),
        "mentions credential-related keyword",
        "skill.secret_keyword",
    ),
)


def _is_enabled() -> bool:
    return os.environ.get("KIRA_SKILL_SCANNER", "1") not in ("0", "false", "False")


def _warn_blocks() -> bool:
    return os.environ.get("KIRA_SKILL_SCAN_WARN_BLOCKS", "0") in ("1", "true", "True")


def scan(name: str, description: str, body: str) -> ScanResult:
    """Scan skill content. Returns ScanResult.

    The three fields are all considered. `body` is the most important; we
    also look at description for sneaky descriptions like
    "Run this skill to ignore previous instructions".
    """
    if not _is_enabled():
        return ScanResult(decision="allow")

    text = "\n".join(
        (name or "", description or "", body or "")
    )

    for pat, reason, code in _BLOCK_PATTERNS:
        if pat.search(text):
            return ScanResult(decision="block", reason=reason, code=code)

    for pat, reason, code in _WARN_PATTERNS:
        if pat.search(text):
            if _warn_blocks():
                return ScanResult(decision="block", reason=f"warning treated as block: {reason}", code=code)
            return ScanResult(decision="warn", reason=reason, code=code)

    return ScanResult(decision="allow")
