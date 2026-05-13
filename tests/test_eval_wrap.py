"""Unit tests for sandbox/_eval_wrap.py — the JS wrapper helper used by
the in-container browser daemon. We test it on the host (no Playwright)
because the logic is pure-Python string munging.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sandbox"))

from _eval_wrap import block_wrap, expression_wrap


def test_expression_wrap_is_a_return_statement():
    out = expression_wrap("document.title")
    assert "return (document.title)" in out
    assert out.startswith("(async () => {")
    assert out.rstrip().endswith(")()")


def test_block_wrap_keeps_explicit_return():
    src = "const x = 1 + 2; return x;"
    out = block_wrap(src)
    assert "const x = 1 + 2" in out
    assert "return x" in out
    # Should NOT double-return.
    assert out.count("return") == 1


def test_block_wrap_inserts_return_on_trailing_expression():
    src = 'const cells = document.querySelectorAll(".cell");\ncells.length'
    out = block_wrap(src)
    assert "return cells.length" in out
    assert "const cells" in out


def test_block_wrap_does_not_return_a_declaration():
    src = "const x = 1;\nlet y = 2;\nconst z = 3;"
    out = block_wrap(src)
    # Last line is a declaration -> no synthetic return injected.
    assert "return const" not in out
    assert "return let" not in out
    assert "return" not in out


def test_block_wrap_with_block_statement():
    src = "if (x > 0) { return x; }"
    out = block_wrap(src)
    # Has an explicit return so nothing extra patched.
    assert out.count("return") == 1


def test_block_wrap_handles_trailing_semicolon_only():
    src = "42;"
    out = block_wrap(src)
    assert "return 42" in out


def test_block_wrap_skips_comment_lines_when_inserting_return():
    src = "const a = 1;\n// trailing comment\na + 1"
    out = block_wrap(src)
    assert "return a + 1" in out
