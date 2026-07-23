"""Tests for agent_toolqa.py — arg validation + semantic status."""
from __future__ import annotations

import pytest

import agent_toolqa as qa


# ---------------- validate_args -------------------------------------------


def test_valid_args_pass():
    assert qa.validate_args("execute_bash", {"command": "ls"}) is None


def test_missing_required():
    err = qa.validate_args("execute_bash", {})
    assert err and "command" in err and "missing" in err.lower()


def test_missing_required_none_value():
    err = qa.validate_args("execute_bash", {"command": None})
    assert err and "command" in err


def test_wrong_type():
    err = qa.validate_args("execute_bash", {"command": 123})
    assert err and "must be string" in err


def test_bool_not_integer():
    # fs_write has insert_line: integer
    err = qa.validate_args("fs_write",
                           {"command": "insert", "path": "/x", "insert_line": True})
    assert err and "insert_line" in err


def test_extra_params_tolerated():
    assert qa.validate_args("execute_bash",
                            {"command": "ls", "totally_unknown": 1}) is None


def test_unknown_tool_allows():
    assert qa.validate_args("no_such_tool", {"whatever": 1}) is None


def test_non_dict_args():
    err = qa.validate_args("execute_bash", ["not", "a", "dict"])
    assert err and "object" in err


def test_enum_violation():
    # fs_read uses operations; construct a synthetic enum check via a known tool
    # that has an enum, else skip gracefully.
    schema = qa._schema_map()
    tool_with_enum = None
    for name, sch in schema.items():
        for k, p in (sch.get("properties") or {}).items():
            if isinstance(p, dict) and p.get("enum"):
                tool_with_enum = (name, k, p["enum"])
                break
        if tool_with_enum:
            break
    if not tool_with_enum:
        pytest.skip("no enum params in specs")
    name, key, enum = tool_with_enum
    # Build minimal valid args + bad enum value.
    sch = schema[name]
    args = {r: "x" for r in (sch.get("required") or [])}
    args[key] = "___invalid_enum_value___"
    err = qa.validate_args(name, args)
    assert err and key in err


# ---------------- semantic_status -----------------------------------------


def test_bash_nonzero_exit_demoted():
    status, reason = qa.semantic_status(
        "execute_bash", "success", "boom\n--- exit 1 ---\n--- cwd /x ---")
    assert status == "error"
    assert "exit 1" in reason


def test_bash_zero_exit_ok():
    status, reason = qa.semantic_status(
        "execute_bash", "success", "hi\n--- exit 0 ---\n--- cwd /x ---")
    assert status == "success"
    assert reason is None


def test_tests_fail_demoted():
    status, reason = qa.semantic_status(
        "run_tests", "success", "TESTS=FAIL runner=pytest rc=1")
    assert status == "error"
    assert "tests failed" in reason.lower()


def test_tests_pass_ok():
    status, reason = qa.semantic_status(
        "run_tests", "success", "TESTS=PASS runner=pytest passed=10")
    assert status == "success"


def test_git_rc_demoted():
    status, reason = qa.semantic_status(
        "git", "success", "nothing to commit\n--- exit 1 ---")
    assert status == "error"


def test_error_never_promoted():
    status, reason = qa.semantic_status(
        "execute_bash", "error", "already failed\n--- exit 0 ---")
    assert status == "error"


def test_non_exit_aware_tool_unchanged():
    # fs_read isn't exit-aware; even with a stray 'exit 1' in file content it
    # must not be demoted.
    status, reason = qa.semantic_status(
        "fs_read", "success", "file contents mentioning --- exit 1 ---")
    assert status == "success"
    assert reason is None
