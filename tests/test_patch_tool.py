"""Patch tool semantics: we replicate the algorithm in-process,
so we don't need docker. Host-side fs_write was tested elsewhere; here we
focus on the clipboard/reindent/operations matrix via the function as if it
rolled host paths."""

import importlib
from pathlib import Path

# Reuse sandbox_tools._reindent purely (no docker needed).
import sandbox_tools


def test_reindent_strip_and_add():
    src = "  a\n  b\n\n  c\n"
    out = sandbox_tools._reindent(src, strip="  ", add="    ")
    assert out == "    a\n    b\n\n    c\n"


def test_reindent_no_strip():
    out = sandbox_tools._reindent("a\nb\n", strip="", add=">>")
    assert out == ">>a\n>>b\n"
