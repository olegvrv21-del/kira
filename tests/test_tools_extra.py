"""Coverage tests for agent_tools.py beyond the existing test_tools.py.

Covers: _truncate, fs_read (Line/Directory/Search/unknown), fs_write
append/insert/unknown, glob, grep (success/no-match/error/limits),
verify_change http/absent/shell paths, review_changes (no-diff/no-key/
with critic), memory_search/add, coverage_status (status/file/error),
_line_ranges, run_tool tuple-result.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import agent_tools as at


# ---------- _truncate ----------


def test_truncate_short_passthrough():
    assert at._truncate("hello", max_chars=100) == "hello"


def test_truncate_marks_when_long():
    text = "A" * 1000
    out = at._truncate(text, max_chars=100)
    assert "[... truncated" in out and len(out) < 1000


# ---------- _read_line_op ----------


def test_read_line_op_default_range(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("l1\nl2\nl3\n")
    out = at._read_line_op({"path": str(f)})
    assert "1: l1" in out and "3: l3" in out


def test_read_line_op_explicit_range(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("\n".join(f"line{i}" for i in range(1, 11)))
    out = at._read_line_op({"path": str(f), "start_line": 3, "end_line": 5})
    assert "3: line3" in out and "5: line5" in out and "line2" not in out


def test_read_line_op_negative_start(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a\nb\nc\nd\ne")
    out = at._read_line_op({"path": str(f), "start_line": -2})
    # last 2 lines (d,e)
    assert "d" in out and "e" in out
    assert "a" not in out.split(":")[1] if ":" in out else True


def test_read_line_op_negative_end(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a\nb\nc\nd\ne")
    out = at._read_line_op({"path": str(f), "start_line": 1, "end_line": -1})
    assert "a" in out


# ---------- _read_dir_op ----------


def test_read_dir_op_basic(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "sub" / "b.txt").write_text("")
    out = at._read_dir_op({"path": str(tmp_path), "depth": 1})
    assert "sub/" in out and "a.txt" in out and "b.txt" in out


def test_read_dir_op_excludes(tmp_path):
    (tmp_path / "keep.py").write_text("")
    (tmp_path / "skip.pyc").write_text("")
    out = at._read_dir_op({"path": str(tmp_path), "exclude_patterns": ["*.pyc"]})
    assert "keep.py" in out and "skip.pyc" not in out


def test_read_dir_op_on_file(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    out = at._read_dir_op({"path": str(f)})
    assert "file" in out and "bytes" in out


# ---------- _read_search_op ----------


def test_read_search_op_hits(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("alpha\nbravo\ncharlie\ndelta")
    out = at._read_search_op({"path": str(f), "pattern": "bravo", "context_lines": 1})
    assert "> 2: bravo" in out
    assert "alpha" in out and "charlie" in out


def test_read_search_op_no_match(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("abc")
    assert at._read_search_op({"path": str(f), "pattern": "xyz"}) == "(no matches)"


# ---------- fs_read dispatcher ----------


def test_fs_read_line_mode(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    out = at.fs_read({"operations": [{"mode": "Line", "path": str(f)}]}, cwd=str(tmp_path))
    assert "=== Line" in out and "1: hi" in out


def test_fs_read_unknown_mode(tmp_path):
    out = at.fs_read({"operations": [{"mode": "Bogus", "path": "x"}]}, cwd=str(tmp_path))
    assert "not implemented" in out


def test_fs_read_op_error_caught(tmp_path):
    out = at.fs_read(
        {"operations": [{"mode": "Line", "path": str(tmp_path / "nope")}]},
        cwd=str(tmp_path),
    )
    assert "ERROR" in out


# ---------- fs_write extras ----------


def test_fs_write_append(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    out = at.fs_write({"command": "append", "path": str(f), "new_str": "Y"}, cwd=str(tmp_path))
    assert f.read_text() == "xY"
    assert "Appended" in out


def test_fs_write_str_replace_not_found(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("abc")
    with pytest.raises(ValueError, match="not found"):
        at.fs_write({"command": "str_replace", "path": str(f), "old_str": "zz", "new_str": "q"}, cwd=str(tmp_path))


def test_fs_write_str_replace_ambiguous(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("xx xx")
    with pytest.raises(ValueError, match="matches 2 times"):
        at.fs_write({"command": "str_replace", "path": str(f), "old_str": "xx", "new_str": "yy"}, cwd=str(tmp_path))


def test_fs_write_insert(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a\nb\nc")
    out = at.fs_write(
        {"command": "insert", "path": str(f), "insert_line": 1, "new_str": "NEW"}, cwd=str(tmp_path)
    )
    assert "Inserted" in out
    assert f.read_text().splitlines() == ["a", "NEW", "b", "c"]


def test_fs_write_unknown_command(tmp_path):
    with pytest.raises(ValueError, match="unknown command"):
        at.fs_write({"command": "weird", "path": str(tmp_path / "x")}, cwd=str(tmp_path))


# ---------- glob ----------


def test_glob_finds_files(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("")
    (tmp_path / "c.txt").write_text("")
    out = at.glob_tool({"pattern": "*.py", "path": str(tmp_path)}, cwd=str(tmp_path))
    assert "a.py" in out and "b.py" in out and "c.txt" not in out


def test_glob_max_depth(tmp_path):
    (tmp_path / "a.py").write_text("")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "deep.py").write_text("")
    out = at.glob_tool(
        {"pattern": "*.py", "path": str(tmp_path), "max_depth": 1}, cwd=str(tmp_path)
    )
    assert "a.py" in out and "deep.py" not in out


def test_glob_limit(tmp_path):
    for i in range(20):
        (tmp_path / f"f{i}.py").write_text("")
    out = at.glob_tool({"pattern": "*.py", "path": str(tmp_path), "limit": 5}, cwd=str(tmp_path))
    assert len(out.splitlines()) == 5


def test_glob_no_matches(tmp_path):
    out = at.glob_tool({"pattern": "*.zzz", "path": str(tmp_path)}, cwd=str(tmp_path))
    assert out == "(no matches)"


# ---------- grep ----------


def test_grep_success(tmp_path):
    (tmp_path / "a.txt").write_text("hello world\nfoo bar\n")
    out = at.grep_tool({"pattern": "hello", "path": str(tmp_path)}, cwd=str(tmp_path))
    assert "hello world" in out


def test_grep_no_match(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    out = at.grep_tool({"pattern": "zzzzzz", "path": str(tmp_path)}, cwd=str(tmp_path))
    assert out == "(no matches)"


def test_grep_files_with_matches(tmp_path):
    (tmp_path / "a.txt").write_text("target")
    (tmp_path / "b.txt").write_text("target")
    out = at.grep_tool(
        {"pattern": "target", "path": str(tmp_path), "output_mode": "files_with_matches"},
        cwd=str(tmp_path),
    )
    assert "a.txt" in out and "b.txt" in out


def test_grep_count_mode(tmp_path):
    (tmp_path / "a.txt").write_text("x\nx\ny")
    out = at.grep_tool(
        {"pattern": "x", "path": str(tmp_path), "output_mode": "count"}, cwd=str(tmp_path)
    )
    assert ":2" in out  # ripgrep prints '<path>:<count>'


def test_grep_case_sensitive(tmp_path):
    (tmp_path / "a.txt").write_text("HELLO\nhello\n")
    out = at.grep_tool(
        {"pattern": "HELLO", "path": str(tmp_path), "case_sensitive": True}, cwd=str(tmp_path)
    )
    assert "HELLO" in out
    # lowercase 'hello' line should not appear
    body_lines = [ln for ln in out.splitlines() if "hello" in ln and "HELLO" not in ln]
    assert not body_lines


def test_grep_with_include(tmp_path):
    (tmp_path / "a.py").write_text("FIND")
    (tmp_path / "a.txt").write_text("FIND")
    out = at.grep_tool(
        {"pattern": "FIND", "path": str(tmp_path), "include": "*.py"}, cwd=str(tmp_path)
    )
    assert "a.py" in out and "a.txt" not in out


def test_grep_rg_error(monkeypatch, tmp_path):
    """Simulate rg returning exit > 1 (e.g., bad regex)."""

    class FakeProc:
        returncode = 2
        stdout = ""
        stderr = "regex parse error"

    monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: FakeProc())
    out = at.grep_tool({"pattern": "(", "path": str(tmp_path)}, cwd=str(tmp_path))
    assert "ERROR rg" in out


def test_grep_max_total_lines(tmp_path):
    (tmp_path / "a.txt").write_text("hit\n" * 10)
    out = at.grep_tool(
        {"pattern": "hit", "path": str(tmp_path), "max_total_lines": 3}, cwd=str(tmp_path)
    )
    assert len(out.splitlines()) == 3


def test_grep_max_files_in_files_mode(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("hit")
    out = at.grep_tool(
        {
            "pattern": "hit",
            "path": str(tmp_path),
            "output_mode": "files_with_matches",
            "max_files": 2,
        },
        cwd=str(tmp_path),
    )
    assert len(out.splitlines()) == 2


# ---------- verify_change additional ----------


def test_verify_change_http_get(monkeypatch, tmp_path):
    class FakeResp:
        def getcode(self):
            return 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(at, "_truncate", lambda s, **k: s)
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())
    out = at.verify_change({"http_get": ["http://example.com"]}, cwd=str(tmp_path))
    assert "OK http" in out and out.startswith("VERIFY=OK")


def test_verify_change_http_error(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("network down")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    out = at.verify_change({"http_get": ["http://example.com"]}, cwd=str(tmp_path))
    assert "VERIFY=FAIL" in out and "FAIL http" in out


def test_verify_change_http_4xx(monkeypatch, tmp_path):
    class FakeResp:
        def getcode(self):
            return 404

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())
    out = at.verify_change({"http_get": ["http://x"]}, cwd=str(tmp_path))
    assert out.startswith("VERIFY=FAIL")


def test_verify_change_absent_found_fails(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("oops FORBIDDEN here")
    out = at.verify_change(
        {"absent_in": [{"path": str(f), "pattern": "FORBIDDEN"}]}, cwd=str(tmp_path)
    )
    assert "VERIFY=FAIL" in out


def test_verify_change_absent_missing_file(tmp_path):
    out = at.verify_change(
        {"absent_in": [{"path": str(tmp_path / "nope"), "pattern": "x"}]},
        cwd=str(tmp_path),
    )
    assert "VERIFY=FAIL" in out


def test_verify_change_present_missing_file(tmp_path):
    out = at.verify_change(
        {"present_in": [{"path": str(tmp_path / "nope"), "pattern": "x"}]},
        cwd=str(tmp_path),
    )
    assert "VERIFY=FAIL" in out


# ---------- review_changes ----------


def test_review_changes_no_diff_returns_ok(tmp_path):
    out = at.review_changes({"diff": ""}, cwd=str(tmp_path))
    assert "no changes" in out


def test_review_changes_no_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("KIRO_API_KEY", "")
    out = at.review_changes({"diff": "--- a\n+++ b\n@@\n+x"}, cwd=str(tmp_path))
    assert "critic disabled" in out


def test_review_changes_with_critic(monkeypatch, tmp_path):
    monkeypatch.setenv("KIRO_API_KEY", "fake")

    async def fake_review(api_key, diff, intent="", model=None):
        return {"verdict": "BLOCK", "reason": "bug found", "issues": ["i1", "i2"]}

    import agent_critic

    monkeypatch.setattr(agent_critic, "review_diff", fake_review)
    out = at.review_changes(
        {"diff": "--- a\n+++ b\n@@\n+bug", "intent": "fix"}, cwd=str(tmp_path)
    )
    assert "REVIEW=BLOCK" in out
    assert "i1" in out and "i2" in out


def test_review_changes_critic_error(monkeypatch, tmp_path):
    monkeypatch.setenv("KIRO_API_KEY", "fake")

    async def boom(*a, **k):
        raise RuntimeError("critic blew up")

    import agent_critic

    monkeypatch.setattr(agent_critic, "review_diff", boom)
    out = at.review_changes({"diff": "+x"}, cwd=str(tmp_path))
    assert "critic-error" in out


def test_review_changes_reads_git_diff(monkeypatch, tmp_path):
    # If no diff is supplied, falls back to `git diff <ref>`.
    class FakeProc:
        stdout = ""
        stderr = ""

    monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: FakeProc())
    out = at.review_changes({}, cwd=str(tmp_path))
    assert "no changes" in out


# ---------- memory tools ----------


def test_memory_search_empty_query():
    with pytest.raises(ValueError, match="query is required"):
        at.memory_search({}, cwd=".")


def test_memory_search_no_hits(monkeypatch):
    import agent_memory

    monkeypatch.setattr(agent_memory.memory, "search", lambda q, k=5: [])
    out = at.memory_search({"query": "missing-term"}, cwd=".")
    assert "no hits" in out


def test_memory_search_with_hits(monkeypatch):
    import agent_memory

    hit = {
        "file": "JOURNAL.md",
        "start_line": 10,
        "end_line": 20,
        "heading": "X",
        "score": 0.5,
        "snippet": "text",
    }
    monkeypatch.setattr(agent_memory.memory, "search", lambda q, k=5: [hit])
    out = at.memory_search({"query": "x", "k": 1}, cwd=".")
    assert "JOURNAL.md" in out and "text" in out


def test_memory_add_empty_text():
    with pytest.raises(ValueError, match="text is required"):
        at.memory_add({"text": ""}, cwd=".")


def test_memory_add_success(monkeypatch):
    import agent_memory

    info = {"file": "f.md", "bytes": 12, "lines": 2}
    monkeypatch.setattr(agent_memory.memory, "add", lambda text, file=None: info)
    out = at.memory_add({"text": "note"}, cwd=".")
    assert "file=f.md" in out and "bytes=12" in out


# ---------- coverage_status ----------


def test_coverage_status_no_data(monkeypatch):
    import agent_coverage

    monkeypatch.setattr(agent_coverage, "status", lambda: {"ok": False, "error": "no coverage.json"})
    out = at.coverage_status({}, cwd=".")
    assert "COVERAGE ERROR" in out and "no coverage.json" in out


def test_coverage_status_summary(monkeypatch):
    import agent_coverage

    monkeypatch.setattr(
        agent_coverage,
        "status",
        lambda: {
            "ok": True,
            "total_percent": 60.5,
            "total_covered": 1200,
            "total_statements": 2000,
            "age_seconds": 30,
            "files": [
                {"percent": 10.0, "missing": 90, "path": "a.py"},
                {"percent": 80.0, "missing": 20, "path": "b.py"},
            ],
        },
    )
    out = at.coverage_status({"limit": 1}, cwd=".")
    assert "COVERAGE total=60.5%" in out
    assert "a.py" in out
    assert "1 more" in out


def test_coverage_status_file_path(monkeypatch):
    import agent_coverage

    monkeypatch.setattr(
        agent_coverage,
        "file_detail",
        lambda p: {
            "ok": True,
            "summary": {"percent": 50.0, "covered": 5, "statements": 10, "missing": 5},
            "missing_lines": [1, 2, 3, 7, 9, 10, 11],
        },
    )
    out = at.coverage_status({"path": "foo.py"}, cwd=".")
    assert "foo.py" in out and "50.0%" in out
    assert "1-3" in out and "9-11" in out


def test_coverage_status_file_error(monkeypatch):
    import agent_coverage

    monkeypatch.setattr(
        agent_coverage, "file_detail", lambda p: {"ok": False, "error": "unknown file"}
    )
    out = at.coverage_status({"path": "foo.py"}, cwd=".")
    assert "COVERAGE ERROR" in out


# ---------- _line_ranges ----------


def test_line_ranges_empty():
    assert at._line_ranges([]) == "(none)"


def test_line_ranges_single():
    assert at._line_ranges([5]) == "5"


def test_line_ranges_complex():
    assert at._line_ranges([1, 2, 3, 7, 9, 10, 11, 20]) == "1-3,7,9-11,20"


# ---------- load_skill_tool ----------


def test_load_skill_empty_name():
    with pytest.raises(ValueError, match="name is required"):
        at.load_skill_tool({}, cwd=".")


def test_load_skill_unknown(monkeypatch):
    import agent_skills

    monkeypatch.setattr(agent_skills, "load_skill", lambda n: None)
    monkeypatch.setattr(agent_skills, "list_skills", lambda: [{"name": "x"}])
    with pytest.raises(ValueError, match="unknown skill"):
        at.load_skill_tool({"name": "zzz"}, cwd=".")


def test_load_skill_success(monkeypatch):
    import agent_skills

    monkeypatch.setattr(agent_skills, "load_skill", lambda n: "BODY")
    out = at.load_skill_tool({"name": "x"}, cwd=".")
    assert out == "BODY"


# ---------- run_tool ----------


def test_run_tool_unsupported_browser():
    status, text, _ = at.run_tool("browser_navigate", {"url": "x"}, cwd=".")
    assert status == "error" and "KIRA_SANDBOX" in text


def test_run_tool_tuple_return(monkeypatch):
    # Replace a tool with one returning (text, images).
    def returns_tuple(args, cwd):
        return ("ok-text", [{"format": "png", "source": {"bytes": "YQ=="}}])

    monkeypatch.setitem(at.TOOLS, "execute_bash", returns_tuple)
    status, text, imgs = at.run_tool("execute_bash", {}, cwd=".")
    assert status == "success" and text == "ok-text"
    assert imgs and imgs[0]["format"] == "png"


def test_run_tool_exception_to_error(monkeypatch):
    def boom(args, cwd):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(at.TOOLS, "execute_bash", boom)
    status, text, _ = at.run_tool("execute_bash", {}, cwd=".")
    assert status == "error" and "kaboom" in text


# ---------- execute_bash stderr branch ----------


def test_execute_bash_stderr_appended(tmp_path):
    out = at.execute_bash({"command": "echo hi; echo err 1>&2"}, cwd=str(tmp_path))
    assert "hi" in out and "--- stderr ---" in out and "err" in out


def test_execute_bash_working_dir(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    out = at.execute_bash({"command": "pwd", "working_dir": str(sub)}, cwd=str(tmp_path))
    assert str(sub) in out
