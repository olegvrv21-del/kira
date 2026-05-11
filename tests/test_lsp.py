"""Unit tests for the LSP-backed code-intel tool layer (host-side parsers).

We mock sb.lsp_call/sb.read_file/sb.write_file so tests run without Docker.
"""

import pytest

import sandbox_tools as st


class FakeSB:
    def __init__(self, files: dict[str, str], lsp_responses: dict[str, dict]):
        self.files = dict(files)
        self.lsp = dict(lsp_responses)
        self.calls = []
        self.writes = {}

    def _to_container_path(self, p, sid):
        return p if p.startswith("/") else "/workspace/" + p

    def read_file(self, sid, p):
        if p not in self.files:
            raise FileNotFoundError(p)
        return self.files[p]

    def write_file(self, sid, p, content):
        self.writes[p] = content
        self.files[p] = content

    def lsp_call(self, sid, path, body=None, timeout=60):
        self.calls.append((path, body))
        return self.lsp[path]


@pytest.fixture
def fake_sb(monkeypatch):
    fake = FakeSB(files={}, lsp_responses={})
    monkeypatch.setattr(st, "sb", fake)
    # _backup_if_exists is used by rename; bypass it (no real fs)
    monkeypatch.setattr(st, "_backup_if_exists", lambda sid, p: None)
    return fake


def test_find_definition_with_symbol(fake_sb):
    fake_sb.files["/host/webchat/a.py"] = "def foo():\n    pass\n\nfoo()\n"
    fake_sb.lsp["/definition"] = {
        "locations": [
            {"file": "/host/webchat/a.py", "start_line": 0, "start_character": 4, "end_line": 0, "end_character": 7}
        ],
    }
    out = st.find_definition({"file": "/host/webchat/a.py", "symbol": "foo"}, "/host/webchat", "sid")
    assert "DEFINITION" in out
    assert "a.py:1:5" in out
    # symbol search should have located foo at line 0
    body = fake_sb.calls[0][1]
    assert body["line"] == 0 and body["character"] == 4


def test_find_references_explicit_pos(fake_sb):
    fake_sb.files["/host/webchat/a.py"] = "x = 1\n"
    fake_sb.lsp["/references"] = {
        "locations": [
            {"file": "/host/webchat/a.py", "start_line": 0, "start_character": 0, "end_line": 0, "end_character": 1},
            {"file": "/host/webchat/b.py", "start_line": 4, "start_character": 2, "end_line": 4, "end_character": 3},
        ]
    }
    out = st.find_references(
        {"file": "/host/webchat/a.py", "line": 0, "character": 0, "include_declaration": False}, "/host/webchat", "sid"
    )
    assert "REFERENCES (2)" in out
    assert "a.py:1:1" in out and "b.py:5:3" in out
    body = fake_sb.calls[0][1]
    assert body["include_declaration"] is False


def test_diagnostics_clean(fake_sb):
    fake_sb.lsp["/diagnostics"] = {"diagnostics": [], "file": "/host/webchat/a.py"}
    out = st.diagnostics({"file": "/host/webchat/a.py"}, "/host/webchat", "sid")
    assert "DIAGNOSTICS clean" in out


def test_diagnostics_with_errors(fake_sb):
    fake_sb.lsp["/diagnostics"] = {
        "diagnostics": [
            {
                "severity": "error",
                "message": "undefined name x",
                "source": "pyright",
                "code": "reportUndefinedVariable",
                "start_line": 3,
                "start_character": 5,
                "end_line": 3,
                "end_character": 6,
            },
        ]
    }
    out = st.diagnostics({"file": "/host/webchat/a.py"}, "/host/webchat", "sid")
    assert "total=1" in out
    assert "undefined name x" in out
    assert "a.py:4:6" in out


def test_rename_applies_edits(fake_sb):
    src = "def foo():\n    return 1\n\nx = foo()\n"
    fake_sb.files["/host/webchat/a.py"] = src
    fake_sb.lsp["/rename"] = {
        "edits": [
            {
                "file": "/host/webchat/a.py",
                "edits": [
                    {"start_line": 0, "start_character": 4, "end_line": 0, "end_character": 7, "new_text": "bar"},
                    {"start_line": 3, "start_character": 4, "end_line": 3, "end_character": 7, "new_text": "bar"},
                ],
            }
        ],
        "changed_files": 1,
    }
    out = st.rename_symbol(
        {
            "file": "/host/webchat/a.py",
            "line": 0,
            "character": 4,
            "new_name": "bar",
        },
        "/host/webchat",
        "sid",
    )
    assert "RENAME -> bar" in out
    assert "files=1" in out and "edits=2" in out
    written = fake_sb.writes["/host/webchat/a.py"]
    assert "def bar():" in written
    assert "x = bar()" in written
    assert "foo" not in written


def test_rename_preview_only(fake_sb):
    fake_sb.files["/host/webchat/a.py"] = "foo\n"
    fake_sb.lsp["/rename"] = {
        "edits": [
            {
                "file": "/host/webchat/a.py",
                "edits": [
                    {"start_line": 0, "start_character": 0, "end_line": 0, "end_character": 3, "new_text": "bar"},
                ],
            }
        ]
    }
    out = st.rename_symbol(
        {
            "file": "/host/webchat/a.py",
            "line": 0,
            "character": 0,
            "new_name": "bar",
            "apply": False,
        },
        "/host/webchat",
        "sid",
    )
    assert "preview" in out
    assert fake_sb.writes == {}
