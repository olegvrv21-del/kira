"""agent_tools: fs_write backup, verify_change, dispatcher."""

import os
import tempfile
from pathlib import Path

import agent_tools


def test_fs_write_creates_backup(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    out = agent_tools.fs_write(
        {"command": "str_replace", "path": str(f), "old_str": "hello", "new_str": "world"}, cwd=str(tmp_path)
    )
    assert "[BACKUP=" in out
    bak = out.split("[BACKUP=", 1)[1].split("]", 1)[0]
    assert Path(bak).read_text() == "hello"
    assert f.read_text() == "world"


def test_fs_write_create_no_backup_for_new(tmp_path):
    f = tmp_path / "new.txt"
    out = agent_tools.fs_write({"command": "create", "path": str(f), "file_text": "x"}, cwd=str(tmp_path))
    assert "[BACKUP=" not in out
    assert f.read_text() == "x"


def test_fs_write_create_existing_makes_backup(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("old")
    out = agent_tools.fs_write({"command": "create", "path": str(f), "file_text": "new"}, cwd=str(tmp_path))
    assert "[BACKUP=" in out
    assert f.read_text() == "new"


def test_verify_change_ok(tmp_path):
    py = tmp_path / "ok.py"
    py.write_text("x = 1\n")
    out = agent_tools.verify_change(
        {
            "py_files": [str(py)],
            "present_in": [{"path": str(py), "pattern": "x = 1"}],
            "absent_in": [{"path": str(py), "pattern": "FORBIDDEN"}],
            "shell": ["true"],
        },
        cwd=str(tmp_path),
    )
    assert out.startswith("VERIFY=OK")


def test_verify_change_fail_syntax(tmp_path):
    py = tmp_path / "bad.py"
    py.write_text("def (\n")
    out = agent_tools.verify_change({"py_files": [str(py)]}, cwd=str(tmp_path))
    assert out.startswith("VERIFY=FAIL")


def test_verify_change_fail_present_missing(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("hello")
    out = agent_tools.verify_change({"present_in": [{"path": str(f), "pattern": "nope"}]}, cwd=str(tmp_path))
    assert "VERIFY=FAIL" in out


def test_verify_change_fail_shell(tmp_path):
    out = agent_tools.verify_change({"shell": ["false"]}, cwd=str(tmp_path))
    assert out.startswith("VERIFY=FAIL")


def test_dispatcher_unknown_tool():
    status, text, _ = agent_tools.run_tool("nope", {}, cwd=".")
    assert status == "error" and "unknown tool" in text


def test_dispatcher_success():
    status, text, _ = agent_tools.run_tool("execute_bash", {"command": "echo hi"}, cwd=".")
    assert status == "success" and "hi" in text


def test_dispatcher_error_handling():
    status, text, _ = agent_tools.run_tool(
        "fs_write", {"command": "str_replace", "path": "/nonexistent", "old_str": "a", "new_str": "b"}, cwd="."
    )
    assert status == "error"
