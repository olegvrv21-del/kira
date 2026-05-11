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
        lines.append("<activate>load_skill name=\"" + s["name"] + "\"</activate>")
        lines.append("</skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)
