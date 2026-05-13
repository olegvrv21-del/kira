"""Sandbox-backed tool implementations.

Same interface as agent_tools (TOOLS dict, run_tool fn) but routes every
operation through a per-session docker container via sandbox_runtime.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any

import sandbox_runtime as sb


def _truncate(text: str, max_chars: int = 64_000) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return f"{head}\n\n[... truncated {len(text) - max_chars} chars ...]\n\n{tail}"


def _cpath(p: str, sid: str) -> str:
    return sb._to_container_path(p, sid)


# ---------- execute_bash ----------


def _get_cwd(sid: str, fallback: str = "/workspace") -> str:
    try:
        import agent_store

        v = agent_store.get_meta(sid, "cwd", None)
        return v if isinstance(v, str) and v else fallback
    except Exception:
        return fallback


def execute_bash(args: dict[str, Any], cwd: str, sid: str) -> str:
    cmd = args["command"]
    wd = args.get("working_dir") or _get_cwd(sid)
    rc, out, err = sb.exec_bash(sid, cmd, working_dir=wd)
    body = out
    if err:
        body += ("\n--- stderr ---\n" + err) if body else err
    body += f"\n--- exit {rc} ---\n--- cwd {wd} ---"
    return _truncate(body)


def change_dir(args: dict[str, Any], cwd: str, sid: str) -> str:
    path = args["path"]
    # Resolve relative to current cwd inside container
    cur = _get_cwd(sid)
    if not path.startswith("/"):
        rc, abs_path, err = sb.exec_bash(sid, f"cd {shlex.quote(cur)} && cd {shlex.quote(path)} && pwd")
    else:
        rc, abs_path, err = sb.exec_bash(sid, f"cd {shlex.quote(path)} && pwd")
    if rc != 0:
        raise OSError((err or abs_path).strip() or f"cannot cd to {path}")
    new = abs_path.strip().splitlines()[-1]
    import agent_store

    agent_store.set_meta(sid, "cwd", new)
    return f"cwd = {new}"


# ---------- fs_read ----------


def _read_line_op(op: dict[str, Any], sid: str) -> str:
    path = _cpath(op["path"], sid)
    start = op.get("start_line", 1)
    end = op.get("end_line")
    # sed handles negative? we do it via python inside container
    snippet = (
        "python3 - <<'EOF'\n"
        "import sys\n"
        f"path={path!r}; start={start!r}; end={end!r}\n"
        "text=open(path,encoding='utf-8',errors='replace').read()\n"
        "lines=text.splitlines()\n"
        "if start<0: start=max(1,len(lines)+start+1)\n"
        "s=max(1,start)-1\n"
        "e=end if end is not None else len(lines)\n"
        "if e<0: e=len(lines)+e+1\n"
        "e=min(len(lines),e)\n"
        "w=len(str(e))\n"
        "for i,ln in enumerate(lines[s:e]): print(f'{i+s+1:>{w}}: {ln}')\n"
        "EOF"
    )
    rc, out, err = sb.exec_bash(sid, snippet)
    if rc != 0:
        raise RuntimeError(err.strip() or f"exit {rc}")
    return out.rstrip("\n")


def _read_dir_op(op: dict[str, Any], sid: str) -> str:
    path = _cpath(op["path"], sid)
    depth = op.get("depth", 0)
    excludes = op.get("exclude_patterns") or []
    # use tree if depth>0, else ls
    if depth > 0:
        ex = ""
        if excludes:
            ex = "-I " + shlex.quote("|".join(excludes))
        rc, out, err = sb.exec_bash(sid, f"tree -L {depth + 1} --noreport {ex} {shlex.quote(path)}")
    else:
        rc, out, err = sb.exec_bash(sid, f"ls -la {shlex.quote(path)}")
    if rc != 0:
        raise RuntimeError(err.strip() or f"exit {rc}")
    return out.rstrip("\n")


def _read_image_op(op: dict[str, Any], sid: str) -> tuple[str, list[dict]]:
    """Returns (text marker, list of image dicts for the next user turn)."""
    import base64 as _b64

    paths = op.get("image_paths") or ([op["path"]] if op.get("path") else [])
    images: list[dict] = []
    for p in paths:
        cp = _cpath(p, sid)
        # Read raw bytes from inside the container
        raw = subprocess.run(
            ["docker", "exec", sb.ensure_container(sid), "cat", cp],
            capture_output=True,
            timeout=60,
        )
        if raw.returncode != 0:
            raise FileNotFoundError(cp)
        data = raw.stdout
        if len(data) > 5 * 1024 * 1024:
            raise ValueError(f"image too large ({len(data)} bytes); max 5MB")
        ext = (cp.rsplit(".", 1)[-1] or "").lower()
        if ext == "jpg":
            ext = "jpeg"
        if ext not in ("png", "jpeg", "gif", "webp"):
            ext = "png"
        images.append({"format": ext, "source": {"bytes": _b64.b64encode(data).decode("ascii")}})
    return "See images data supplied", images


def _read_search_op(op: dict[str, Any], sid: str) -> str:
    path = _cpath(op["path"], sid)
    pat = op["pattern"]
    ctx = op.get("context_lines", 2)
    rc, out, err = sb.exec_bash(sid, f"rg -n -i -C {ctx} {shlex.quote(pat)} {shlex.quote(path)}")
    if rc == 1:
        return "(no matches)"
    if rc > 1:
        raise RuntimeError(err.strip() or f"rg exit {rc}")
    return out.rstrip("\n")


def fs_read(args: dict[str, Any], cwd: str, sid: str) -> str | tuple[str, list[dict]]:
    results = []
    images: list[dict] = []
    for op in args["operations"]:
        mode = op["mode"]
        try:
            if mode == "Line":
                r = _read_line_op(op, sid)
            elif mode == "Directory":
                r = _read_dir_op(op, sid)
            elif mode == "Search":
                r = _read_search_op(op, sid)
            elif mode == "Image":
                r, imgs = _read_image_op(op, sid)
                images.extend(imgs)
            else:
                r = f"(mode {mode!r} not implemented)"
        except Exception as e:
            r = f"ERROR: {type(e).__name__}: {e}"
        header = f"=== {mode}: {op.get('path', op.get('image_paths', '?'))} ==="
        results.append(f"{header}\n{r}")
    text = _truncate("\n\n".join(results))
    if images:
        return text, images
    return text


# ---------- fs_write ----------


def _backup_if_exists(sid: str, path: str) -> str | None:
    rc, _, _ = sb.exec_bash(sid, f"test -f {shlex.quote(path)}")
    if rc != 0:
        return None
    bak = f"{path}.bak.{int(__import__('time').time())}"
    sb.exec_bash(sid, f"cp -a {shlex.quote(path)} {shlex.quote(bak)}")
    return bak


def fs_write(args: dict[str, Any], cwd: str, sid: str) -> str:
    cmd = args["command"]
    path = _cpath(args["path"], sid)
    bak = _backup_if_exists(sid, path)
    suffix = f" [BACKUP={bak}]" if bak else ""
    if cmd == "create":
        sb.write_file(sid, path, args["file_text"])
        return f"Created {path} ({len(args['file_text'])} chars){suffix}"
    if cmd == "append":
        # read existing then write back; cheaper: shell append
        rc, _, err = sb.exec_bash(
            sid,
            f"printf '%s' {shlex.quote(args['new_str'])} >> {shlex.quote(path)}",
        )
        if rc != 0:
            raise OSError(err.strip())
        return f"Appended {len(args['new_str'])} chars to {path}{suffix}"
    if cmd == "str_replace":
        old = args["old_str"]
        new = args["new_str"]
        text = sb.read_file(sid, path)
        count = text.count(old)
        if count == 0:
            raise ValueError("old_str not found in file")
        if count > 1:
            raise ValueError(f"old_str matches {count} times; must be unique")
        sb.write_file(sid, path, text.replace(old, new, 1))
        return f"Replaced 1 occurrence in {path}{suffix}"
    if cmd == "insert":
        line_no = args["insert_line"]
        text = sb.read_file(sid, path)
        lines = text.split("\n")
        lines.insert(line_no, args["new_str"])
        sb.write_file(sid, path, "\n".join(lines))
        return f"Inserted after line {line_no} in {path}{suffix}"
    raise ValueError(f"unknown command: {cmd}")


# ---------- patch (multi-operation, clipboard-aware) ----------

_CLIPBOARDS: dict[str, dict[str, str]] = {}  # sid -> {name: text}


def _reindent(text: str, strip: str, add: str) -> str:
    lines = text.split("\n")
    out = []
    for ln in lines:
        if ln.strip() == "":
            out.append(ln)
            continue
        if strip and ln.startswith(strip):
            ln = ln[len(strip) :]
        out.append(add + ln)
    return "\n".join(out)


def patch(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Multi-operation editor with named clipboards.
    args = {
      "path": "...",
      "patches": [
        {
          "operation": "replace|append_eof|prepend_bof|overwrite",
          "oldText": "… (for replace)",
          "newText": "…",
          "toClipboard": "name"      # save oldText to clipboard before edit
          "fromClipboard": "name"    # use clipboard content as newText
          "reindent": {"strip": "  ", "add": "    "}
        }, ...]
    }
    Clipboards persist across patch calls within the session.
    """
    path = _cpath(args["path"], sid)
    patches = args.get("patches") or []
    if not patches:
        raise ValueError("patches must be a non-empty list")
    clips = _CLIPBOARDS.setdefault(sid, {})
    # Determine whether the file exists; needed for the very first op (overwrite
    # is allowed to create).
    rc_exists, _, _ = sb.exec_bash(sid, f"test -f {shlex.quote(path)}")
    file_exists = rc_exists == 0
    text = sb.read_file(sid, path) if file_exists else ""
    bak = _backup_if_exists(sid, path) if file_exists else None
    suffix = f" [BACKUP={bak}]" if bak else ""
    applied: list[str] = []
    for i, p in enumerate(patches):
        op = p.get("operation")
        old = p.get("oldText", "") or ""
        new = p.get("newText", "") or ""
        # Clipboard write happens against the pre-edit text.
        clip_name = p.get("toClipboard")
        if clip_name:
            clips[clip_name] = old
        clip_from = p.get("fromClipboard")
        if clip_from:
            if clip_from not in clips:
                raise ValueError(f"clipboard {clip_from!r} not set")
            new = clips[clip_from]
        ri = p.get("reindent")
        if ri:
            new = _reindent(new, ri.get("strip") or "", ri.get("add") or "")
        if op == "replace":
            cnt = text.count(old)
            if cnt == 0:
                raise ValueError(f"patch[{i}].oldText not found")
            if cnt > 1:
                raise ValueError(f"patch[{i}].oldText matches {cnt} times; must be unique")
            text = text.replace(old, new, 1)
            applied.append(f"replace#{i} (1 occurrence)")
        elif op == "append_eof":
            if text and not text.endswith("\n"):
                text += "\n"
            text += new
            applied.append(f"append_eof#{i} (+{len(new)})")
        elif op == "prepend_bof":
            text = new + ("" if new.endswith("\n") or not text else "") + text
            applied.append(f"prepend_bof#{i} (+{len(new)})")
        elif op == "overwrite":
            text = new
            applied.append(f"overwrite#{i} ({len(new)} chars)")
        else:
            raise ValueError(f"patch[{i}].operation invalid: {op!r}")
    sb.write_file(sid, path, text)
    return f"patched {path}: " + ", ".join(applied) + suffix


