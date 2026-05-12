"""Top-up tests for sandbox_tools branches still uncovered after sweep B.

Targets the small one-line branches: patch ops (prepend_bof/overwrite/
fromClipboard), fs_read other-modes, glob error, grep details, browser
response fields, git ops (stash/restore/ls_files/init/diff/log/show),
lint variants, LSP find_references, rename_symbol full path, review
critic full path with stub agent_critic, memory_index singleton.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

import sandbox_runtime as sb
import sandbox_tools as st


@pytest.fixture
def mock_sb(monkeypatch):
    class Scripted:
        def __init__(self):
            self.exec_bash = MagicMock(return_value=(0, "", ""))
            self.exec_argv = MagicMock(return_value=(0, "", ""))
            self.read_file = MagicMock(return_value="")
            self.write_file = MagicMock(return_value=None)
            self.browser_call = MagicMock(return_value={})
            self.lsp_call = MagicMock(return_value={})
            self.ensure_container = MagicMock(return_value="kira-sb-test")
            self.to_container_path = MagicMock(side_effect=lambda p, sid: p)

    s = Scripted()
    monkeypatch.setattr(sb, "exec_bash", s.exec_bash)
    monkeypatch.setattr(sb, "exec_argv", s.exec_argv)
    monkeypatch.setattr(sb, "read_file", s.read_file)
    monkeypatch.setattr(sb, "write_file", s.write_file)
    monkeypatch.setattr(sb, "browser_call", s.browser_call)
    monkeypatch.setattr(sb, "lsp_call", s.lsp_call)
    monkeypatch.setattr(sb, "ensure_container", s.ensure_container)
    monkeypatch.setattr(sb, "_to_container_path", s.to_container_path)
    return s


@pytest.fixture(autouse=True)
def _clear_clipboards():
    st._CLIPBOARDS.clear()
    yield
    st._CLIPBOARDS.clear()


# ---------- patch ops: prepend_bof, overwrite, fromClipboard ----------


def test_patch_prepend_bof_empty_file(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")  # file_exists = False
    out = st.patch({"path": "new.txt", "patches": [{"operation": "prepend_bof", "newText": "head\n"}]}, cwd="/", sid="s")
    assert "prepend_bof" in out


def test_patch_prepend_bof_existing(mock_sb):
    mock_sb.exec_bash.return_value = (0, "", "")
    mock_sb.read_file.return_value = "body\n"
    out = st.patch({"path": "f.txt", "patches": [{"operation": "prepend_bof", "newText": "head\n"}]}, cwd="/", sid="s")
    args, kw = mock_sb.write_file.call_args
    assert args[2].startswith("head\nbody")


def test_patch_overwrite(mock_sb):
    mock_sb.exec_bash.return_value = (0, "", "")
    mock_sb.read_file.return_value = "old"
    out = st.patch({"path": "f.txt", "patches": [{"operation": "overwrite", "newText": "NEW"}]}, cwd="/", sid="s")
    args, _ = mock_sb.write_file.call_args
    assert args[2] == "NEW" and "overwrite" in out


# ---------- fs_read modes: Image already covered; check unknown-mode error inside ----------


def test_fs_read_image_too_large(mock_sb, monkeypatch):
    import subprocess as _sp
    class FakeRun:
        def __init__(self, *a, **kw):
            self.returncode = 0
            self.stdout = b"x" * (6 * 1024 * 1024)
            self.stderr = b""
    monkeypatch.setattr(_sp, "run", lambda *a, **kw: FakeRun())
    # fs_read catches ValueError per-op and includes ERROR in result
    out = st.fs_read({"operations": [{"mode": "Image", "path": "/x.png"}]}, cwd="/", sid="s")
    if isinstance(out, tuple):
        out = out[0]
    assert "too large" in out or "ERROR" in out


def test_fs_read_image_not_found(mock_sb, monkeypatch):
    import subprocess as _sp
    class FakeRun:
        def __init__(self, *a, **kw):
            self.returncode = 1
            self.stdout = b""
            self.stderr = b"no"
    monkeypatch.setattr(_sp, "run", lambda *a, **kw: FakeRun())
    out = st.fs_read({"operations": [{"mode": "Image", "path": "/missing.png"}]}, cwd="/", sid="s")
    # fs_read catches per-op exceptions and includes ERROR in the result text
    if isinstance(out, tuple):
        out = out[0]
    assert "ERROR" in out


def test_fs_read_image_unknown_ext_defaults_png(mock_sb, monkeypatch):
    import subprocess as _sp
    class FakeRun:
        def __init__(self, *a, **kw):
            self.returncode = 0
            self.stdout = b"data"
            self.stderr = b""
    monkeypatch.setattr(_sp, "run", lambda *a, **kw: FakeRun())
    out = st.fs_read({"operations": [{"mode": "Image", "path": "/x.zzz"}]}, cwd="/", sid="s")
    text, imgs = out
    assert imgs and imgs[0]["format"] == "png"


def test_fs_read_search_mode_no_match(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")
    out = st.fs_read({"operations": [{"mode": "Search", "path": "/x", "pattern": "nope"}]}, cwd="/", sid="s")
    assert "no matches" in out


def test_fs_read_search_mode_error(mock_sb):
    mock_sb.exec_bash.return_value = (2, "", "rg crashed")
    out = st.fs_read({"operations": [{"mode": "Search", "path": "/x", "pattern": "p"}]}, cwd="/", sid="s")
    assert "ERROR" in out and "rg crashed" in out


# ---------- glob error path ----------


def test_glob_tool_error(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "find: invalid")
    with pytest.raises(RuntimeError, match="find: invalid"):
        st.glob_tool({"pattern": "*.py"}, cwd="/", sid="s")


# ---------- git ops: stash, restore, ls_files, init, diff, log, show, current_branch ----------


def _stub_cwd(monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")


def test_git_diff_with_ref_and_flags(mock_sb, monkeypatch):
    _stub_cwd(monkeypatch)
    mock_sb.exec_bash.return_value = (0, "--- diff ---\n", "")
    out = st.git_tool({"op": "diff", "ref": "HEAD~1", "stat": True, "cached": True}, cwd="/", sid="s")
    assert "--- diff ---" in out


def test_git_log_default(mock_sb, monkeypatch):
    _stub_cwd(monkeypatch)
    mock_sb.exec_bash.return_value = (0, "abc1234 msg\n", "")
    out = st.git_tool({"op": "log", "limit": 5}, cwd="/", sid="s")
    assert "abc1234" in out


def test_git_show(mock_sb, monkeypatch):
    _stub_cwd(monkeypatch)
    mock_sb.exec_bash.return_value = (0, "commit abc\n", "")
    out = st.git_tool({"op": "show", "ref": "HEAD"}, cwd="/", sid="s")
    assert "commit abc" in out


def test_git_branch(mock_sb, monkeypatch):
    _stub_cwd(monkeypatch)
    mock_sb.exec_bash.return_value = (0, "* main\n", "")
    out = st.git_tool({"op": "branch"}, cwd="/", sid="s")
    assert "main" in out


def test_git_current_branch(mock_sb, monkeypatch):
    _stub_cwd(monkeypatch)
    mock_sb.exec_bash.return_value = (0, "main\n", "")
    out = st.git_tool({"op": "current_branch"}, cwd="/", sid="s")
    assert "main" in out


def test_git_stash(mock_sb, monkeypatch):
    _stub_cwd(monkeypatch)
    mock_sb.exec_bash.return_value = (0, "Saved working directory\n", "")
    out = st.git_tool({"op": "stash", "sub": "push", "message": "wip"}, cwd="/", sid="s")
    assert "Saved" in out


def test_git_restore(mock_sb, monkeypatch):
    _stub_cwd(monkeypatch)
    mock_sb.exec_bash.return_value = (0, "", "")
    out = st.git_tool({"op": "restore", "files": ["a.py"], "staged": True}, cwd="/", sid="s")
    assert "exit 0" in out


def test_git_restore_default_files(mock_sb, monkeypatch):
    _stub_cwd(monkeypatch)
    mock_sb.exec_bash.return_value = (0, "", "")
    st.git_tool({"op": "restore"}, cwd="/", sid="s")
    cmd = mock_sb.exec_bash.call_args[0][1]
    assert " restore " in cmd and "." in cmd


def test_git_ls_files(mock_sb, monkeypatch):
    _stub_cwd(monkeypatch)
    mock_sb.exec_bash.return_value = (0, "a.py\nb.py\n", "")
    out = st.git_tool({"op": "ls_files", "modified": True}, cwd="/", sid="s")
    assert "a.py" in out


def test_git_init(mock_sb, monkeypatch):
    _stub_cwd(monkeypatch)
    mock_sb.exec_bash.return_value = (0, "Initialized empty Git repo\n", "")
    out = st.git_tool({"op": "init"}, cwd="/", sid="s")
    assert "Initialized" in out


def test_git_stderr_included(mock_sb, monkeypatch):
    _stub_cwd(monkeypatch)
    mock_sb.exec_bash.return_value = (1, "", "fatal: not a git repo")
    out = st.git_tool({"op": "status"}, cwd="/", sid="s")
    assert "not a git repo" in out and "exit 1" in out


# ---------- review_changes full critic path ----------


def test_review_changes_critic_returns_block(mock_sb, monkeypatch):
    monkeypatch.setenv("KIRO_API_KEY", "ksk_x")
    # Stub git diff subprocess.run with a non-empty diff
    class FakeProc:
        returncode = 0
        stdout = "--- a\n+++ b\n@@\n+code\n"
        stderr = ""
    monkeypatch.setattr(st.subprocess, "run", lambda *a, **k: FakeProc())

    # Inject a fake agent_critic module
    fake = types.ModuleType("agent_critic")

    async def fake_review(api_key, diff, intent="", model=None):
        return {"verdict": "BLOCK", "reason": "contains bug", "issues": ["i1", "i2"]}

    fake.review_diff = fake_review
    monkeypatch.setitem(sys.modules, "agent_critic", fake)
    out = st.review_changes({"intent": "x"}, cwd="/", sid="s")
    assert "REVIEW=BLOCK" in out and "contains bug" in out and "i1" in out


def test_review_changes_critic_raises(mock_sb, monkeypatch):
    monkeypatch.setenv("KIRO_API_KEY", "ksk_x")
    class FakeProc:
        returncode = 0
        stdout = "diff body"
        stderr = ""
    monkeypatch.setattr(st.subprocess, "run", lambda *a, **k: FakeProc())

    fake = types.ModuleType("agent_critic")

    async def boom(*a, **kw):
        raise RuntimeError("net dead")

    fake.review_diff = boom
    monkeypatch.setitem(sys.modules, "agent_critic", fake)
    out = st.review_changes({"diff": "x"}, cwd="/", sid="s")
    assert "critic-error" in out and "net dead" in out


# ---------- memory_index singleton ----------


def test_memory_index_caches(monkeypatch):
    st._MEMORY_SINGLETON = None
    fake_mem = MagicMock()
    fake_module = types.ModuleType("agent_memory")
    fake_module.memory = fake_mem
    monkeypatch.setitem(sys.modules, "agent_memory", fake_module)
    a = st._memory_index()
    b = st._memory_index()
    assert a is b is fake_mem
    st._MEMORY_SINGLETON = None  # cleanup


# ---------- lint variants: ts file with no tooling, js failure ----------


def test_lint_js_fail(mock_sb):
    mock_sb.exec_bash.return_value = (1, "SyntaxError\n", "")
    out = st.lint({"path": "a.js"}, cwd="/", sid="s")
    assert "LINT=FAIL" in out and "SyntaxError" in out


def test_lint_ts_no_tooling(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")  # tsc / npx missing
    out = st.lint({"path": "a.ts"}, cwd="/", sid="s")
    assert "SKIP" in out and "no tsc" in out


def test_lint_ts_with_tooling_pass(mock_sb):
    calls = []
    def fake_exec(sid, cmd):
        calls.append(cmd)
        if "which tsc" in cmd:
            return (0, "", "")
        return (0, "", "")
    mock_sb.exec_bash.side_effect = fake_exec
    out = st.lint({"path": "a.ts"}, cwd="/", sid="s")
    assert "LINT=OK" in out


def test_lint_ts_with_tooling_fail(mock_sb):
    def fake_exec(sid, cmd):
        if "which tsc" in cmd:
            return (0, "", "")
        return (1, "TS2304: cannot find x", "")
    mock_sb.exec_bash.side_effect = fake_exec
    out = st.lint({"path": "a.ts"}, cwd="/", sid="s")
    assert "LINT=FAIL" in out


# ---------- browser_console_logs / browser_network / accessibility / emulate ----------


def test_browser_console_logs(mock_sb):
    mock_sb.browser_call.return_value = {"logs": [{"type": "info", "text": "hi"}]}
    out = st.browser_console_logs({"limit": 50}, cwd="/", sid="s")
    assert "info" in out and "hi" in out


def test_browser_console_logs_empty(mock_sb):
    mock_sb.browser_call.return_value = {"logs": []}
    out = st.browser_console_logs({}, cwd="/", sid="s")
    assert "no console logs" in out


def test_browser_console_logs_clear(mock_sb):
    out = st.browser_console_logs({"clear": True}, cwd="/", sid="s")
    assert "cleared" in out


def test_browser_network_start(mock_sb):
    out = st.browser_network({"action": "start"}, cwd="/", sid="s")
    assert "started" in out


def test_browser_network_stop(mock_sb):
    mock_sb.browser_call.return_value = {"count": 7}
    out = st.browser_network({"action": "stop"}, cwd="/", sid="s")
    assert "stopped" in out and "7" in out


def test_browser_network_clear(mock_sb):
    out = st.browser_network({"action": "clear"}, cwd="/", sid="s")
    assert "cleared" in out


def test_browser_network_log_with_entries(mock_sb):
    mock_sb.browser_call.return_value = {
        "recording": True,
        "total": 2,
        "logs": [
            {"method": "GET", "status": 200, "resource_type": "document", "url": "http://x"},
            {"method": "POST", "status": None, "resource_type": "xhr", "url": "http://y", "failure": "net::ERR"},
        ],
    }
    out = st.browser_network({"filter": "x"}, cwd="/", sid="s")
    assert "http://x" in out and "200" in out and "net::ERR" in out


def test_browser_network_log_empty(mock_sb):
    mock_sb.browser_call.return_value = {"recording": False, "logs": []}
    out = st.browser_network({}, cwd="/", sid="s")
    assert "no network entries" in out


def test_browser_accessibility(mock_sb):
    mock_sb.browser_call.return_value = {"tree": {"role": "WebArea"}}
    out = st.browser_accessibility({"action": "tree"}, cwd="/", sid="s")
    assert "WebArea" in out or "tree" in out.lower()


def test_browser_emulate(mock_sb):
    mock_sb.browser_call.return_value = {"ok": True}
    out = st.browser_emulate({"action": "device", "device": "iPhone 13"}, cwd="/", sid="s")
    assert out
