"""Tests for sandbox_tools.py with the docker-facing sandbox_runtime mocked.

We stub every `sb.*` call (exec_bash, read_file, write_file, browser_call,
lsp_call, ensure_container, _to_container_path) so no docker is touched.
The goal is to exercise the orchestration / parsing logic in sandbox_tools.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import sandbox_tools as st
import sandbox_runtime as sb


@pytest.fixture
def mock_sb(monkeypatch):
    """Stub sandbox_runtime calls. Provides a ScriptedSb container with a
    `set_exec_bash(fn)` etc. for per-test customisation."""

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


# ---------- _truncate / _cpath / _get_cwd ----------


def test_truncate_short():
    assert st._truncate("ok") == "ok"


def test_truncate_long():
    out = st._truncate("A" * 1000, max_chars=100)
    assert "[... truncated" in out


def test_cpath_delegates(mock_sb):
    assert st._cpath("/x", "sid") == "/x"
    mock_sb.to_container_path.assert_called()


def test_get_cwd_default(monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda sid, k, default: default)
    assert st._get_cwd("sid1") == "/workspace"


def test_get_cwd_from_store(monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda sid, k, default: "/home/x")
    assert st._get_cwd("sid1") == "/home/x"


# ---------- execute_bash / change_dir ----------


def test_execute_bash_appends_stderr_and_exit(mock_sb):
    mock_sb.exec_bash.return_value = (1, "out", "oops")
    out = st.execute_bash({"command": "x"}, cwd="/", sid="s")
    assert "out" in out and "--- stderr ---" in out and "oops" in out
    assert "--- exit 1 ---" in out


def test_change_dir_absolute(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "set_meta", lambda sid, k, v: None)
    mock_sb.exec_bash.return_value = (0, "/abs/path\n", "")
    out = st.change_dir({"path": "/abs/path"}, cwd="/", sid="s")
    assert "/abs/path" in out


def test_change_dir_relative(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **kw: "/workspace")
    monkeypatch.setattr(agent_store, "set_meta", lambda *a, **kw: None)
    mock_sb.exec_bash.return_value = (0, "/workspace/sub\n", "")
    out = st.change_dir({"path": "sub"}, cwd="/", sid="s")
    assert "/workspace/sub" in out


def test_change_dir_error(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "No such file")
    with pytest.raises(OSError, match="No such file"):
        st.change_dir({"path": "/nope"}, cwd="/", sid="s")


# ---------- _read_line_op / _read_dir_op / _read_search_op / fs_read ----------


def test_read_line_op_ok(mock_sb):
    mock_sb.exec_bash.return_value = (0, "1: hi\n2: bye\n", "")
    out = st._read_line_op({"path": "/x"}, "s")
    assert "1: hi" in out


def test_read_line_op_error(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "nope")
    with pytest.raises(RuntimeError):
        st._read_line_op({"path": "/x"}, "s")


def test_read_dir_op_ls(mock_sb):
    mock_sb.exec_bash.return_value = (0, "a.txt\n", "")
    out = st._read_dir_op({"path": "/x"}, "s")
    assert "a.txt" in out


def test_read_dir_op_tree(mock_sb):
    mock_sb.exec_bash.return_value = (0, "x/\n  y.txt\n", "")
    out = st._read_dir_op({"path": "/x", "depth": 2, "exclude_patterns": ["*.pyc"]}, "s")
    assert "y.txt" in out
    cmd = mock_sb.exec_bash.call_args[0][1]
    assert "tree" in cmd and "*.pyc" in cmd


def test_read_dir_op_error(mock_sb):
    mock_sb.exec_bash.return_value = (2, "", "err")
    with pytest.raises(RuntimeError):
        st._read_dir_op({"path": "/x"}, "s")


def test_read_search_op_hit(mock_sb):
    mock_sb.exec_bash.return_value = (0, "file:1:hit\n", "")
    out = st._read_search_op({"path": "/x", "pattern": "hit"}, "s")
    assert "hit" in out


def test_read_search_op_no_match(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")
    assert st._read_search_op({"path": "/x", "pattern": "none"}, "s") == "(no matches)"


def test_read_search_op_error(mock_sb):
    mock_sb.exec_bash.return_value = (2, "", "bad regex")
    with pytest.raises(RuntimeError):
        st._read_search_op({"path": "/x", "pattern": "("}, "s")


def test_fs_read_line_mode(mock_sb):
    mock_sb.exec_bash.return_value = (0, "1: hi", "")
    out = st.fs_read({"operations": [{"mode": "Line", "path": "/x"}]}, cwd="/", sid="s")
    assert "=== Line" in out


def test_fs_read_unknown_mode(mock_sb):
    out = st.fs_read({"operations": [{"mode": "Bogus", "path": "/x"}]}, cwd="/", sid="s")
    assert "not implemented" in out


def test_fs_read_error_caught(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "explosion")
    out = st.fs_read({"operations": [{"mode": "Line", "path": "/x"}]}, cwd="/", sid="s")
    assert "ERROR" in out


def test_fs_read_image_mode(mock_sb, monkeypatch):
    """Image mode uses subprocess.run on docker exec; mock that out."""

    class FakeProc:
        returncode = 0
        stdout = b"\x89PNGFAKEDATA"

    monkeypatch.setattr(
        st.subprocess, "run", lambda *a, **k: FakeProc()
    )
    res = st.fs_read(
        {"operations": [{"mode": "Image", "path": "/workspace/a.png"}]},
        cwd="/",
        sid="s",
    )
    assert isinstance(res, tuple)
    text, images = res
    assert images and images[0]["format"] == "png"


# ---------- fs_write ----------


def test_fs_write_create_with_backup(mock_sb):
    mock_sb.exec_bash.return_value = (0, "", "")  # test -f finds the file
    out = st.fs_write(
        {"command": "create", "path": "/x.txt", "file_text": "hi"}, cwd="/", sid="s"
    )
    assert "Created" in out and "[BACKUP=" in out
    mock_sb.write_file.assert_called_once()


def test_fs_write_create_no_backup(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "not found")  # test -f -> miss
    out = st.fs_write(
        {"command": "create", "path": "/new.txt", "file_text": "hi"}, cwd="/", sid="s"
    )
    assert "Created" in out and "[BACKUP=" not in out


def test_fs_write_append(mock_sb):
    mock_sb.exec_bash.return_value = (0, "", "")
    out = st.fs_write(
        {"command": "append", "path": "/x.txt", "new_str": "data"}, cwd="/", sid="s"
    )
    assert "Appended" in out


def test_fs_write_append_error(mock_sb):
    """backup test -f returns 0 (file exists), the printf append fails."""
    calls = [
        (0, "", ""),  # test -f
        (0, "", ""),  # cp (backup)
        (1, "", "disk full"),  # printf
    ]
    mock_sb.exec_bash.side_effect = calls
    with pytest.raises(OSError, match="disk full"):
        st.fs_write(
            {"command": "append", "path": "/x", "new_str": "y"}, cwd="/", sid="s"
        )


def test_fs_write_str_replace(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")  # no backup
    mock_sb.read_file.return_value = "hello world"
    out = st.fs_write(
        {"command": "str_replace", "path": "/x", "old_str": "world", "new_str": "there"},
        cwd="/",
        sid="s",
    )
    assert "Replaced 1" in out
    args, kwargs = mock_sb.write_file.call_args
    assert "hello there" in args[2]


def test_fs_write_str_replace_not_found(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")
    mock_sb.read_file.return_value = "abc"
    with pytest.raises(ValueError, match="not found"):
        st.fs_write(
            {"command": "str_replace", "path": "/x", "old_str": "zz", "new_str": "q"},
            cwd="/",
            sid="s",
        )


def test_fs_write_str_replace_ambiguous(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")
    mock_sb.read_file.return_value = "xx xx xx"
    with pytest.raises(ValueError, match="matches 3"):
        st.fs_write(
            {"command": "str_replace", "path": "/x", "old_str": "xx", "new_str": "y"},
            cwd="/",
            sid="s",
        )


def test_fs_write_insert(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")
    mock_sb.read_file.return_value = "a\nb\nc"
    out = st.fs_write(
        {"command": "insert", "path": "/x", "insert_line": 1, "new_str": "NEW"},
        cwd="/",
        sid="s",
    )
    assert "Inserted" in out
    args, _ = mock_sb.write_file.call_args
    assert args[2] == "a\nNEW\nb\nc"


def test_fs_write_unknown(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")
    with pytest.raises(ValueError, match="unknown command"):
        st.fs_write({"command": "weird", "path": "/x"}, cwd="/", sid="s")


# ---------- _reindent + patch ----------


def test_reindent_strips_and_adds():
    out = st._reindent("    foo\n    bar", strip="    ", add="  ")
    assert out == "  foo\n  bar"


def test_patch_replace(mock_sb):
    mock_sb.exec_bash.return_value = (0, "", "")  # file exists
    mock_sb.read_file.return_value = "hello world"
    out = st.patch(
        {
            "path": "/x",
            "patches": [{"operation": "replace", "oldText": "world", "newText": "there"}],
        },
        cwd="/",
        sid="s",
    )
    assert "patched /x" in out
    args, _ = mock_sb.write_file.call_args
    assert "hello there" in args[2]


def test_patch_append_eof(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")  # no file -> no backup
    out = st.patch(
        {
            "path": "/x",
            "patches": [{"operation": "overwrite", "newText": "first"},
                         {"operation": "append_eof", "newText": "\nsecond"}],
        },
        cwd="/",
        sid="s",
    )
    args, _ = mock_sb.write_file.call_args
    assert "first" in args[2] and "second" in args[2]


def test_patch_clipboards(mock_sb):
    mock_sb.exec_bash.return_value = (0, "", "")
    mock_sb.read_file.return_value = "AAA-BBB"
    out = st.patch(
        {
            "path": "/x",
            "patches": [
                {"operation": "replace", "oldText": "AAA", "toClipboard": "saved", "newText": "X"},
                {"operation": "replace", "oldText": "BBB", "fromClipboard": "saved"},
            ],
        },
        cwd="/",
        sid="s",
    )
    args, _ = mock_sb.write_file.call_args
    # First replace: AAA -> X, second: BBB -> AAA (clipboard)
    assert args[2] == "X-AAA"


def test_patch_unknown_clipboard(mock_sb):
    mock_sb.exec_bash.return_value = (0, "", "")
    mock_sb.read_file.return_value = "x"
    with pytest.raises(ValueError, match="not set"):
        st.patch(
            {
                "path": "/x",
                "patches": [{"operation": "replace", "oldText": "x", "fromClipboard": "ghost"}],
            },
            cwd="/",
            sid="s",
        )


def test_patch_replace_not_found(mock_sb):
    mock_sb.exec_bash.return_value = (0, "", "")
    mock_sb.read_file.return_value = "abc"
    with pytest.raises(ValueError, match="not found"):
        st.patch(
            {"path": "/x", "patches": [{"operation": "replace", "oldText": "zz", "newText": ""}]},
            cwd="/",
            sid="s",
        )


def test_patch_invalid_op(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")
    with pytest.raises(ValueError, match="invalid"):
        st.patch(
            {"path": "/x", "patches": [{"operation": "sneeze"}]},
            cwd="/",
            sid="s",
        )


def test_patch_empty_patches(mock_sb):
    with pytest.raises(ValueError, match="non-empty"):
        st.patch({"path": "/x", "patches": []}, cwd="/", sid="s")


def test_patch_reindent(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")
    out = st.patch(
        {
            "path": "/x",
            "patches": [
                {
                    "operation": "overwrite",
                    "newText": "    a\n    b",
                    "reindent": {"strip": "    ", "add": "  "},
                }
            ],
        },
        cwd="/",
        sid="s",
    )
    args, _ = mock_sb.write_file.call_args
    assert args[2] == "  a\n  b"


# ---------- glob / grep ----------


def test_glob_tool(mock_sb):
    mock_sb.exec_bash.return_value = (0, "a.py\nb.py\n", "")
    out = st.glob_tool({"pattern": "*.py"}, cwd="/", sid="s")
    assert "a.py" in out


def test_glob_tool_no_matches(mock_sb):
    mock_sb.exec_bash.return_value = (0, "", "")
    assert st.glob_tool({"pattern": "*.zzz"}, cwd="/", sid="s") == "(no matches)"


def test_glob_tool_error(mock_sb):
    mock_sb.exec_bash.return_value = (2, "", "perm denied")
    with pytest.raises(RuntimeError):
        st.glob_tool({"pattern": "x"}, cwd="/", sid="s")


def test_grep_tool_success(mock_sb):
    mock_sb.exec_bash.return_value = (0, "file.py:1:hit\n", "")
    out = st.grep_tool({"pattern": "hit"}, cwd="/", sid="s")
    assert "hit" in out


def test_grep_tool_no_match(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")
    assert st.grep_tool({"pattern": "zzz"}, cwd="/", sid="s") == "(no matches)"


def test_grep_tool_error(mock_sb):
    mock_sb.exec_bash.return_value = (2, "", "regex parse err")
    out = st.grep_tool({"pattern": "("}, cwd="/", sid="s")
    assert "ERROR rg" in out


# ---------- keyword_search ----------


def test_keyword_search_empty_terms(mock_sb):
    with pytest.raises(ValueError, match="non-empty"):
        st.keyword_search({"search_terms": []}, cwd="/", sid="s")


def test_keyword_search_ranks_results(mock_sb):
    rg_lines = [
        json.dumps(
            {
                "type": "match",
                "data": {
                    "path": {"text": "/workspace/a.py"},
                    "lines": {"text": "def foo():\n"},
                    "line_number": 3,
                },
            }
        ),
        json.dumps(
            {
                "type": "match",
                "data": {
                    "path": {"text": "/workspace/b.py"},
                    "lines": {"text": "foo = 1\n"},
                    "line_number": 1,
                },
            }
        ),
    ]
    mock_sb.exec_bash.return_value = (0, "\n".join(rg_lines), "")
    out = st.keyword_search({"search_terms": ["foo"]}, cwd="/", sid="s")
    # File with def-line should outrank the bare assignment.
    assert out.index("a.py") < out.index("b.py")
    assert "def foo" in out


def test_keyword_search_no_hits(mock_sb):
    mock_sb.exec_bash.return_value = (1, "", "")
    out = st.keyword_search({"search_terms": ["nope"]}, cwd="/", sid="s")
    assert out == "(no matches)"


# ---------- outline ----------


def test_outline_python(mock_sb):
    mock_sb.read_file.return_value = "def foo():\n    pass\n\nclass Bar:\n    def baz(self):\n        pass\n"
    out = st.outline({"path": "/x.py"}, cwd="/", sid="s")
    assert "def foo" in out and "class Bar" in out and "def baz" in out


def test_outline_js(mock_sb):
    mock_sb.read_file.return_value = "export function f() {}\nclass C {}\n"
    out = st.outline({"path": "/x.js"}, cwd="/", sid="s")
    assert "function f" in out and "class C" in out


def test_outline_go(mock_sb):
    mock_sb.read_file.return_value = "package main\nfunc Hello() {}\ntype S struct{}\n"
    out = st.outline({"path": "/x.go"}, cwd="/", sid="s")
    assert "Hello" in out and "S" in out


def test_outline_empty(mock_sb):
    mock_sb.read_file.return_value = ""
    assert st.outline({"path": "/x.py"}, cwd="/", sid="s") == "(empty file)"


def test_outline_unsupported(mock_sb):
    with pytest.raises(ValueError, match="unsupported"):
        st.outline({"path": "/x.cpp"}, cwd="/", sid="s")


def test_outline_no_symbols(mock_sb):
    mock_sb.read_file.return_value = "x = 1\ny = 2\n"
    out = st.outline({"path": "/x.py"}, cwd="/", sid="s")
    assert "no top-level symbols" in out


# ---------- browser_* ----------


def test_browser_navigate(mock_sb):
    mock_sb.browser_call.return_value = {"url": "https://x", "status": 200, "title": "T"}
    out = st.browser_navigate({"url": "https://x"}, cwd="/", sid="s")
    assert "Navigated" in out and "status=200" in out


def test_browser_text(mock_sb):
    mock_sb.browser_call.return_value = {"url": "https://x", "title": "T", "text": "body"}
    out = st.browser_text({}, cwd="/", sid="s")
    assert "body" in out and "https://x" in out


def test_browser_eval_ok(mock_sb):
    mock_sb.browser_call.return_value = {"result": "42"}
    assert st.browser_eval({"expression": "6*7"}, cwd="/", sid="s") == "42"


def test_browser_eval_error(mock_sb):
    mock_sb.browser_call.return_value = {"error": "ReferenceError"}
    with pytest.raises(RuntimeError, match="ReferenceError"):
        st.browser_eval({"expression": "undef"}, cwd="/", sid="s")


def test_browser_click(mock_sb):
    mock_sb.browser_call.return_value = {"url": "https://x/2"}
    out = st.browser_click({"selector": "#go"}, cwd="/", sid="s")
    assert "#go" in out and "https://x/2" in out


def test_browser_click_error(mock_sb):
    mock_sb.browser_call.return_value = {"error": "no such element"}
    with pytest.raises(RuntimeError):
        st.browser_click({"selector": "x"}, cwd="/", sid="s")


def test_browser_type_error(mock_sb):
    mock_sb.browser_call.return_value = {"error": "detached"}
    with pytest.raises(RuntimeError):
        st.browser_type({"selector": "i", "text": "x"}, cwd="/", sid="s")


def test_browser_type_ok(mock_sb):
    mock_sb.browser_call.return_value = {}
    out = st.browser_type({"selector": "#i", "text": "hi"}, cwd="/", sid="s")
    assert "#i" in out


def test_browser_screenshot(mock_sb, monkeypatch):
    import base64

    raw = b"\x89PNGfake"
    mock_sb.browser_call.return_value = {"png_b64": base64.b64encode(raw).decode(), "url": "https://x"}
    # Mock the Popen used to cat the file into the container.
    fake = MagicMock()
    fake.communicate.return_value = (b"", b"")
    fake.returncode = 0
    monkeypatch.setattr(st.subprocess, "Popen", lambda *a, **k: fake)
    text, imgs = st.browser_screenshot({"path": "/workspace/s.png"}, cwd="/", sid="s")
    assert imgs and imgs[0]["format"] == "png"
    assert "Screenshot" in text


def test_browser_screenshot_no_data(mock_sb):
    mock_sb.browser_call.return_value = {"error": "timeout"}
    with pytest.raises(RuntimeError):
        st.browser_screenshot({}, cwd="/", sid="s")


def test_browser_console_logs_clear(mock_sb):
    out = st.browser_console_logs({"clear": True}, cwd="/", sid="s")
    assert "cleared" in out


def test_browser_console_logs_list(mock_sb):
    mock_sb.browser_call.return_value = {"logs": [{"type": "log", "text": "hello"}]}
    out = st.browser_console_logs({}, cwd="/", sid="s")
    assert "hello" in out


def test_browser_console_logs_empty(mock_sb):
    mock_sb.browser_call.return_value = {"logs": []}
    assert st.browser_console_logs({}, cwd="/", sid="s") == "(no console logs)"


def test_browser_network_start(mock_sb):
    assert "started" in st.browser_network({"action": "start"}, cwd="/", sid="s")


def test_browser_network_stop(mock_sb):
    mock_sb.browser_call.return_value = {"count": 5}
    assert "5 entries" in st.browser_network({"action": "stop"}, cwd="/", sid="s")


def test_browser_network_clear(mock_sb):
    assert "cleared" in st.browser_network({"action": "clear"}, cwd="/", sid="s")


def test_browser_network_log_empty(mock_sb):
    mock_sb.browser_call.return_value = {"logs": [], "recording": True}
    out = st.browser_network({}, cwd="/", sid="s")
    assert "no network entries" in out


def test_browser_network_log_entries(mock_sb):
    mock_sb.browser_call.return_value = {
        "logs": [
            {"method": "GET", "status": 200, "resource_type": "document", "url": "https://x"},
            {"method": "GET", "failure": "timeout", "resource_type": "image", "url": "https://y"},
        ],
        "recording": True,
        "total": 2,
    }
    out = st.browser_network({"filter": "x"}, cwd="/", sid="s")
    assert "https://x" in out and "FAIL:timeout" in out


def test_browser_accessibility(mock_sb):
    mock_sb.browser_call.return_value = {
        "url": "https://x",
        "tree": {
            "role": "WebArea",
            "name": "Home",
            "children": [{"role": "button", "name": "Go"}],
        },
    }
    out = st.browser_accessibility({}, cwd="/", sid="s")
    assert "WebArea" in out and "button" in out and "Go" in out


def test_browser_accessibility_error(mock_sb):
    mock_sb.browser_call.return_value = {"error": "detached"}
    with pytest.raises(RuntimeError):
        st.browser_accessibility({}, cwd="/", sid="s")


def test_browser_emulate(mock_sb):
    mock_sb.browser_call.return_value = {"applied": {"device": "iPhone"}}
    out = st.browser_emulate({"device": "iPhone"}, cwd="/", sid="s")
    assert "iPhone" in out


def test_browser_emulate_error(mock_sb):
    mock_sb.browser_call.return_value = {"error": "unknown device"}
    with pytest.raises(RuntimeError):
        st.browser_emulate({"device": "x"}, cwd="/", sid="s")


# ---------- git_tool / git_commit ----------


def test_git_status(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    mock_sb.exec_bash.return_value = (0, "## main\n M file.txt\n", "")
    out = st.git_tool({"op": "status"}, cwd="/", sid="s")
    assert "main" in out and "file.txt" in out


def test_git_diff_with_options(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    mock_sb.exec_bash.return_value = (0, "diff --git\n", "")
    out = st.git_tool(
        {"op": "diff", "cached": True, "stat": True, "path": "x.py"}, cwd="/", sid="s"
    )
    assert "diff" in out


def test_git_log(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    mock_sb.exec_bash.return_value = (0, "abc Hello\n", "")
    out = st.git_tool({"op": "log", "limit": 5, "path": "x.py"}, cwd="/", sid="s")
    assert "abc" in out


def test_git_blame_requires_file(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    with pytest.raises(ValueError, match="requires 'file'"):
        st.git_tool({"op": "blame"}, cwd="/", sid="s")


def test_git_blame_with_lines(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    mock_sb.exec_bash.return_value = (0, "hash author\n", "")
    out = st.git_tool(
        {"op": "blame", "file": "x.py", "line_start": 1, "line_end": 5}, cwd="/", sid="s"
    )
    assert "hash" in out


def test_git_add(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    mock_sb.exec_bash.return_value = (0, "", "")
    out = st.git_tool({"op": "add", "files": "x.py"}, cwd="/", sid="s")
    assert "staged" in out


def test_git_checkout(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    mock_sb.exec_bash.return_value = (0, "Switched\n", "")
    out = st.git_tool({"op": "checkout", "ref": "feat", "create_new": True}, cwd="/", sid="s")
    assert "Switched" in out


def test_git_checkout_requires_ref(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    with pytest.raises(ValueError, match="checkout requires"):
        st.git_tool({"op": "checkout"}, cwd="/", sid="s")


def test_git_unknown_op(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    with pytest.raises(ValueError, match="unknown git op"):
        st.git_tool({"op": "floop"}, cwd="/", sid="s")


def test_git_commit_empty_message(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    with pytest.raises(ValueError, match="non-empty"):
        st.git_commit({"message": "  "}, cwd="/", sid="s")


def test_git_commit_add_failed(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    mock_sb.exec_bash.return_value = (1, "", "perm denied")
    with pytest.raises(RuntimeError, match="git add failed"):
        st.git_commit({"message": "x"}, cwd="/", sid="s")


def test_git_commit_success(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    calls = [
        (0, "", ""),                            # git add -A
        (0, "[main abc] msg\n", ""),            # git commit
        (0, "abc1234\n", ""),                   # rev-parse
    ]
    mock_sb.exec_bash.side_effect = calls
    out = st.git_commit({"message": "my msg"}, cwd="/", sid="s")
    assert "abc1234" in out


def test_git_commit_nothing_to_commit(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    mock_sb.exec_bash.side_effect = [
        (0, "", ""),  # add
        (1, "nothing to commit", ""),  # commit
    ]
    out = st.git_commit({"message": "x"}, cwd="/", sid="s")
    assert "exit 1" in out and "nothing to commit" in out


# ---------- run_tests / lint ----------


def test_run_tests_auto_pytest(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    # auto-detect: pyproject.toml exists -> pytest; venv missing -> 'pytest' bin
    mock_sb.exec_bash.side_effect = [
        (0, "", ""),  # test -f pyproject.toml (success in first command of OR chain)
        (1, "", ""),  # venv missing
        (0, "2 passed in 0.1s\n", ""),
    ]
    out = st.run_tests({}, cwd="/", sid="s")
    assert "TESTS=PASS" in out and "passed=2" in out


def test_run_tests_jest(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    mock_sb.exec_bash.return_value = (
        0,
        "Tests: 1 failed, 2 passed, 3 total\n",
        "",
    )
    out = st.run_tests({"runner": "jest"}, cwd="/", sid="s")
    assert "TESTS=FAIL" in out and "failed=1" in out


def test_run_tests_unknown_runner(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    with pytest.raises(ValueError, match="unknown runner"):
        st.run_tests({"runner": "weird"}, cwd="/", sid="s")


def test_run_tests_auto_cannot_detect(mock_sb, monkeypatch):
    import agent_store
    monkeypatch.setattr(agent_store, "get_meta", lambda *a, **k: "/workspace")
    mock_sb.exec_bash.side_effect = [
        (1, "", ""),  # pyproject/tests check
        (1, "", ""),  # package.json
        (1, "", ""),  # go.mod
    ]
    out = st.run_tests({}, cwd="/", sid="s")
    assert "cannot auto-detect" in out


def test_lint_py_ruff_ok(mock_sb):
    mock_sb.exec_bash.side_effect = [
        (0, "", ""),  # which ruff
        (0, "", ""),  # ruff check
    ]
    out = st.lint({"path": "/x.py"}, cwd="/", sid="s")
    assert "LINT=OK" in out


def test_lint_py_compile_fallback(mock_sb):
    mock_sb.exec_bash.side_effect = [
        (1, "", ""),  # which ruff missing
        (1, "syntax error", ""),  # py_compile fails
    ]
    out = st.lint({"paths": ["/x.py"]}, cwd="/", sid="s")
    assert "LINT=FAIL" in out


def test_lint_js(mock_sb):
    mock_sb.exec_bash.return_value = (0, "", "")
    out = st.lint({"path": "/x.js"}, cwd="/", sid="s")
    assert "OK" in out


def test_lint_unknown_ext(mock_sb):
    out = st.lint({"path": "/x.rs"}, cwd="/", sid="s")
    assert "SKIP" in out


def test_lint_no_paths():
    with pytest.raises(ValueError, match="requires"):
        st.lint({}, cwd="/", sid="s")


# ---------- LSP-backed tools ----------


def test_lsp_resolve_pos_explicit_line(mock_sb):
    out = st._lsp_resolve_pos({"file": "/x.py", "line": 4, "character": 5}, "s")
    assert out == ("/x.py", 4, 5)


def test_lsp_resolve_pos_line_1based(mock_sb):
    out = st._lsp_resolve_pos({"file": "/x.py", "line_1based": 7}, "s")
    assert out == ("/x.py", 6, 0)


def test_lsp_resolve_pos_missing_file():
    with pytest.raises(ValueError, match="file is required"):
        st._lsp_resolve_pos({}, "s")


def test_find_symbol_pos(mock_sb):
    mock_sb.read_file.return_value = "a = 1\ndef foo():\n  pass\n"
    line, col = st._find_symbol_pos("s", "/x.py", "foo")
    assert line == 1
    assert col > 0


def test_find_symbol_pos_missing(mock_sb):
    mock_sb.read_file.return_value = "a=1\n"
    with pytest.raises(ValueError, match="not found"):
        st._find_symbol_pos("s", "/x.py", "ghost")


def test_find_definition_by_symbol(mock_sb):
    mock_sb.read_file.return_value = "def foo():\n  pass\n"
    mock_sb.lsp_call.return_value = {
        "locations": [
            {"file": "/x.py", "start_line": 0, "start_character": 4}
        ]
    }
    out = st.find_definition({"file": "/x.py", "symbol": "foo"}, cwd="/", sid="s")
    assert "DEFINITION" in out and "x.py" in out


def test_find_definition_not_found(mock_sb):
    mock_sb.lsp_call.return_value = {"locations": []}
    out = st.find_definition({"file": "/x.py", "line": 0, "character": 0}, cwd="/", sid="s")
    assert "DEFINITION not found" in out


def test_find_references_none(mock_sb):
    mock_sb.lsp_call.return_value = {"locations": []}
    out = st.find_references({"file": "/x.py", "line": 0, "character": 0}, cwd="/", sid="s")
    assert "REFERENCES none" in out


def test_find_references_list(mock_sb):
    mock_sb.lsp_call.return_value = {
        "locations": [
            {"file": "/x.py", "start_line": 1, "start_character": 0},
            {"file": "/y.py", "start_line": 2, "start_character": 4},
        ]
    }
    out = st.find_references(
        {"file": "/x.py", "line": 0, "character": 0}, cwd="/", sid="s"
    )
    assert "REFERENCES (2)" in out and "y.py" in out


def test_rename_symbol_no_new_name():
    with pytest.raises(ValueError, match="new_name"):
        st.rename_symbol({"file": "/x.py", "line": 0, "character": 0}, cwd="/", sid="s")


def test_rename_symbol_preview(mock_sb):
    mock_sb.lsp_call.return_value = {
        "edits": [
            {
                "file": "/x.py",
                "edits": [
                    {
                        "start_line": 0,
                        "start_character": 4,
                        "end_line": 0,
                        "end_character": 7,
                        "new_text": "bar",
                    }
                ],
            }
        ]
    }
    out = st.rename_symbol(
        {"file": "/x.py", "line": 0, "character": 0, "new_name": "bar", "apply": False},
        cwd="/",
        sid="s",
    )
    assert "preview only" in out and "bar" in out


def test_rename_symbol_no_edits(mock_sb):
    mock_sb.lsp_call.return_value = {"edits": []}
    out = st.rename_symbol(
        {"file": "/x.py", "line": 0, "character": 0, "new_name": "bar"}, cwd="/", sid="s"
    )
    assert "no edits" in out


def test_rename_symbol_apply(mock_sb):
    mock_sb.lsp_call.return_value = {
        "edits": [
            {
                "file": "/x.py",
                "edits": [
                    {
                        "start_line": 0,
                        "start_character": 4,
                        "end_line": 0,
                        "end_character": 7,
                        "new_text": "bar",
                    }
                ],
            }
        ]
    }
    mock_sb.read_file.return_value = "def foo():\n    return 1\n"
    # test -f for backup -> exists
    mock_sb.exec_bash.return_value = (0, "", "")
    out = st.rename_symbol(
        {"file": "/x.py", "line": 0, "character": 0, "new_name": "bar"},
        cwd="/",
        sid="s",
    )
    assert "applied" in out
    args, _ = mock_sb.write_file.call_args
    assert "def bar()" in args[2]


def test_diagnostics_clean(mock_sb):
    mock_sb.lsp_call.return_value = {"diagnostics": []}
    out = st.diagnostics({"file": "/x.py"}, cwd="/", sid="s")
    assert "clean" in out


def test_diagnostics_with_issues(mock_sb):
    mock_sb.lsp_call.return_value = {
        "diagnostics": [
            {
                "severity": "Error",
                "start_line": 0,
                "start_character": 4,
                "source": "pyright",
                "code": "E1",
                "message": "oops",
            }
        ]
    }
    out = st.diagnostics({"file": "/x.py"}, cwd="/", sid="s")
    assert "Error" in out and "oops" in out


# ---------- verify_change / review_changes / memory ----------


def test_verify_change(mock_sb):
    mock_sb.exec_bash.return_value = (0, "VERIFY=OK\nOK py_compile /x.py", "")
    out = st.verify_change({"py_files": ["/x.py"]}, cwd="/", sid="s")
    assert "VERIFY=OK" in out


def test_review_changes_no_diff(mock_sb, monkeypatch):
    """When git diff returns empty, review_changes says no changes."""
    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(st.subprocess, "run", lambda *a, **k: FakeProc())
    out = st.review_changes({}, cwd="/", sid="s")
    assert "no changes" in out


def test_review_changes_no_key(mock_sb, monkeypatch):
    monkeypatch.setenv("KIRO_API_KEY", "")
    out = st.review_changes({"diff": "--- a\n+++ b\n@@\n+x"}, cwd="/", sid="s")
    assert "critic disabled" in out


def test_memory_search_empty():
    with pytest.raises(ValueError, match="query"):
        st.memory_search({}, cwd="/", sid="s")


def test_memory_search_no_hits(monkeypatch):
    monkeypatch.setattr(st, "_memory_index", lambda: MagicMock(search=lambda q, k=5: []))
    out = st.memory_search({"query": "x"}, cwd="/", sid="s")
    assert "no hits" in out


def test_memory_search_hits(monkeypatch):
    fake = MagicMock(
        search=lambda q, k=5: [
            {
                "file": "f.md",
                "start_line": 1,
                "end_line": 3,
                "heading": "H",
                "score": 0.5,
                "snippet": "text",
            }
        ]
    )
    monkeypatch.setattr(st, "_memory_index", lambda: fake)
    out = st.memory_search({"query": "x"}, cwd="/", sid="s")
    assert "f.md" in out and "text" in out


def test_memory_add_empty():
    with pytest.raises(ValueError, match="text"):
        st.memory_add({"text": ""}, cwd="/", sid="s")


def test_memory_add_success(monkeypatch):
    monkeypatch.setattr(
        st, "_memory_index", lambda: MagicMock(add=lambda text, file=None: {"file": "f.md", "bytes": 12, "lines": 2})
    )
    out = st.memory_add({"text": "hi"}, cwd="/", sid="s")
    assert "f.md" in out and "bytes=12" in out


# ---------- coverage_status / load_skill / run_tool ----------


def test_coverage_status_summary(monkeypatch):
    import agent_coverage
    monkeypatch.setattr(
        agent_coverage,
        "status",
        lambda: {
            "ok": True,
            "total_percent": 60,
            "total_covered": 6,
            "total_statements": 10,
            "age_seconds": 5,
            "files": [{"percent": 20.0, "missing": 8, "path": "a.py"}],
        },
    )
    out = st.coverage_status({}, cwd="/", sid="s")
    assert "COVERAGE total=60%" in out


def test_coverage_status_file_error(monkeypatch):
    import agent_coverage
    monkeypatch.setattr(agent_coverage, "file_detail", lambda p: {"ok": False, "error": "x"})
    out = st.coverage_status({"path": "a.py"}, cwd="/", sid="s")
    assert "COVERAGE ERROR" in out


def test_coverage_status_no_data(monkeypatch):
    import agent_coverage
    monkeypatch.setattr(agent_coverage, "status", lambda: {"ok": False, "error": "none"})
    out = st.coverage_status({}, cwd="/", sid="s")
    assert "COVERAGE ERROR" in out


def test_load_skill_empty():
    with pytest.raises(ValueError, match="name"):
        st.load_skill_tool({}, cwd="/", sid="s")


def test_load_skill_unknown(monkeypatch):
    import agent_skills
    monkeypatch.setattr(agent_skills, "load_skill", lambda n: None)
    monkeypatch.setattr(agent_skills, "list_skills", lambda: [{"name": "x"}])
    with pytest.raises(ValueError, match="unknown skill"):
        st.load_skill_tool({"name": "zzz"}, cwd="/", sid="s")


def test_load_skill_success(monkeypatch):
    import agent_skills
    monkeypatch.setattr(agent_skills, "load_skill", lambda n: "BODY")
    out = st.load_skill_tool({"name": "x"}, cwd="/", sid="s")
    assert out == "BODY"


def test_run_tool_unknown():
    status, text, _ = st.run_tool("nope", {}, cwd="/", sid="s")
    assert status == "error" and "unknown tool" in text


def test_run_tool_tuple_return(monkeypatch):
    def tuple_fn(args, cwd, sid):
        return "text", [{"format": "png", "source": {"bytes": "YQ=="}}]

    monkeypatch.setitem(st.TOOLS, "execute_bash", tuple_fn)
    status, text, imgs = st.run_tool("execute_bash", {}, cwd="/", sid="s")
    assert status == "success" and text == "text" and imgs


def test_run_tool_exception_to_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bang")

    monkeypatch.setitem(st.TOOLS, "execute_bash", boom)
    status, text, _ = st.run_tool("execute_bash", {}, cwd="/", sid="s")
    assert status == "error" and "bang" in text