# ---------- glob ----------


def glob_tool(args: dict[str, Any], cwd: str, sid: str) -> str:
    pattern = args["pattern"]
    root = _cpath(args.get("path") or "/workspace", sid) if args.get("path") else "/workspace"
    limit = args.get("limit", 1000)
    max_depth = args.get("max_depth")
    # use find
    depth_arg = f"-maxdepth {max_depth}" if max_depth is not None else ""
    cmd = f"cd {shlex.quote(root)} && find . {depth_arg} -name {shlex.quote(pattern)} | head -{limit} | sed 's|^\\./||'"
    rc, out, err = sb.exec_bash(sid, cmd)
    if rc != 0:
        raise RuntimeError(err.strip() or f"exit {rc}")
    out = out.rstrip("\n")
    return out or "(no matches)"


# ---------- keyword_search (ranked rg over terms) ----------


def keyword_search(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Rank files by relevance to a list of search terms.
    Score = sum over terms of (matches_in_file * weight_for_term_position).
    Bonuses for hits in filename / symbol-defining lines (def, class, function).
    Returns top N paths with brief preview lines.
    """
    terms = args.get("search_terms") or []
    if isinstance(terms, str):
        terms = [terms]
    terms = [t for t in (terms or []) if t]
    if not terms:
        raise ValueError("search_terms must be non-empty")
    root = _cpath(args.get("path") or "/workspace", sid) if args.get("path") else "/workspace"
    glob = args.get("glob")  # e.g. '*.py'
    limit = int(args.get("limit", 20))
    case_sensitive = bool(args.get("case_sensitive", False))
    # weight by position: first term most important.
    weights = [max(1.0, 5.0 - 0.5 * i) for i in range(len(terms))]
    # We use rg in JSON mode for accurate per-file match counts.
    cs_flag = "-S" if not case_sensitive else "-s"
    glob_flag = f"-g {shlex.quote(glob)}" if glob else ""
    scores: dict[str, dict[str, Any]] = {}
    for term, w in zip(terms, weights, strict=False):
        cmd = (
            f"rg --json {cs_flag} --hidden -."
            f" --glob '!.git' --glob '!**/node_modules' --glob '!**/__pycache__'"
            f" --glob '!**/.venv' {glob_flag} -F -- {shlex.quote(term)} {shlex.quote(root)}"
            f" 2>/dev/null"
        )
        rc, out, err = sb.exec_bash(sid, cmd)
        if rc not in (0, 1):  # 1 = no matches
            continue
        for line in out.split("\n"):
            if not line:
                continue
            try:
                obj = __import__("json").loads(line)
            except Exception:
                continue
            kind = obj.get("type")
            data = obj.get("data", {})
            if kind == "match":
                path = (data.get("path") or {}).get("text")
                if not path:
                    continue
                txt = (data.get("lines") or {}).get("text", "")
                line_no = data.get("line_number")
                rec = scores.setdefault(path, {"score": 0.0, "hits": 0, "snippets": [], "terms_hit": set()})
                # bonus if term appears in a definition line
                stripped = txt.lstrip()
                bonus = 1.0
                if any(
                    stripped.startswith(p)
                    for p in (
                        "def ",
                        "async def ",
                        "class ",
                        "function ",
                        "export function ",
                        "export const ",
                        "const ",
                        "let ",
                        "var ",
                        "func ",
                        "fn ",
                        "interface ",
                        "type ",
                    )
                ):
                    bonus += 1.5
                # bonus if term appears in the filename
                if term.lower() in path.lower():
                    bonus += 2.0
                rec["score"] += w * bonus
                rec["hits"] += 1
                rec["terms_hit"].add(term)
                if len(rec["snippets"]) < 3:
                    rec["snippets"].append(f"{line_no}: {txt.rstrip()[:200]}")
    if not scores:
        return "(no matches)"
    # multi-term bonus: files matching MORE distinct terms rank higher.
    n_terms = len(terms)
    ranked = []
    for path, rec in scores.items():
        cover = len(rec["terms_hit"]) / n_terms
        final = rec["score"] * (1 + cover)
        ranked.append((final, path, rec))
    ranked.sort(key=lambda r: -r[0])
    lines: list[str] = [f"Top {min(limit, len(ranked))} of {len(ranked)} matched files:"]
    for score, path, rec in ranked[:limit]:
        rel = path[len(root) :].lstrip("/") if path.startswith(root) else path
        lines.append(f"\n{rel}  ·  score={score:.1f}  hits={rec['hits']}  terms={len(rec['terms_hit'])}/{n_terms}")
        for sn in rec["snippets"]:
            lines.append(f"  {sn}")
    return _truncate("\n".join(lines))


# ---------- outline (regex-based symbol extraction) ----------

import re as _re_outline

_PY_PAT = _re_outline.compile(
    r"^(?P<ind>[ \t]*)(?:(?P<dec>@[\w\.]+(?:\(.*\))?)\s*\n[ \t]*)?"
    r"(?P<kw>async\s+def|def|class)\s+(?P<name>[A-Za-z_][\w]*)",
    _re_outline.MULTILINE,
)

_JS_PAT = _re_outline.compile(
    r"^(?P<ind>[ \t]*)(?:export\s+(?:default\s+)?)?"
    r"(?P<kw>async\s+function|function|class|interface|type|const|let|var)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)",
    _re_outline.MULTILINE,
)

_GO_PAT = _re_outline.compile(
    r"^(?P<kw>func|type|var|const|package)\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_][\w]*)",
    _re_outline.MULTILINE,
)

_LANG_MAP = {
    ".py": (_PY_PAT, "python"),
    ".js": (_JS_PAT, "js"),
    ".jsx": (_JS_PAT, "js"),
    ".ts": (_JS_PAT, "ts"),
    ".tsx": (_JS_PAT, "ts"),
    ".mjs": (_JS_PAT, "js"),
    ".go": (_GO_PAT, "go"),
}


def outline(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Return a compact outline of a source file (top-level + nested symbols).
    Supports .py .js .ts .jsx .tsx .go via regex; ignores comments/strings cheaply.
    Use BEFORE editing an unfamiliar file: 'what's in here?' in <1s and ~1KB.
    """
    import os.path

    path = _cpath(args["path"], sid)
    ext = os.path.splitext(path)[1].lower()
    pair = _LANG_MAP.get(ext)
    if not pair:
        raise ValueError(f"unsupported file type {ext!r}; supported: " + ", ".join(sorted(_LANG_MAP)))
    pat, lang = pair
    text = sb.read_file(sid, path)
    if not text:
        return "(empty file)"
    out_lines: list[str] = [f"# outline ({lang}) {path}  ({len(text.splitlines())} lines)"]
    for m in pat.finditer(text):
        line_no = text[: m.start()].count("\n") + 1
        ind = m.group("ind") if "ind" in m.groupdict() and m.group("ind") else ""
        depth = len(ind.replace("\t", "    ")) // 2
        kw = m.group("kw").strip()
        name = m.group("name")
        out_lines.append(f"{'  ' * depth}{line_no:4d}: {kw} {name}")
    if len(out_lines) == 1:
        return out_lines[0] + "\n(no top-level symbols detected)"
    return _truncate("\n".join(out_lines))


# ---------- grep ----------


def grep_tool(args: dict[str, Any], cwd: str, sid: str) -> str:
    pattern = args["pattern"]
    path = _cpath(args.get("path") or "/workspace", sid) if args.get("path") else "/workspace"
    case = args.get("case_sensitive", False)
    include = args.get("include")
    mode = args.get("output_mode", "content")
    parts = ["rg", "--no-heading", "--line-number"]
    if not case:
        parts.append("-i")
    if include:
        parts.extend(["-g", include])
    if mode == "files_with_matches":
        parts.append("-l")
    elif mode == "count":
        parts.append("-c")
    md = args.get("max_depth")
    if md is not None:
        parts.extend(["--max-depth", str(md)])
    mm = args.get("max_matches_per_file")
    if mm is not None:
        parts.extend(["-m", str(mm)])
    parts.append(pattern)
    parts.append(path)
    cmd = " ".join(shlex.quote(p) for p in parts)
    rc, out, err = sb.exec_bash(sid, cmd)
    mt = args.get("max_total_lines")
    if mt is not None and out:
        out = "\n".join(out.splitlines()[:mt])
    mf = args.get("max_files")
    if mf is not None and mode in ("files_with_matches", "count") and out:
        out = "\n".join(out.splitlines()[:mf])
    if not out and rc == 1:
        return "(no matches)"
    if rc > 1:
        return f"ERROR rg exit={rc}\n{err}"
    return _truncate(out)


# ---------- browser_* ----------


def browser_navigate(args: dict[str, Any], cwd: str, sid: str) -> str:
    r = sb.browser_call(
        sid,
        "/navigate",
        {
            "url": args["url"],
            "wait_until": args.get("wait_until", "domcontentloaded"),
            "timeout_ms": 30000,
        },
    )
    return f"Navigated to {r.get('url')}  (status={r.get('status')}, title={r.get('title')!r})"


def browser_text(args: dict[str, Any], cwd: str, sid: str) -> str:
    r = sb.browser_call(sid, "/text")
    body = r.get("text", "")
    return f"=== URL: {r.get('url')}  Title: {r.get('title')} ===\n{body}"


def browser_eval(args: dict[str, Any], cwd: str, sid: str) -> str:
    r = sb.browser_call(sid, "/eval", {"expression": args["expression"], "timeout_ms": 15000})
    if "error" in r:
        raise RuntimeError(r["error"])
    return r.get("result", "")


def browser_click(args: dict[str, Any], cwd: str, sid: str) -> str:
    r = sb.browser_call(sid, "/click", {"selector": args["selector"], "timeout_ms": 15000})
    if "error" in r:
        raise RuntimeError(r["error"])
    return f"Clicked {args['selector']!r}; now at {r.get('url')}"


def browser_type(args: dict[str, Any], cwd: str, sid: str) -> str:
    r = sb.browser_call(sid, "/type", {"selector": args["selector"], "text": args["text"], "timeout_ms": 15000})
    if "error" in r:
        raise RuntimeError(r["error"])
    return f"Typed into {args['selector']!r}"


def browser_screenshot(args: dict[str, Any], cwd: str, sid: str) -> tuple[str, list[dict]]:
    import base64

    out_path = args.get("path") or "/workspace/screenshot.png"
    r = sb.browser_call(sid, "/screenshot")
    if "png_b64" not in r:
        raise RuntimeError(r.get("error", "no screenshot"))
    b64 = r["png_b64"]
    data = base64.b64decode(b64)
    # also persist to workspace so the user can fetch it
    p = subprocess.Popen(
        ["docker", "exec", "-i", sb.ensure_container(sid), "sh", "-c", f"cat > {shlex.quote(out_path)}"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _, err = p.communicate(data, timeout=30)
    if p.returncode != 0:
        raise OSError(err.decode("utf-8", "replace"))
    text = f"Screenshot ({len(data)} bytes) saved to {out_path}  at {r.get('url')}.\nSee images data supplied"
    return text, [{"format": "png", "source": {"bytes": b64}}]


def browser_console_logs(args: dict[str, Any], cwd: str, sid: str) -> str:
    limit = int(args.get("limit", 100))
    clear = bool(args.get("clear", False))
    if clear:
        sb.browser_call(sid, "/clear_console")
        return "console logs cleared"
    r = sb.browser_call(sid, "/console_logs", {"limit": limit})
    logs = r.get("logs", [])
    if not logs:
        return "(no console logs)"
    lines = [f"{m.get('type', 'log')}: {m.get('text', '')[:500]}" for m in logs]
    return _truncate("\n".join(lines))


def browser_network(args: dict[str, Any], cwd: str, sid: str) -> str:
    action = (args.get("action") or "log").lower()
    if action == "start":
        sb.browser_call(sid, "/network/start")
        return "network recording started"
    if action == "stop":
        r = sb.browser_call(sid, "/network/stop")
        return f"network recording stopped ({r.get('count', 0)} entries)"
    if action == "clear":
        sb.browser_call(sid, "/network/start")  # also clears
        return "network log cleared and recording (re)started"
    # log
    payload = {"limit": int(args.get("limit", 200))}
    if args.get("filter"):
        payload["filter"] = args["filter"]
    r = sb.browser_call(sid, "/network/log", payload)
    logs = r.get("logs", [])
    if not logs:
        return "(no network entries; recording=" + ("on" if r.get("recording") else "off") + ")"
    lines = [f"recording={r.get('recording')} total={r.get('total')} shown={len(logs)}"]
    for e in logs:
        st = e.get("status")
        fail = e.get("failure")
        tail = f" FAIL:{fail}" if fail else ""
        lines.append(
            f"  {e.get('method', '?'):<6} {st if st is not None else '---':<4} "
            f"{e.get('resource_type', '?'):<10} {e.get('url', '')}{tail}"
        )
    return _truncate("\n".join(lines))


def browser_accessibility(args: dict[str, Any], cwd: str, sid: str) -> str:
    payload = {
        "interesting_only": bool(args.get("interesting_only", True)),
    }
    if args.get("root"):
        payload["root"] = args["root"]
    r = sb.browser_call(sid, "/accessibility", payload)
    if r.get("error"):
        raise RuntimeError(r["error"])
    tree = r.get("tree") or {}
    out: list[str] = [f"# accessibility tree @ {r.get('url')}"]

    def walk(node, depth):
        if not node:
            return
        role = node.get("role", "?")
        name = node.get("name", "") or ""
        if len(name) > 80:
            name = name[:77] + "..."
        out.append("  " * depth + f"{role}: {name}".rstrip(": "))
        for ch in node.get("children") or []:
            walk(ch, depth + 1)

    walk(tree, 0)
    return _truncate("\n".join(out))


def browser_emulate(args: dict[str, Any], cwd: str, sid: str) -> str:
    payload = {
        k: args[k]
        for k in ("device", "width", "height", "device_scale_factor", "mobile", "dark_mode", "media")
        if k in args
    }
    r = sb.browser_call(sid, "/emulate", payload)
    if r.get("error"):
        raise RuntimeError(r["error"])
    return f"emulate applied: {r.get('applied')}"


# ---------- git ----------


def _git_run(sid: str, cwd: str, args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """Run a git subcommand with safe defaults inside the sandbox."""
    safe = ["-c", "safe.directory=*", "-c", "core.pager=cat", "-c", "color.ui=never"]
    cmd = f"cd {shlex.quote(cwd)} && git " + " ".join(shlex.quote(x) for x in (safe + args))
    return sb.exec_bash(sid, cmd)


def git_tool(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Versatile git wrapper. ops: status, diff, log, show, blame,
    add, branch, checkout, stash, restore, ls_files, current_branch."""
    op = (args.get("op") or "status").lower()
    repo = args.get("path") or _get_cwd(sid)
    if op == "status":
        rc, out, err = _git_run(sid, repo, ["status", "--short", "--branch"])
    elif op == "diff":
        gargs = ["diff"]
        if args.get("cached"):
            gargs.append("--cached")
        if args.get("stat"):
            gargs.append("--stat")
        if args.get("path"):
            gargs.extend(["--", *([args["path"]] if isinstance(args["path"], str) else args["path"])])
        if args.get("ref"):
            gargs.insert(1, args["ref"])
        rc, out, err = _git_run(sid, repo, gargs, timeout=120)
    elif op == "log":
        n = int(args.get("limit", 20))
        gargs = ["log", f"-{n}", "--oneline", "--decorate"]
        if args.get("path"):
            p = args["path"]
            gargs.extend(["--", *([p] if isinstance(p, str) else p)])
        rc, out, err = _git_run(sid, repo, gargs)
    elif op == "show":
        ref = args.get("ref") or "HEAD"
        rc, out, err = _git_run(sid, repo, ["show", ref, "--stat", "-p"], timeout=120)
    elif op == "blame":
        if not args.get("file"):
            raise ValueError("git blame requires 'file'")
        gargs = ["blame", "--date=short"]
        if args.get("line_start") is not None and args.get("line_end") is not None:
            gargs.extend(["-L", f"{args['line_start']},{args['line_end']}"])
        gargs.append(args["file"])
        rc, out, err = _git_run(sid, repo, gargs)
    elif op == "add":
        files = args.get("files") or ["."]
        if isinstance(files, str):
            files = [files]
        rc, out, err = _git_run(sid, repo, ["add", *files])
        if rc == 0 and not out.strip():
            out = f"staged {len(files)} path(s)"
    elif op == "branch":
        rc, out, err = _git_run(sid, repo, ["branch", "-vv"])
    elif op == "current_branch":
        rc, out, err = _git_run(sid, repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    elif op == "checkout":
        if not args.get("ref"):
            raise ValueError("checkout requires 'ref'")
        gargs = ["checkout", args["ref"]]
        if args.get("create_new"):
            gargs = ["checkout", "-b", args["ref"]]
        rc, out, err = _git_run(sid, repo, gargs)
    elif op == "stash":
        sub = args.get("sub") or "push"
        gargs = ["stash", sub]
        if args.get("message"):
            gargs.extend(["-m", args["message"]])
        rc, out, err = _git_run(sid, repo, gargs)
    elif op == "restore":
        files = args.get("files") or ["."]
        if isinstance(files, str):
            files = [files]
        gargs = ["restore"]
        if args.get("staged"):
            gargs.append("--staged")
        gargs.extend(files)
        rc, out, err = _git_run(sid, repo, gargs)
    elif op == "ls_files":
        gargs = ["ls-files"]
        if args.get("modified"):
            gargs.append("--modified")
        rc, out, err = _git_run(sid, repo, gargs)
    elif op == "init":
        rc, out, err = _git_run(sid, repo, ["init"])
    else:
        raise ValueError(f"unknown git op: {op}")
    body = out
    if err:
        body = (body + "\n--- stderr ---\n" + err) if body else err
    body += f"\n--- exit {rc} ---"
    return _truncate(body)


def git_commit(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Stage + commit. If args.stage is True (default), stages all changes
    first. Returns the new commit hash on success."""
    repo = args.get("path") or _get_cwd(sid)
    message = args.get("message") or ""
    if not message.strip():
        raise ValueError("commit requires non-empty 'message'")
    author = args.get("author")  # e.g. 'Kira <kira@webchat>'
    if args.get("stage", True):
        rc, _, err = _git_run(sid, repo, ["add", "-A"])
        if rc != 0:
            raise RuntimeError("git add failed: " + err.strip())
    gargs = ["commit", "-m", message]
    if author:
        gargs.extend(["--author", author])
    if args.get("allow_empty"):
        gargs.append("--allow-empty")
    rc, out, err = _git_run(sid, repo, gargs)
    if rc != 0:
        # 'nothing to commit' surfaces here — keep stderr for clarity.
        body = out + ("\n" + err if err else "")
        return f"--- exit {rc} ---\n{body.strip()}"
    # Get the new hash.
    rc2, hash_out, _ = _git_run(sid, repo, ["rev-parse", "--short", "HEAD"])
    head = hash_out.strip() if rc2 == 0 else "HEAD"
    return f"committed {head}: {message.splitlines()[0][:80]}\n{out.strip()}"


# ---------- run_tests ----------


def run_tests(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Detect and run the project's test suite.
    runner: 'pytest' | 'jest' | 'go' | 'auto' (default).
    Parses output and returns a compact summary: TOTAL, PASSED, FAILED,
    DURATION, and a tail of failure lines.
    """
    runner = (args.get("runner") or "auto").lower()
    repo = args.get("path") or _get_cwd(sid)
    target = args.get("target") or ""
    extra = args.get("extra") or ""
    if runner == "auto":
        rc, _, _ = sb.exec_bash(
            sid,
            f"test -f {shlex.quote(repo)}/pyproject.toml || test -d {shlex.quote(repo)}/tests || ls {shlex.quote(repo)}/test_*.py 2>/dev/null | head -1",
        )
        if rc == 0:
            runner = "pytest"
        else:
            rc, _, _ = sb.exec_bash(sid, f"test -f {shlex.quote(repo)}/package.json")
            if rc == 0:
                runner = "jest"
            else:
                rc, _, _ = sb.exec_bash(sid, f"test -f {shlex.quote(repo)}/go.mod")
                if rc == 0:
                    runner = "go"
                else:
                    return "run_tests: cannot auto-detect runner; pass runner=pytest|jest|go"
    if runner == "pytest":
        # Prefer project venv if it exists.
        rc_v, _, _ = sb.exec_bash(sid, f"test -x {shlex.quote(repo)}/.venv/bin/pytest")
        pytest_bin = f"{repo}/.venv/bin/pytest" if rc_v == 0 else "pytest"
        cmd = f"cd {shlex.quote(repo)} && {pytest_bin} -q --tb=short {shlex.quote(target) if target else ''} {extra}"
    elif runner == "jest":
        cmd = (
            f"cd {shlex.quote(repo)} && npx --no-install jest --silent {shlex.quote(target) if target else ''} {extra}"
        )
    elif runner == "go":
        pkg = target or "./..."
        cmd = f"cd {shlex.quote(repo)} && go test {extra} {shlex.quote(pkg)}"
    else:
        raise ValueError(f"unknown runner: {runner}")
    rc, out, err = sb.exec_bash(sid, cmd)
    combined = (out or "") + ("\n" + err if err else "")
    summary = _parse_test_output(runner, combined, rc)
    return _truncate(summary + "\n--- raw tail ---\n" + combined[-2000:])


def _parse_test_output(runner: str, text: str, rc: int) -> str:
    import re as _re

    if runner == "pytest":
        # "3 failed, 42 passed, 4 warnings in 0.52s"
        m = _re.search(
            r"(?:(\d+) failed,?\s*)?(?:(\d+) passed,?\s*)?(?:(\d+) skipped,?\s*)?"
            r"(?:(\d+) errors?,?\s*)?(?:(\d+) warnings?,?\s*)?"
            r"in ([\d.]+)s",
            text,
        )
        if m:
            failed = int(m.group(1) or 0)
            passed = int(m.group(2) or 0)
            skipped = int(m.group(3) or 0)
            errors = int(m.group(4) or 0)
            dur = m.group(6)
            status = "PASS" if (rc == 0 and failed + errors == 0) else "FAIL"
            head = (
                f"TESTS={status} runner=pytest passed={passed} failed={failed} "
                f"errors={errors} skipped={skipped} duration={dur}s"
            )
            fails = _re.findall(r"^FAILED .+$", text, _re.MULTILINE)
            if fails:
                head += "\n" + "\n".join(fails[:30])
            return head
    elif runner == "jest":
        m = _re.search(
            r"Tests?:\s+(?:(\d+) failed,?\s*)?(?:(\d+) passed,?\s*)?" r"(?:(\d+) skipped,?\s*)?(\d+) total",
            text,
        )
        if m:
            failed = int(m.group(1) or 0)
            passed = int(m.group(2) or 0)
            total = int(m.group(4))
            status = "PASS" if rc == 0 and failed == 0 else "FAIL"
            return f"TESTS={status} runner=jest passed={passed} failed={failed} total={total}"
    elif runner == "go":
        ok = _re.findall(r"^ok\s+(\S+).*?(\d+\.\d+)s", text, _re.MULTILINE)
        bad = _re.findall(r"^FAIL\s+(\S+)", text, _re.MULTILINE)
        status = "PASS" if rc == 0 and not bad else "FAIL"
        return f"TESTS={status} runner=go ok={len(ok)} fail={len(bad)}" + (
            ("\nFAIL packages: " + ", ".join(bad)) if bad else ""
        )
    return f"TESTS={'PASS' if rc == 0 else 'FAIL'} runner={runner} rc={rc}"


# ---------- lint ----------


def lint(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Quick syntax / type / style check for a single file or a list.
    For .py: ruff (if available) else py_compile. For .js/.ts: node --check / tsc --noEmit (if tsconfig).
    Returns a structured 'LINT=OK' or 'LINT=FAIL' header.
    """
    paths = args.get("paths") or ([args["path"]] if args.get("path") else [])
    if not paths:
        raise ValueError("lint requires 'path' or 'paths'")
    if isinstance(paths, str):
        paths = [paths]
    issues: list[str] = []
    ok = True
    for p in paths:
        p_full = _cpath(p, sid)
        ext = p_full.rsplit(".", 1)[-1].lower() if "." in p_full else ""
        if ext == "py":
            rc_ruff, _, _ = sb.exec_bash(sid, "which ruff >/dev/null 2>&1")
            if rc_ruff == 0:
                rc, out, err = sb.exec_bash(sid, f"ruff check {shlex.quote(p_full)}")
            else:
                rc, out, err = sb.exec_bash(sid, f"python3 -m py_compile {shlex.quote(p_full)}")
            if rc != 0:
                ok = False
                issues.append(f"FAIL {p_full}\n{(out + err).strip()}")
            else:
                issues.append(f"OK   {p_full}")
        elif ext in ("js", "jsx", "mjs", "cjs"):
            rc, out, err = sb.exec_bash(sid, f"node --check {shlex.quote(p_full)}")
            if rc != 0:
                ok = False
                issues.append(f"FAIL {p_full}\n{(out + err).strip()}")
            else:
                issues.append(f"OK   {p_full}")
        elif ext in ("ts", "tsx"):
            rc_tsc, _, _ = sb.exec_bash(sid, "which tsc >/dev/null 2>&1 || which npx >/dev/null 2>&1")
            if rc_tsc == 0:
                rc, out, err = sb.exec_bash(sid, f"npx --no-install tsc --noEmit --allowJs {shlex.quote(p_full)} 2>&1")
                if rc != 0:
                    ok = False
                    issues.append(f"FAIL {p_full}\n{(out + err).strip()[:1000]}")
                else:
                    issues.append(f"OK   {p_full}")
            else:
                issues.append(f"SKIP {p_full} (no tsc available)")
        else:
            issues.append(f"SKIP {p_full} (no linter for .{ext})")
    header = "LINT=OK" if ok else "LINT=FAIL"
    return header + "\n" + "\n".join(issues)


# ---------- verify_change (runs INSIDE sandbox) ----------

# ---------- LSP-backed code intel ----------


def _lsp_resolve_pos(args: dict[str, Any], sid: str) -> tuple[str, int, int]:
    """Return (container_file, line0, col0).

    Accepts either explicit line/character (0-based LSP convention) OR a
    convenience pair (line_1based, character) where line_1based is 1-based.
    """
    f = args.get("file")
    if not f:
        raise ValueError("file is required")
    cpath = _cpath(f, sid)
    if "line" in args:
        line0 = int(args["line"])
    elif "line_1based" in args:
        line0 = int(args["line_1based"]) - 1
    else:
        raise ValueError("line (0-based) or line_1based is required")
    col0 = int(args.get("character", args.get("col", 0)))
    return cpath, line0, col0


def _find_symbol_pos(sid: str, file_path: str, symbol: str) -> tuple[int, int]:
    """Find first occurrence of identifier `symbol` in file. Return (line0, col0)."""
    import re

    cpath = _cpath(file_path, sid)
    text = sb.read_file(sid, cpath)
    pat = re.compile(r"\b" + re.escape(symbol) + r"\b")
    for i, line in enumerate(text.splitlines()):
        m = pat.search(line)
        if m:
            return i, m.start()
    raise ValueError(f"symbol {symbol!r} not found in {file_path}")


def _fmt_loc(loc: dict) -> str:
    return f"{loc['file']}:{loc['start_line'] + 1}:{loc['start_character'] + 1}"


def find_definition(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Find symbol definition. Args: file + (line,character) OR file + symbol.
    Lines/cols are 0-based LSP convention.
    """
    f = args.get("file")
    if not f:
        raise ValueError("file is required")
    if "symbol" in args and "line" not in args and "line_1based" not in args:
        line0, col0 = _find_symbol_pos(sid, f, args["symbol"])
        cpath = _cpath(f, sid)
    else:
        cpath, line0, col0 = _lsp_resolve_pos(args, sid)
    r = sb.lsp_call(sid, "/definition", {"file": cpath, "line": line0, "character": col0})
    locs = r.get("locations", [])
    if not locs:
        return f"DEFINITION not found for position {cpath}:{line0 + 1}:{col0 + 1}"
    out = [f"DEFINITION ({len(locs)}):"]
    for l in locs[:20]:
        out.append("  " + _fmt_loc(l))
    return "\n".join(out)


def find_references(args: dict[str, Any], cwd: str, sid: str) -> str:
    f = args.get("file")
    if not f:
        raise ValueError("file is required")
    if "symbol" in args and "line" not in args and "line_1based" not in args:
        line0, col0 = _find_symbol_pos(sid, f, args["symbol"])
        cpath = _cpath(f, sid)
    else:
        cpath, line0, col0 = _lsp_resolve_pos(args, sid)
    incl = bool(args.get("include_declaration", True))
    r = sb.lsp_call(
        sid,
        "/references",
        {
            "file": cpath,
            "line": line0,
            "character": col0,
            "include_declaration": incl,
        },
    )
    locs = r.get("locations", [])
    if not locs:
        return f"REFERENCES none for {cpath}:{line0 + 1}:{col0 + 1}"
    out = [f"REFERENCES ({len(locs)}):"]
    for l in locs[:200]:
        out.append("  " + _fmt_loc(l))
    return "\n".join(out)


def rename_symbol(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Compute rename edits via LSP and apply them.

    Args: file, (line+character | line_1based | symbol), new_name, apply?=true.
    With apply=false returns a preview only.
    """
    new_name = args.get("new_name")
    if not new_name:
        raise ValueError("new_name is required")
    f = args.get("file")
    if not f:
        raise ValueError("file is required")
    if "symbol" in args and "line" not in args and "line_1based" not in args:
        line0, col0 = _find_symbol_pos(sid, f, args["symbol"])
        cpath = _cpath(f, sid)
    else:
        cpath, line0, col0 = _lsp_resolve_pos(args, sid)
    apply = bool(args.get("apply", True))
    r = sb.lsp_call(
        sid,
        "/rename",
        {
            "file": cpath,
            "line": line0,
            "character": col0,
            "new_name": new_name,
        },
    )
    edits = r.get("edits", [])
    if not edits:
        return "RENAME no edits proposed (symbol may not be renameable here)"
    total = sum(len(e["edits"]) for e in edits)
    summary = [f"RENAME -> {new_name}  files={len(edits)}  edits={total}"]
    for e in edits[:20]:
        summary.append(f"  {e['file']}: {len(e['edits'])} edit(s)")
    if not apply:
        return "\n".join(summary) + "\n(preview only — apply=false)"
    # apply edits per file: read, splice ranges (sorted reverse), write
    for fe in edits:
        fp = fe["file"]
        text = sb.read_file(sid, fp)
        lines = text.splitlines(keepends=True)
        # build absolute char offsets per line
        offsets = [0]
        for ln in lines:
            offsets.append(offsets[-1] + len(ln))

        def to_offset(line: int, col: int) -> int:
            if line >= len(lines):
                return offsets[-1]
            return offsets[line] + min(col, len(lines[line]))

        # sort edits by (start_line, start_character) descending so splicing is safe
        sorted_edits = sorted(
            fe["edits"],
            key=lambda x: (x["start_line"], x["start_character"]),
            reverse=True,
        )
        buf = text
        for ed in sorted_edits:
            so = to_offset(ed["start_line"], ed["start_character"])
            eo = to_offset(ed["end_line"], ed["end_character"])
            buf = buf[:so] + ed["new_text"] + buf[eo:]
        # backup and write back
        bak = _backup_if_exists(sid, fp)
        sb.write_file(sid, fp, buf)
        summary.append(f"  applied {fp} (backup {bak})")
    return "\n".join(summary)


def diagnostics(args: dict[str, Any], cwd: str, sid: str) -> str:
    f = args.get("file")
    if not f:
        raise ValueError("file is required")
    cpath = _cpath(f, sid)
    wait_ms = int(args.get("wait_ms", 4000))
    r = sb.lsp_call(sid, "/diagnostics", {"file": cpath, "wait_ms": wait_ms}, timeout=max(30, wait_ms // 1000 + 10))
    diags = r.get("diagnostics", [])
    if not diags:
        return f"DIAGNOSTICS clean  file={cpath}"
    out = [f"DIAGNOSTICS file={cpath}  total={len(diags)}"]
    for d in diags[:200]:
        out.append(
            f"  {d['severity']:<7} {cpath}:{d['start_line'] + 1}:{d['start_character'] + 1}"
            f"  [{d.get('source', '')} {d.get('code', '')}] {d['message']}"
        )
    return "\n".join(out)


def verify_change(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Runs validation checks inside the sandbox via shell + python.
    Supports same keys as host verify_change. host.docker.internal works.
    """
    py_files = args.get("py_files") or []
    http_get = args.get("http_get") or []
    absent_in = args.get("absent_in") or []
    present_in = args.get("present_in") or []
    shells = args.get("shell") or []
    script = """
import sys,json,urllib.request,subprocess
spec=json.loads(sys.argv[1])
ok=True; out=[]
for p in spec.get('py_files') or []:
    try:
        compile(open(p).read(),p,'exec'); out.append('OK py_compile '+p)
    except Exception as e:
        ok=False; out.append('FAIL py_compile %s: %s'%(p,e))
for u in spec.get('http_get') or []:
    try:
        r=urllib.request.urlopen(u,timeout=10); code=r.getcode()
        out.append('OK http %s -> %d'%(u,code))
        if code>=400: ok=False
    except Exception as e:
        ok=False; out.append('FAIL http %s: %s'%(u,e))
for s in spec.get('absent_in') or []:
    try:
        t=open(s['path']).read(); n=t.count(s['pattern'])
        if n==0: out.append(\"OK absent '%s' in %s\"%(s['pattern'],s['path']))
        else: ok=False; out.append(\"FAIL '%s' found %dx in %s\"%(s['pattern'],n,s['path']))
    except Exception as e:
        ok=False; out.append('FAIL absent %s: %s'%(s['path'],e))
for s in spec.get('present_in') or []:
    try:
        t=open(s['path']).read(); n=t.count(s['pattern'])
        if n>=1: out.append(\"OK present '%s' in %s (%dx)\"%(s['pattern'],s['path'],n))
        else: ok=False; out.append(\"FAIL '%s' missing in %s\"%(s['pattern'],s['path']))
    except Exception as e:
        ok=False; out.append('FAIL present %s: %s'%(s['path'],e))
for c in spec.get('shell') or []:
    p=subprocess.run(['bash','-c',c],capture_output=True,text=True,timeout=60)
    tag='OK' if p.returncode==0 else 'FAIL'
    if p.returncode!=0: ok=False
    out.append('%s sh `%s` rc=%d\\n%s'%(tag,c,p.returncode,(p.stdout+p.stderr)[:500]))
print(('VERIFY=OK' if ok else 'VERIFY=FAIL')+'\\n'+'\\n'.join(out))
"""
    spec_json = __import__("json").dumps(
        {
            "py_files": py_files,
            "http_get": http_get,
            "absent_in": absent_in,
            "present_in": present_in,
            "shell": shells,
        }
    )
    cmd = f"python3 -c {shlex.quote(script)} {shlex.quote(spec_json)}"
    rc, out, err = sb.exec_bash(sid, cmd)
    return _truncate((out + err).strip())


# ---------- dispatcher ----------

# ---------- critic (review_changes) ----------


def _get_git_diff(sid: str, cwd: str, ref: str | None = None) -> str:
    """Get the current diff for review.

    If ref is None: combined `git diff HEAD` (staged + working tree).
    Else: `git diff <ref>`.
    """
    cwd_c = _cpath(cwd, sid) if cwd else "/host/webchat"
    if not cwd_c or cwd_c == "/workspace":
        cwd_c = "/host/webchat"
    cmd = [
        "docker",
        "exec",
        "-w",
        cwd_c,
        sb.ensure_container(sid),
        "git",
        "-c",
        "safe.directory=*",
        "-c",
        "core.pager=cat",
        "diff",
        ref or "HEAD",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        # fallback: maybe no HEAD yet -> diff vs empty
        return r.stdout + r.stderr
    return r.stdout


def review_changes(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Run the critic on a diff. Either diff= is provided, or it is taken
    from `git diff HEAD` in the current cwd.
    """
    diff = args.get("diff")
    if not diff:
        ref = args.get("ref")
        diff = _get_git_diff(sid, cwd, ref=ref)
    if not (diff or "").strip():
        return "REVIEW=OK (no changes to review)"
    intent = args.get("intent") or ""
    api_key = os.environ.get("KIRO_API_KEY", "")
    if not api_key:
        return "REVIEW=OK (critic disabled: no KIRO_API_KEY)"
    model = args.get("model")
    # Critic runs on host (uses our key + key_pool). Bridge via sync wrapper.
    import asyncio
    import sys

    for p in ("/host/webchat",):
        if p not in sys.path and os.path.isdir(p):
            sys.path.insert(0, p)
    import agent_critic  # type: ignore

    try:
        loop = asyncio.new_event_loop()
        try:
            verdict = loop.run_until_complete(agent_critic.review_diff(api_key, diff, intent=intent, model=model))
        finally:
            loop.close()
    except Exception as e:
        return f"REVIEW=OK (critic-error: {type(e).__name__}: {e})"
    v = verdict.get("verdict", "OK")
    reason = verdict.get("reason", "")
    issues = verdict.get("issues", []) or []
    head = f"REVIEW={v}"
    if reason:
        head += f" reason: {reason}"
    lines = [head]
    for i in issues[:20]:
        lines.append(f"  - {i}")
    return "\n".join(lines)


import os  # noqa: E402 (used by review_changes above)

# ---------- memory (notebook BM25) ----------


def _memory_index():
    """Lazily build a sandbox-local MemoryIndex pointed at /host/notebook."""
    global _MEMORY_SINGLETON
    if _MEMORY_SINGLETON is None:
        import os
        import sys

        # ensure host webchat source dir is on sys.path so we can import
        # agent_memory (self-edit mount provides it at /host/webchat)
        for p in ("/host/webchat", "/workspace"):
            if p not in sys.path and os.path.isdir(p):
                sys.path.insert(0, p)
        # Only override when running inside the sandbox container where
        # /host/notebook is bind-mounted. On the host, leave the default
        # (~/notebook) so memory_add does not try to mkdir /host (EACCES).
        if os.path.isdir("/host/notebook"):
            os.environ.setdefault("KIRA_NOTEBOOK_DIR", "/host/notebook")
        import agent_memory  # type: ignore

        _MEMORY_SINGLETON = agent_memory.memory
    return _MEMORY_SINGLETON


_MEMORY_SINGLETON = None


def memory_search(args: dict[str, Any], cwd: str, sid: str) -> str:
    q = (args.get("query") or "").strip()
    if not q:
        raise ValueError("query is required")
    k = int(args.get("k") or 5)
    hits = _memory_index().search(q, k=k)
    if not hits:
        return f"MEMORY no hits for {q!r}"
    out = [f"MEMORY {len(hits)} hits for {q!r}:"]
    for h in hits:
        head = (" [" + h["heading"] + "]") if h.get("heading") else ""
        out.append(f"\n--- {h['file']}:{h['start_line']}-{h['end_line']}{head} (score={h['score']}) ---")
        out.append(h["snippet"])
    return "\n".join(out)


def memory_add(args: dict[str, Any], cwd: str, sid: str) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        raise ValueError("text is required")
    file = args.get("file")
    info = _memory_index().add(text, file=file)
    return f"MEMORY appended file={info['file']} bytes={info['bytes']} lines={info['lines']}"


def coverage_status(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Read coverage.json from the host repo (we always mount it at /host/webchat)."""
    import agent_coverage
    from agent_tools import _line_ranges  # reuse host helper

    path = (args.get("path") or "").strip()
    if path:
        d = agent_coverage.file_detail(path)
        if not d.get("ok"):
            return f"COVERAGE ERROR: {d.get('error')}"
        s = d["summary"]
        return (
            f"COVERAGE {path}: {s['percent']}% ({s['covered']}/{s['statements']} stmts)\n"
            f"missing lines ({s['missing']}): {_line_ranges(d['missing_lines'])}"
        )
    limit = int(args.get("limit") or 20)
    st = agent_coverage.status()
    if not st.get("ok"):
        return f"COVERAGE ERROR: {st.get('error')}. Run `make coverage` first."
    lines = [
        f"COVERAGE total={st['total_percent']}% ({st['total_covered']}/{st['total_statements']} stmts, age={st['age_seconds']}s)",
        "files (lowest coverage first):",
    ]
    for f in st["files"][:limit]:
        lines.append(f"  {f['percent']:>5.1f}%  {f['missing']:>4} missing  {f['path']}")
    if len(st["files"]) > limit:
        lines.append(f"... ({len(st['files']) - limit} more)")
    return "\n".join(lines)


def self_status(args: dict[str, Any], cwd: str, sid: str) -> str:
    """Read self-introspection snapshot from the host repo (mounted at /host/webchat)."""
    import os as _os
    import sys as _sys

    for p in ("/host/webchat", "/workspace"):
        if p not in _sys.path and _os.path.isdir(p):
            _sys.path.insert(0, p)
    import agent_self  # type: ignore

    # PROCESS_START is the *host* process; we don't have it inside the sandbox.
    # Try to read it from app module if importable, else omit uptime.
    start_ts = None
    try:
        import app as _app  # type: ignore

        start_ts = getattr(_app, "_PROCESS_START", None)
    except Exception:
        pass
    return agent_self.status_text(start_ts=start_ts)


def load_skill_tool(args: dict[str, Any], cwd: str, sid: str) -> str:
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
    "browser_navigate": browser_navigate,
    "browser_text": browser_text,
    "browser_eval": browser_eval,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_screenshot": browser_screenshot,
    "load_skill": load_skill_tool,
    "verify_change": verify_change,
    "change_dir": change_dir,
    "patch": patch,
    "keyword_search": keyword_search,
    "outline": outline,
    "browser_console_logs": browser_console_logs,
    "browser_network": browser_network,
    "browser_accessibility": browser_accessibility,
    "browser_emulate": browser_emulate,
    "git": git_tool,
    "git_commit": git_commit,
    "run_tests": run_tests,
    "lint": lint,
    "find_definition": find_definition,
    "find_references": find_references,
    "rename_symbol": rename_symbol,
    "diagnostics": diagnostics,
    "memory_search": memory_search,
    "memory_add": memory_add,
    "review_changes": review_changes,
    "coverage_status": coverage_status,
    "self_status": self_status,
}


def run_tool(name: str, args: dict[str, Any], cwd: str, sid: str) -> tuple[str, str, list[dict] | None]:
    """Returns (status, text, images_for_next_turn_or_None)."""
    fn = TOOLS.get(name)
    if fn is None:
        return "error", f"unknown tool: {name}", None
    try:
        result = fn(args, cwd, sid)
        if isinstance(result, tuple):
            text, images = result
            return "success", text, images or None
        return "success", result, None
    except Exception as e:
        return "error", f"{type(e).__name__}: {e}", None
