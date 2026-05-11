"""Tests for sandbox_runtime.py with subprocess mocked out.

We never actually run docker; we patch subprocess.run / Popen so we exercise
the path-translation logic, container lifecycle decisions, and error paths.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sandbox_runtime as sr


class _R:
    """Fake subprocess.CompletedProcess."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _reset_state():
    sr._LAST_USED.clear()
    yield
    sr._LAST_USED.clear()


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "WORKSPACES_HOST", tmp_path)
    return tmp_path


# ---------- _name / _container_running ----------


def test_name():
    assert sr._name("abc") == "kira-sb-abc"


def test_container_running_true():
    with patch("sandbox_runtime.subprocess.run", return_value=_R(0, "true\n")):
        assert sr._container_running("x") is True


def test_container_running_false_stopped():
    with patch("sandbox_runtime.subprocess.run", return_value=_R(0, "false\n")):
        assert sr._container_running("x") is False


def test_container_running_no_container():
    with patch("sandbox_runtime.subprocess.run", return_value=_R(1, "", "no such container")):
        assert sr._container_running("x") is False


# ---------- _to_container_path ----------


def test_to_container_path_under_workspace(workspaces):
    (workspaces / "sid1").mkdir()
    p = workspaces / "sid1" / "a" / "b.txt"
    p.parent.mkdir()
    p.write_text("x")
    out = sr._to_container_path(str(p), "sid1")
    assert out == "/workspace/a/b.txt"


def test_to_container_path_at_root(workspaces):
    (workspaces / "sid1").mkdir()
    out = sr._to_container_path(str(workspaces / "sid1"), "sid1")
    assert out == "/workspace"


def test_to_container_path_outside(workspaces):
    (workspaces / "sid1").mkdir()
    out = sr._to_container_path("/etc/passwd", "sid1")
    assert out == "/etc/passwd"


# ---------- ensure_container ----------


def test_ensure_container_returns_existing(workspaces):
    """When container is already running, returns its name without spawning."""
    with patch("sandbox_runtime.subprocess.run") as mr:
        mr.return_value = _R(0, "true\n")
        name = sr.ensure_container("sid1")
        assert name == "kira-sb-sid1"
        # only one call: the inspect probe
        assert mr.call_count == 1


def test_ensure_container_starts_new(workspaces, monkeypatch):
    """Container not running -> docker rm -f + docker run + healthz probe."""
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        if argv[0:2] == ["docker", "inspect"]:
            return _R(0, "false\n")  # not running
        if argv[0:3] == ["docker", "rm", "-f"]:
            return _R(0)
        if argv[0:2] == ["docker", "run"]:
            return _R(0, "cid\n")
        if "curl" in argv:
            return _R(0)  # healthz ok immediately
        return _R(0)

    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    monkeypatch.setattr(sr.time, "sleep", lambda _s: None)
    name = sr.ensure_container("sid1")
    assert name == "kira-sb-sid1"
    # Workspace dir created on host
    assert (workspaces / "sid1").is_dir()
    # docker run was invoked
    assert any(a[0:2] == ["docker", "run"] for a in calls)


def test_ensure_container_run_fails_raises(workspaces, monkeypatch):
    def fake_run(argv, **kw):
        if argv[0:2] == ["docker", "inspect"]:
            return _R(1, "", "no such container")
        if argv[0:2] == ["docker", "run"]:
            return _R(1, "", "image not found")
        return _R(0)

    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    monkeypatch.setattr(sr.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError, match="docker run failed"):
        sr.ensure_container("sid_bad")


# ---------- stop_container ----------


def test_stop_container_runs_docker_rm(monkeypatch):
    sr._LAST_USED["sid_x"] = 0.0
    seen = []
    monkeypatch.setattr(
        sr.subprocess,
        "run",
        lambda argv, **kw: seen.append(argv) or _R(0),
    )
    sr.stop_container("sid_x")
    assert "sid_x" not in sr._LAST_USED
    assert seen and seen[0][0:3] == ["docker", "rm", "-f"]


# ---------- exec_bash / exec_argv / read_file / write_file ----------


def _patch_running(monkeypatch):
    """Make ensure_container a no-op returning the expected name."""
    monkeypatch.setattr(sr, "ensure_container", lambda sid: f"kira-sb-{sid}")


def test_exec_bash(monkeypatch):
    _patch_running(monkeypatch)
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return _R(0, "hi\n", "")

    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    rc, out, err = sr.exec_bash("sid", "echo hi")
    assert rc == 0 and out == "hi\n"
    assert captured["argv"][:4] == ["docker", "exec", "kira-sb-sid", "bash"]
    assert "echo hi" in captured["argv"][-1]


