"""Tests for Iteration C: test-output parser, tool spec inventory."""
import sandbox_tools


def test_parse_pytest_pass():
    text = "=============================== warnings summary ===============================\n46 passed, 4 warnings in 0.56s\n"
    out = sandbox_tools._parse_test_output("pytest", text, 0)
    assert out.startswith("TESTS=PASS")
    assert "passed=46" in out
    assert "duration=0.56s" in out


def test_parse_pytest_fail():
    text = ("FAILED tests/test_a.py::test_x - AssertionError\n"
            "FAILED tests/test_a.py::test_y - ValueError\n"
            "2 failed, 10 passed in 1.23s\n")
    out = sandbox_tools._parse_test_output("pytest", text, 1)
    assert out.startswith("TESTS=FAIL")
    assert "failed=2" in out
    assert "passed=10" in out
    assert "test_x" in out


def test_parse_jest_pass():
    text = "Tests: 3 passed, 3 total\n"
    out = sandbox_tools._parse_test_output("jest", text, 0)
    assert out.startswith("TESTS=PASS")
    assert "runner=jest" in out


def test_parse_go_pass():
    text = "ok  \tgithub.com/foo/bar\t0.123s\nok  \tgithub.com/foo/baz\t0.045s\n"
    out = sandbox_tools._parse_test_output("go", text, 0)
    assert out.startswith("TESTS=PASS")
    assert "ok=2" in out


def test_parse_go_fail():
    text = "ok  \tgithub.com/foo/a\t0.1s\nFAIL\tgithub.com/foo/b\t0.2s\nFAIL\n"
    out = sandbox_tools._parse_test_output("go", text, 1)
    assert out.startswith("TESTS=FAIL")
    assert "github.com/foo/b" in out


def test_tool_spec_inventory_iter_c():
    import json
    from pathlib import Path
    specs = json.loads((Path(__file__).resolve().parent.parent /
                        "agent_tool_specs.json").read_text())
    names = {t["toolSpecification"]["name"] for t in specs}
    for n in ("git", "git_commit", "run_tests", "lint"):
        assert n in names
    # Sanity: total still equals what we expect.
    assert len(specs) >= 29
