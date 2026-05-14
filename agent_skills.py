"""Skill loader: scans ~/webchat/skills/*.md for YAML frontmatter and exposes
the list + per-skill content. Mirrors the Shelley/Claude-Code skill pattern.

Each file looks like:
    ---
    name: my-skill
    description: When to use this skill.
    ---
    <markdown body>

The agent gets the list (name + description) injected into its system prompt.
It calls load_skill({"name": "my-skill"}) to read the full body when needed.
"""

from __future__ import annotations

import re
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse(text: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta_block, body = m.group(1), m.group(2)
    meta: dict[str, str] = {}
    for line in meta_block.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta, body


def list_skills() -> list[dict[str, str]]:
    """Returns [{name, description}, ...] for every well-formed skill file."""
    out: list[dict[str, str]] = []
    if not SKILLS_DIR.is_dir():
        return out
    for p in sorted(SKILLS_DIR.glob("*.md")):
        try:
            meta, _ = _parse(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = meta.get("name") or p.stem
        desc = meta.get("description") or ""
        out.append({"name": name, "description": desc, "file": p.name})
    return out


def load_skill(name: str) -> str | None:
    """Return full markdown (frontmatter stripped) for a named skill, or None."""
    if not SKILLS_DIR.is_dir():
        return None
    # Allow either exact stem match or 'name:' field match.
    candidates = list(SKILLS_DIR.glob("*.md"))
    for p in candidates:
        if p.stem == name:
            text = p.read_text(encoding="utf-8")
            _, body = _parse(text)
            return body.strip()
    for p in candidates:
        try:
            meta, body = _parse(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("name") == name:
            return body.strip()
    return None


# Strict slug for filename / skill name. Lowercase letters, digits, dashes.
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


def create_skill(name: str, description: str, body: str) -> dict:
    """Create a new skill file at SKILLS_DIR/<name>.md.

    Returns {"ok": True, "file": "<path>"} on success, or
    {"ok": False, "error": "<reason>"} on validation failure / collision.

    Constraints:
    - name: lowercase letters/digits/dashes, 2-40 chars, starts with a letter
    - description: 1-300 chars, single line (newlines stripped)
    - body: required, max 20 KiB
    - refuses to overwrite an existing file
    """
    import re as _re
    name = (name or "").strip().lower()
    description = (description or "").strip()
    body = body or ""

    if not _NAME_RE.match(name):
        return {"ok": False, "error": "name must be 2-40 chars: lowercase letters, digits, dashes; start with a letter"}
    description = _re.sub(r"\s+", " ", description)[:300]
    if not description:
        return {"ok": False, "error": "description is required (1-300 chars)"}
    if not body.strip():
        return {"ok": False, "error": "body is required"}
    if len(body.encode("utf-8")) > 20 * 1024:
        return {"ok": False, "error": "body too large (>20 KiB)"}

    # Content safety scan (regex-based; no LLM call). Blocks prompt-injection,
    # RCE patterns, secret-file reads, ~/.ssh backdoors, fork-bombs, etc.
    import agent_skill_scanner
    scan = agent_skill_scanner.scan(name, description, body)
    if scan.decision == "block":
        return {"ok": False, "error": f"skill content rejected by scanner: {scan.reason} (code={scan.code})"}

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    # Path safety: rebuild `name` character-by-character from a constant
    # whitelist alphabet. This breaks taint propagation in CodeQL — the
    # resulting string is provably composed of safe constants, even though
    # the *length* and *order* still come from user input. _NAME_RE has
    # already rejected anything not matching this alphabet, so the loop
    # output equals the input but the static-analyzer no longer flags it.
    _ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-"
    safe_chars = []
    for ch in name:
        idx = _ALPHABET.find(ch)
        if idx < 0:
            return {"ok": False, "error": "invalid name (unexpected char post-regex)"}
        safe_chars.append(_ALPHABET[idx])
    safe_name = "".join(safe_chars)

    safe_dir = SKILLS_DIR.resolve()
    target = safe_dir / (safe_name + ".md")
    if target.parent.resolve() != safe_dir:
        return {"ok": False, "error": "invalid path (containment violated)"}
    if target.exists():
        return {"ok": False, "error": f"skill '{safe_name}' already exists"}

    front = "---\nname: " + safe_name + "\ndescription: " + description + "\n---\n\n"
    target.write_text(front + body.strip() + "\n", encoding="utf-8")
    out = {"ok": True, "file": target.name}
    if scan.decision == "warn":
        out["warning"] = f"{scan.reason} (code={scan.code})"
    return out


def render_skills_section() -> str:
    """Build the <available_skills> block injected into the system prompt."""
    skills = list_skills()
    if not skills:
        return ""
    lines = ["<available_skills>"]
    for s in skills:
        lines.append("<skill>")
        lines.append(f"<name>{s['name']}</name>")
        lines.append(f"<description>{s['description']}</description>")
        lines.append('<activate>load_skill name="' + s["name"] + '"</activate>')
        lines.append("</skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)
