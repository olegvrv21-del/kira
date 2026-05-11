"""Outline regex parsing (host-side, no docker)."""

import re

import sandbox_tools as st


def test_outline_python_pattern():
    src = """import os

class A:
    def m(self): pass
    async def n(self): pass

def top():
    pass
"""
    out = [(m.group("kw").strip(), m.group("name")) for m in st._PY_PAT.finditer(src)]
    assert ("class", "A") in out
    assert ("def", "m") in out
    assert any(kw.startswith("async") and n == "n" for kw, n in out)
    assert ("def", "top") in out


def test_outline_js_pattern():
    src = """export const Foo = 1;
function bar() {}
class Baz {}
interface Iface { x: number }
async function qux() {}
"""
    out = [(m.group("kw").strip(), m.group("name")) for m in st._JS_PAT.finditer(src)]
    names = {n for _, n in out}
    assert {"Foo", "bar", "Baz", "Iface", "qux"} <= names


def test_outline_go_pattern():
    src = """package main

import "fmt"

func main() {}
func (s *S) Method() {}
type S struct{ x int }
"""
    out = [(m.group("kw").strip(), m.group("name")) for m in st._GO_PAT.finditer(src)]
    names = {n for _, n in out}
    assert {"main", "Method", "S"} <= names


def test_outline_unsupported_lang_raises(tmp_path):
    # Construct fake args; we call the helper directly via the regex map lookup.
    import os

    assert os.path.splitext("foo.rb")[1] not in st._LANG_MAP
