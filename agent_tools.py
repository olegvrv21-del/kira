"""Minimal local runtime for Kiro-CLI compatible tools.

Implements the 5 core tools so the agent can read/write files, run shell
commands, glob, and grep — mirroring the JSON Schemas captured from kiro-cli.

Every handler returns a string (treated as a single text block by the caller).
Raise an exception on failure; the agent loop converts it into an error
toolResult.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from pathlib import Path
from typing import Any


# ---------- helpers ----------

def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def _truncate(text: str, max_chars: int = 64_000) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return f"{head}\n\n[... truncated {len(text) - max_chars} chars ...]\n\n{tail}"


# ---------- execute_bash ----------

def execute_bash(args: dict[str, Any], cwd: str) -> str:
    cmd = args["command"]
    wd = args.get("working_dir") or cwd
    wd = str(_expand(wd))
    proc = subprocess.run(
        ["bash", "-lc", cmd],
        cwd=wd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = proc.stdout
    err = proc.stderr
    body = out
    if err:
        body += ("\n--- stderr ---\n" + err) if body else err
    body += f"\n--- exit {proc.returncode} ---"
    return _truncate(body)


# ---------- fs_read ----------

def _read_line_op(op: dict[str, Any]) -> str:
    path = _expand(op["path"])
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = op.get("start_line", 1)
    end = op.get("end_line")
    if start < 0:
        start = max(1, len(lines) + start + 1)
    s = max(1, start) - 1
    e = end if end is not None else len(lines)
    if e < 0:
        e = len(lines) + e + 1
    e = min(len(lines), e)
    chunk = lines[s:e]
    width = len(str(e))
    return "\n".join(f"{i+s+1:>{width}}: {ln}" for i, ln in enumerate(chunk))


def _read_dir_op(op: dict[str, Any]) -> str:
    path = _expand(op["path"])
    depth = op.get("depth", 0)
    excludes = op.get("exclude_patterns") or []
    out: list[str] = []

    def skip(name: str) -> bool:
        return any(fnmatch.fnmatch(name, p) for p in excludes)

    def walk(d: Path, level: int) -> None:
        try:
            entries = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except PermissionError:
            return
        for ent in entries:
            if skip(ent.name):
                continue
            suffix = "/" if ent.is_dir() else ""
            out.append(f"{'  ' * level}{ent.name}{suffix}")
            if ent.is_dir() and level < depth:
                walk(ent, level + 1)

    if path.is_file():
        return f"{path} (file, {path.stat().st_size} bytes)"
    out.append(f"{path}/")
    walk(path, 0)
    return "\n".join(out)


def _read_search_op(op: dict[str, Any]) -> str:
    path = _expand(op["path"])
    pat = op["pattern"]
    ctx = op.get("context_lines", 2)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    rx = re.compile(pat, re.IGNORECASE)
    hits: list[str] = []
    for i, ln in enumerate(lines):
        if rx.search(ln):
            s = max(0, i - ctx)
            e = min(len(lines), i + ctx + 1)
            for j in range(s, e):
                mark = ">" if j == i else " "
                hits.append(f"{mark} {j+1}: {lines[j]}")
            hits.append("---")
    return "\n".join(hits) or "(no matches)"


def fs_read(args: dict[str, Any], cwd: str) -> str:
    results = []
    for op in args["operations"]:
        mode = op["mode"]
        try:
            if mode == "Line":
                r = _read_line_op(op)
            elif mode == "Directory":
                r = _read_dir_op(op)
            elif mode == "Search":
                r = _read_search_op(op)
            else:
                r = f"(mode {mode!r} not implemented)"
        except Exception as e:
            r = f"ERROR: {type(e).__name__}: {e}"
        header = f"=== {mode}: {op.get('path','?')} ==="
        results.append(f"{header}\n{r}")
    return _truncate("\n\n".join(results))


# ---------- fs_write ----------

def _backup_if_exists(path: Path) -> str | None:
    if not path.exists():
        return None
    import shutil, time
    bak = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
    shutil.copy2(path, bak)
    return str(bak)


def fs_write(args: dict[str, Any], cwd: str) -> str:
    cmd = args["command"]
    path = _expand(args["path"])
    bak = _backup_if_exists(path)
    suffix = f" [BACKUP={bak}]" if bak else ""
    if cmd == "create":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["file_text"], encoding="utf-8")
        return f"Created {path} ({len(args['file_text'])} chars){suffix}"
    if cmd == "append":
        with open(path, "a", encoding="utf-8") as f:
            f.write(args["new_str"])
        return f"Appended {len(args['new_str'])} chars to {path}{suffix}"
    if cmd == "str_replace":
        old = args["old_str"]
        new = args["new_str"]
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            raise ValueError("old_str not found in file")
        if count > 1:
            raise ValueError(f"old_str matches {count} times; must be unique")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"Replaced 1 occurrence in {path}{suffix}"
    if cmd == "insert":
        line_no = args["insert_line"]
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        lines.insert(line_no, args["new_str"])
        path.write_text("\n".join(lines), encoding="utf-8")
        return f"Inserted after line {line_no} in {path}{suffix}"
    raise ValueError(f"unknown command: {cmd}")


# ---------- glob ----------

def glob_tool(args: dict[str, Any], cwd: str) -> str:
    pattern = args["pattern"]
    root = _expand(args.get("path") or cwd)
    limit = args.get("limit", 1000)
    max_depth = args.get("max_depth")
    matches: list[str] = []
    base_depth = len(root.parts)
    for p in root.rglob("*"):
        if max_depth is not None and len(p.parts) - base_depth > max_depth:
            continue
        rel = p.relative_to(root).as_posix()
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(p.name, pattern):
            matches.append(rel)
        if len(matches) >= limit:
            break
    matches.sort()
    return "\n".join(matches) or "(no matches)"


# ---------- grep ----------

def grep_tool(args: dict[str, Any], cwd: str) -> str:
    pattern = args["pattern"]
    path = str(_expand(args.get("path") or cwd))
    case = args.get("case_sensitive", False)
    include = args.get("include")
    mode = args.get("output_mode", "content")
    cmd = ["rg", "--no-heading", "--line-number"]
    if not case:
        cmd.append("-i")
    if include:
        cmd.extend(["-g", include])
    if mode == "files_with_matches":
        cmd.append("-l")
    elif mode == "count":
        cmd.append("-c")
    md = args.get("max_depth")
    if md is not None:
        cmd.extend(["--max-depth", str(md)])
    mm = args.get("max_matches_per_file")
    if mm is not None:
        cmd.extend(["-m", str(mm)])
    cmd.extend([pattern, path])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    out = proc.stdout
    mt = args.get("max_total_lines")
    if mt is not None and out:
        out = "\n".join(out.splitlines()[:mt])
    mf = args.get("max_files")
    if mf is not None and mode in ("files_with_matches", "count") and out:
        out = "\n".join(out.splitlines()[:mf])
    if not out and proc.returncode == 1:
        return "(no matches)"
    if proc.returncode > 1:
        return f"ERROR rg exit={proc.returncode}\n{proc.stderr}"
    return _truncate(out)


# ---------- verify_change ----------

def verify_change(args: dict[str, Any], cwd: str, sid: str | None = None) -> str:
    """Run validation checks after edits. Returns multi-line report.
    Checks: python syntax, optional curl, optional grep absence,
    optional shell command, optional service health.
    """
    import urllib.request
    reports: list[str] = []
    ok_all = True
    # python syntax
    for p in args.get("py_files", []) or []:
        path = _expand(p)
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            reports.append(f"OK py_compile {path}")
        except Exception as e:
            ok_all = False
            reports.append(f"FAIL py_compile {path}: {e}")
    # http GET
    for u in args.get("http_get", []) or []:
        try:
            with urllib.request.urlopen(u, timeout=10) as r:
                code = r.getcode()
            reports.append(f"OK http {u} -> {code}")
            if code >= 400:
                ok_all = False
        except Exception as e:
            ok_all = False
            reports.append(f"FAIL http {u}: {e}")
    # absent_substrings: a pattern that MUST NOT appear in file
    for spec in args.get("absent_in", []) or []:
        path = _expand(spec["path"]); pat = spec["pattern"]
        try:
            txt = path.read_text(encoding="utf-8")
            n = txt.count(pat)
            if n == 0:
                reports.append(f"OK absent '{pat}' in {path}")
            else:
                ok_all = False
                reports.append(f"FAIL '{pat}' found {n}x in {path}")
        except Exception as e:
            ok_all = False
            reports.append(f"FAIL absent {path}: {e}")
    # present_substrings
    for spec in args.get("present_in", []) or []:
        path = _expand(spec["path"]); pat = spec["pattern"]
        try:
            txt = path.read_text(encoding="utf-8")
            n = txt.count(pat)
            if n >= 1:
                reports.append(f"OK present '{pat}' in {path} ({n}x)")
            else:
                ok_all = False
                reports.append(f"FAIL '{pat}' missing in {path}")
        except Exception as e:
            ok_all = False
            reports.append(f"FAIL present {path}: {e}")
    # shell command (exit 0)
    for cmd in args.get("shell", []) or []:
        proc = subprocess.run(["bash", "-c", cmd], capture_output=True,
                              text=True, timeout=60)
        tag = "OK" if proc.returncode == 0 else "FAIL"
        if proc.returncode != 0:
            ok_all = False
        out = (proc.stdout + proc.stderr).strip()[:500]
        reports.append(f"{tag} sh `{cmd}` rc={proc.returncode}\n{out}")
    header = "VERIFY=OK" if ok_all else "VERIFY=FAIL"
    return header + "\n" + "\n".join(reports)


# ---------- dispatcher ----------

def _not_supported(args, cwd):
    raise RuntimeError("browser tools require KIRA_SANDBOX=1")


def memory_search(args: dict[str, Any], cwd: str) -> str:
    import agent_memory
    q = (args.get("query") or "").strip()
    if not q:
        raise ValueError("query is required")
    k = int(args.get("k") or 5)
    hits = agent_memory.memory.search(q, k=k)
    if not hits:
        return f"MEMORY no hits for {q!r}"
    out = [f"MEMORY {len(hits)} hits for {q!r}:"]
    for h in hits:
        head = (" [" + h["heading"] + "]") if h.get("heading") else ""
        out.append(f"\n--- {h['file']}:{h['start_line']}-{h['end_line']}{head} "
                   f"(score={h['score']}) ---")
        out.append(h["snippet"])
    return "\n".join(out)


def memory_add(args: dict[str, Any], cwd: str) -> str:
    import agent_memory
    text = (args.get("text") or "").strip()
    if not text:
        raise ValueError("text is required")
    file = args.get("file")
    info = agent_memory.memory.add(text, file=file)
    return (f"MEMORY appended file={info['file']} "
            f"bytes={info['bytes']} lines={info['lines']}")


def load_skill_tool(args: dict[str, Any], cwd: str) -> str:
    import agent_skills
    name = (args.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    body = agent_skills.load_skill(name)
    if body is None:
        skills = [s["name"] for s in agent_skills.list_skills()]
        raise ValueError(f"unknown skill {name!r}. Available: {skills}")
    return body


TOOLS = {
    "execute_bash": execute_bash,
    "fs_read": fs_read,
    "fs_write": fs_write,
    "glob": glob_tool,
    "grep": grep_tool,
    "browser_navigate": _not_supported,
    "browser_text": _not_supported,
    "browser_eval": _not_supported,
    "browser_click": _not_supported,
    "browser_type": _not_supported,
    "browser_screenshot": _not_supported,
    "browser_console_logs": _not_supported,
    "browser_network": _not_supported,
    "browser_accessibility": _not_supported,
    "browser_emulate": _not_supported,
    "git": _not_supported,
    "git_commit": _not_supported,
    "run_tests": _not_supported,
    "lint": _not_supported,
    "keyword_search": _not_supported,
    "outline": _not_supported,
    "find_definition": _not_supported,
    "find_references": _not_supported,
    "rename_symbol": _not_supported,
    "diagnostics": _not_supported,
    "dev_loop": _not_supported,
    "load_skill": load_skill_tool,
    "verify_change": verify_change,
    "memory_search": memory_search,
    "memory_add": memory_add,
}


def run_tool(name: str, args: dict[str, Any], cwd: str
             ) -> tuple[str, str, list[dict] | None]:
    """Returns (status, content_text, images_for_next_turn)."""
    fn = TOOLS.get(name)
    if fn is None:
        return "error", f"unknown tool: {name}", None
    try:
        result = fn(args, cwd)
        if isinstance(result, tuple):
            text, images = result
            return "success", text, images or None
        return "success", result, None
    except Exception as e:
        return "error", f"{type(e).__name__}: {e}", None
