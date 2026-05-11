"""In-container LSP daemon for Kira.

Wraps pyright-langserver (Python) and typescript-language-server (TS/JS) over
JSON-RPC and exposes a small HTTP API for the host runtime.

Endpoints:
  GET  /healthz                 -> {ok:true, servers:[...]}
  POST /definition              {file, line, character}
  POST /references              {file, line, character, include_declaration?}
  POST /rename                  {file, line, character, new_name}  -> {edits:[{file, edits:[{start,end,new_text}]}], applied:bool}
  POST /diagnostics             {file}

Line/character are 0-based (LSP convention).
File paths are absolute paths inside the container (e.g. /host/webchat/x.py).
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import time
import urllib.parse
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn


# ---------- LSP language registry ----------

PY_EXTS = {".py", ".pyi"}
TS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def lang_for(path: str) -> Optional[str]:
    ext = pathlib.Path(path).suffix.lower()
    if ext in PY_EXTS:
        return "python"
    if ext in TS_EXTS:
        return "typescript"
    return None


LANG_ID_FOR_EXT = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescriptreact",
    ".js": "javascript", ".jsx": "javascriptreact",
    ".mjs": "javascript", ".cjs": "javascript",
}


SERVER_CMD = {
    "python": ["pyright-langserver", "--stdio"],
    "typescript": ["typescript-language-server", "--stdio"],
}


def path_to_uri(p: str) -> str:
    return "file://" + urllib.parse.quote(os.path.abspath(p))


def uri_to_path(u: str) -> str:
    if u.startswith("file://"):
        u = u[len("file://"):]
    return urllib.parse.unquote(u)


# ---------- JSON-RPC client over stdio ----------


class LspServer:
    def __init__(self, name: str, cmd: list[str], roots: list[str]):
        self.name = name
        self.cmd = cmd
        self.roots = roots
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._notifications: list[dict] = []
        self._open_files: dict[str, int] = {}  # uri -> version
        self._diag: dict[str, list] = {}
        self._lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._initialized = False

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        await self._initialize()

    async def _drain_stderr(self):
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                return
            # silently drop or print
            sys.stderr.write(f"[{self.name}] {line.decode(errors='replace')}")

    async def _read_loop(self):
        assert self.proc and self.proc.stdout
        reader = self.proc.stdout
        while True:
            headers = {}
            while True:
                line = await reader.readline()
                if not line:
                    return
                line = line.decode("ascii", errors="replace").rstrip("\r\n")
                if not line:
                    break
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            cl = int(headers.get("content-length", "0"))
            if cl <= 0:
                continue
            body = await reader.readexactly(cl)
            try:
                msg = json.loads(body)
            except Exception:
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg: dict):
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(int(msg["id"]), None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(RuntimeError(json.dumps(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
            return
        if msg.get("method") == "textDocument/publishDiagnostics":
            params = msg.get("params", {})
            self._diag[params.get("uri", "")] = params.get("diagnostics", [])
            return
        if "id" in msg and "method" in msg:
            method = msg.get("method", "")
            params = msg.get("params") or {}
            # server -> client request; reply with sensible defaults
            if method == "workspace/configuration":
                items = params.get("items") or []
                result = [{} for _ in items]
            elif method == "workspace/workspaceFolders":
                result = [
                    {"uri": path_to_uri(r), "name": os.path.basename(r) or r}
                    for r in self.roots if os.path.isdir(r)
                ]
            elif method == "client/registerCapability":
                result = None
            elif method == "window/workDoneProgress/create":
                result = None
            elif method == "workspace/applyEdit":
                result = {"applied": False}
            else:
                result = None
            await self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
            return
        # window/logMessage, $/progress, etc. — ignore

    async def _send(self, msg: dict):
        assert self.proc and self.proc.stdin
        data = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
        self.proc.stdin.write(header + data)
        await self.proc.stdin.drain()

    async def request(self, method: str, params: Any, timeout: float = 30.0):
        rid = self.next_id
        self.next_id += 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise

    async def notify(self, method: str, params: Any):
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _initialize(self):
        workspace_folders = [
            {"uri": path_to_uri(r), "name": os.path.basename(r) or r}
            for r in self.roots if os.path.isdir(r)
        ]
        root_uri = workspace_folders[0]["uri"] if workspace_folders else None
        params = {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "workspaceFolders": workspace_folders,
            "capabilities": {},
        }
        await self.request("initialize", params, timeout=30.0)
        await self.notify("initialized", {})
        # Give the server time to finish workspace scan before we send didOpen.
        await asyncio.sleep(2.0)
        self._initialized = True

    async def ensure_open(self, path: str):
        uri = path_to_uri(path)
        if uri in self._open_files:
            # send didChange with current content (in case host edited it)
            version = self._open_files[uri] + 1
            self._open_files[uri] = version
            try:
                text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                raise HTTPException(404, f"cannot read {path}: {e}")
            await self.notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text}],
            })
            return uri
        try:
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raise HTTPException(404, f"cannot read {path}: {e}")
        ext = pathlib.Path(path).suffix.lower()
        lang_id = LANG_ID_FOR_EXT.get(ext, "plaintext")
        self._open_files[uri] = 1
        await self.notify("textDocument/didOpen", {
            "textDocument": {"uri": uri, "languageId": lang_id, "version": 1, "text": text},
        })
        # Give the server a chance to index the file before we ask questions.
        # Wait for first diagnostics publish (any file) as readiness signal,
        # then a small extra delay for cross-file analysis to settle.
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if self._diag:
                break
            await asyncio.sleep(0.1)
        # specific file readiness
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if uri in self._diag:
                break
            await asyncio.sleep(0.1)
        return uri

    async def wait_diagnostics(self, uri: str, timeout: float = 4.0) -> list:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if uri in self._diag:
                return self._diag[uri]
            await asyncio.sleep(0.1)
        return self._diag.get(uri, [])


# ---------- server registry ----------

SERVERS: dict[str, LspServer] = {}
# Prefer the project root (mounted self-edit dir) so the LSP sees a real
# codebase, not the empty /workspace scratch dir.
_ROOTS_ENV = os.environ.get("KIRA_LSP_ROOTS")
if _ROOTS_ENV:
    ROOTS = [r for r in _ROOTS_ENV.split(":") if os.path.isdir(r)]
else:
    ROOTS = [r for r in ("/host/webchat",) if os.path.isdir(r)] or ["/workspace"]
_servers_lock = asyncio.Lock()


async def get_server(lang: str) -> LspServer:
    async with _servers_lock:
        s = SERVERS.get(lang)
        if s and s.proc and s.proc.returncode is None:
            return s
        cmd = SERVER_CMD.get(lang)
        if not cmd:
            raise HTTPException(400, f"no LSP server for language {lang}")
        s = LspServer(lang, cmd, ROOTS)
        await s.start()
        SERVERS[lang] = s
        return s


# ---------- HTTP API ----------

app = FastAPI()


class PosReq(BaseModel):
    file: str
    line: int
    character: int


class RefReq(PosReq):
    include_declaration: bool = True


class RenameReq(PosReq):
    new_name: str


class DiagReq(BaseModel):
    file: str
    wait_ms: int = 4000


@app.get("/healthz")
async def healthz():
    return {"ok": True, "servers": list(SERVERS.keys()), "roots": ROOTS}


def _ensure_lang(path: str) -> str:
    l = lang_for(path)
    if not l:
        raise HTTPException(400, f"unsupported file extension: {path}")
    return l


def _loc_to_dict(loc: dict) -> dict:
    uri = loc.get("uri") or loc.get("targetUri") or ""
    rng = loc.get("range") or loc.get("targetRange") or {}
    return {
        "file": uri_to_path(uri),
        "start_line": rng.get("start", {}).get("line", 0),
        "start_character": rng.get("start", {}).get("character", 0),
        "end_line": rng.get("end", {}).get("line", 0),
        "end_character": rng.get("end", {}).get("character", 0),
    }


@app.post("/definition")
async def definition(req: PosReq):
    lang = _ensure_lang(req.file)
    srv = await get_server(lang)
    uri = await srv.ensure_open(req.file)
    res = await srv.request("textDocument/definition", {
        "textDocument": {"uri": uri},
        "position": {"line": req.line, "character": req.character},
    }, timeout=20.0)
    if res is None:
        return {"locations": []}
    if isinstance(res, dict):
        res = [res]
    return {"locations": [_loc_to_dict(x) for x in res]}


@app.post("/references")
async def references(req: RefReq):
    lang = _ensure_lang(req.file)
    srv = await get_server(lang)
    uri = await srv.ensure_open(req.file)
    res = await srv.request("textDocument/references", {
        "textDocument": {"uri": uri},
        "position": {"line": req.line, "character": req.character},
        "context": {"includeDeclaration": req.include_declaration},
    })
    if not res:
        return {"locations": []}
    return {"locations": [_loc_to_dict(x) for x in res]}


@app.post("/rename")
async def rename(req: RenameReq):
    lang = _ensure_lang(req.file)
    srv = await get_server(lang)
    uri = await srv.ensure_open(req.file)
    res = await srv.request("textDocument/rename", {
        "textDocument": {"uri": uri},
        "position": {"line": req.line, "character": req.character},
        "newName": req.new_name,
    })
    if not res:
        return {"edits": [], "changed_files": 0}
    edits_by_file: dict[str, list] = {}
    changes = res.get("changes") or {}
    for u, edits in changes.items():
        edits_by_file.setdefault(uri_to_path(u), []).extend(edits)
    for dc in res.get("documentChanges") or []:
        if "textDocument" in dc and "edits" in dc:
            u = dc["textDocument"].get("uri", "")
            edits_by_file.setdefault(uri_to_path(u), []).extend(dc.get("edits", []))
    out = []
    for fp, eds in edits_by_file.items():
        formatted = []
        for e in eds:
            r = e.get("range", {})
            formatted.append({
                "start_line": r.get("start", {}).get("line", 0),
                "start_character": r.get("start", {}).get("character", 0),
                "end_line": r.get("end", {}).get("line", 0),
                "end_character": r.get("end", {}).get("character", 0),
                "new_text": e.get("newText", ""),
            })
        out.append({"file": fp, "edits": formatted})
    return {"edits": out, "changed_files": len(out)}


@app.post("/diagnostics")
async def diagnostics(req: DiagReq):
    lang = _ensure_lang(req.file)
    srv = await get_server(lang)
    uri = await srv.ensure_open(req.file)
    diags = await srv.wait_diagnostics(uri, timeout=max(0.0, req.wait_ms / 1000.0))
    sev_map = {1: "error", 2: "warning", 3: "info", 4: "hint"}
    out = []
    for d in diags:
        r = d.get("range", {})
        out.append({
            "severity": sev_map.get(d.get("severity", 2), "warning"),
            "message": d.get("message", ""),
            "source": d.get("source", ""),
            "code": str(d.get("code", "")) if d.get("code") is not None else "",
            "start_line": r.get("start", {}).get("line", 0),
            "start_character": r.get("start", {}).get("character", 0),
            "end_line": r.get("end", {}).get("line", 0),
            "end_character": r.get("end", {}).get("character", 0),
        })
    return {"diagnostics": out, "file": req.file}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9001, log_level="warning")