def test_exec_bash_uses_working_dir(monkeypatch, workspaces):
    (workspaces / "sid").mkdir()
    (workspaces / "sid" / "sub").mkdir()
    _patch_running(monkeypatch)
    captured = {}
    def _f(a, **k):
        captured["a"] = a
        return _R(0, "", "")
    monkeypatch.setattr(sr.subprocess, "run", _f)
    sr.exec_bash("sid", "ls", working_dir=str(workspaces / "sid" / "sub"))
    assert "cd /workspace/sub" in captured["a"][-1]


def test_exec_argv(monkeypatch):
    _patch_running(monkeypatch)
    monkeypatch.setattr(sr.subprocess, "run", lambda a, **k: _R(0, "out", "err"))
    rc, out, err = sr.exec_argv("sid", ["ls", "-la"], cwd="/workspace/x")
    assert (rc, out, err) == (0, "out", "err")


def test_read_file(monkeypatch):
    _patch_running(monkeypatch)
    monkeypatch.setattr(sr.subprocess, "run", lambda a, **k: _R(0, "file contents"))
    assert sr.read_file("sid", "/workspace/a.txt") == "file contents"


def test_read_file_missing(monkeypatch):
    _patch_running(monkeypatch)
    monkeypatch.setattr(sr.subprocess, "run", lambda a, **k: _R(1, "", "No such file"))
    with pytest.raises(FileNotFoundError):
        sr.read_file("sid", "/nope")


def test_write_file_success(monkeypatch):
    _patch_running(monkeypatch)
    # mkdir call
    monkeypatch.setattr(sr.subprocess, "run", lambda a, **k: _R(0))
    fake = MagicMock()
    fake.communicate.return_value = (b"", b"")
    fake.returncode = 0
    monkeypatch.setattr(sr.subprocess, "Popen", lambda *a, **k: fake)
    sr.write_file("sid", "/workspace/x.txt", "hello")
    fake.communicate.assert_called_once()


def test_write_file_failure(monkeypatch):
    _patch_running(monkeypatch)
    monkeypatch.setattr(sr.subprocess, "run", lambda a, **k: _R(0))
    fake = MagicMock()
    fake.communicate.return_value = (b"", b"permission denied")
    fake.returncode = 1
    monkeypatch.setattr(sr.subprocess, "Popen", lambda *a, **k: fake)
    with pytest.raises(OSError, match="permission denied"):
        sr.write_file("sid", "/etc/x", "hi")


# ---------- lsp_call / browser_call ----------


def test_lsp_call_get(monkeypatch):
    _patch_running(monkeypatch)
    monkeypatch.setattr(sr.subprocess, "run", lambda a, **k: _R(0, '{"ok":true}'))
    assert sr.lsp_call("sid", "/status") == {"ok": True}


def test_lsp_call_post_with_body(monkeypatch):
    _patch_running(monkeypatch)
    captured = {}

    def fake_run(argv, **kw):
        captured["input"] = kw.get("input", "")
        return _R(0, '{"result":1}')

    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    out = sr.lsp_call("sid", "/diag", body={"path": "x.py"})
    assert out == {"result": 1}
    assert "x.py" in captured["input"]


def test_lsp_call_curl_failure(monkeypatch):
    _patch_running(monkeypatch)
    monkeypatch.setattr(sr.subprocess, "run", lambda a, **k: _R(7, "", "connection refused"))
    with pytest.raises(RuntimeError, match="lsp daemon call failed"):
        sr.lsp_call("sid", "/status")


def test_lsp_call_bad_json(monkeypatch):
    _patch_running(monkeypatch)
    monkeypatch.setattr(sr.subprocess, "run", lambda a, **k: _R(0, "not-json"))
    with pytest.raises(RuntimeError, match="non-JSON"):
        sr.lsp_call("sid", "/status")


def test_browser_call_get(monkeypatch):
    _patch_running(monkeypatch)
    monkeypatch.setattr(sr.subprocess, "run", lambda a, **k: _R(0, '{"ok":1}'))
    assert sr.browser_call("sid", "/healthz") == {"ok": 1}


def test_browser_call_post(monkeypatch):
    _patch_running(monkeypatch)
    captured = {}
    def fake_run(argv, **kw):
        captured["input"] = kw.get("input", "")
        return _R(0, '{"navigated":true}')
    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    out = sr.browser_call("sid", "/navigate", body={"url": "https://e.com"})
    assert out == {"navigated": True}
    assert "https://e.com" in captured["input"]


def test_browser_call_failure(monkeypatch):
    _patch_running(monkeypatch)
    monkeypatch.setattr(sr.subprocess, "run", lambda a, **k: _R(7, "", "refused"))
    with pytest.raises(RuntimeError, match="browser daemon call failed"):
        sr.browser_call("sid", "/x")


def test_browser_call_bad_json(monkeypatch):
    _patch_running(monkeypatch)
    monkeypatch.setattr(sr.subprocess, "run", lambda a, **k: _R(0, "<html>"))
    with pytest.raises(RuntimeError, match="non-JSON"):
        sr.browser_call("sid", "/x")
