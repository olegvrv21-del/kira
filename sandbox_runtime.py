"""Per-session docker sandbox manager.

Each agent session gets a long-lived container `kira-sb-<sid>` with the user
workspace mounted at /workspace. Tool calls translate to `docker exec` inside.
Containers auto-clean by setting an idle timeout via a background reaper.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

IMAGE = os.environ.get("KIRA_SANDBOX_IMAGE", "kira-sandbox:latest")
# When set, the host's ~/webchat is bind-mounted at /host/webchat (read-write)
# inside every agent container, enabling "self-edit" mode.
SELF_EDIT = os.environ.get("KIRA_SELF_EDIT", "1") not in ("", "0", "false", "False")
WEBCHAT_HOST_DIR = Path(__file__).parent.resolve()
# ~/notebook bind-mounted at /host/notebook for long-term memory.
_default_notebook = (Path.home() / "notebook").resolve()
NOTEBOOK_HOST_DIR = Path(os.environ.get("KIRA_NOTEBOOK_DIR", str(_default_notebook)))
MEM = os.environ.get("KIRA_SANDBOX_MEM", "512m")
CPUS = os.environ.get("KIRA_SANDBOX_CPUS", "1.0")
NETWORK = os.environ.get("KIRA_SANDBOX_NETWORK", "bridge")  # set 'none' to disable
WORKSPACES_HOST = Path(__file__).parent / "workspaces"
IDLE_SECONDS = 30 * 60  # reap containers idle for 30 min
EXEC_TIMEOUT = 300

_LAST_USED: dict[str, float] = {}
_LOCK = threading.Lock()


def _name(sid: str) -> str:
    return f"kira-sb-{sid}"


def _container_running(name: str) -> bool:
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def ensure_container(sid: str) -> str:
    """Start (or resume) a container for the session; returns its name."""
    name = _name(sid)
    with _LOCK:
        _LAST_USED[sid] = time.time()
        if _container_running(name):
            return name
        # Remove stale stopped one if any
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
        host_ws = (WORKSPACES_HOST / sid).resolve()
        host_ws.mkdir(parents=True, exist_ok=True)
        try:
            os.chown(host_ws, 1000, 1000)
        except PermissionError:
            pass
        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--rm",
            "--memory",
            MEM,
            "--cpus",
            CPUS,
            "--network",
            NETWORK,
            "--pids-limit",
            "512",
            "-v",
            f"{host_ws}:/workspace",
        ]
        if SELF_EDIT:
            # Mount webchat sources read-write so the agent can edit itself.
            cmd += [
                "-v",
                f"{WEBCHAT_HOST_DIR}:/host/webchat:rw",
                # Add a route to reach the host's webchat HTTP service for the
                # /admin/restart call. host-gateway works on docker>=20.10.
                "--add-host",
                "host.docker.internal:host-gateway",
            ]
        if NOTEBOOK_HOST_DIR.exists():
            cmd += ["-v", f"{NOTEBOOK_HOST_DIR}:/host/notebook:rw"]
        cmd += [
            "-w",
            "/workspace",
            IMAGE,  # ENTRYPOINT/CMD starts the browser daemon
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"docker run failed: {r.stderr.strip()}")
        # Browser daemon is started by container ENTRYPOINT (entrypoint.sh).
        # Wait briefly for daemon to come up so first call doesn't race
        for _ in range(20):
            time.sleep(0.5)
            probe = subprocess.run(
                ["docker", "exec", name, "curl", "-sS", "-f", "http://127.0.0.1:9000/healthz"],
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0:
                break
        return name


def stop_container(sid: str) -> None:
    with _LOCK:
        _LAST_USED.pop(sid, None)
    subprocess.run(["docker", "rm", "-f", _name(sid)], capture_output=True, text=True)


def _reaper() -> None:
    while True:
        time.sleep(60)
        now = time.time()
        stale = [sid for sid, ts in list(_LAST_USED.items()) if now - ts > IDLE_SECONDS]
        for sid in stale:
            stop_container(sid)


threading.Thread(target=_reaper, daemon=True).start()


# ---------- helpers used by tool implementations ----------


def _to_container_path(host_path: str, sid: str) -> str:
    """Translate a host path under workspaces/<sid>/... to /workspace/...; pass through other absolute paths only if under workspace."""
    p = Path(host_path).resolve()
    base = (WORKSPACES_HOST / sid).resolve()
    try:
        rel = p.relative_to(base)
        return "/workspace/" + rel.as_posix() if rel.as_posix() != "." else "/workspace"
    except ValueError:
        # path not under session workspace: keep as-is (will likely fail in container)
        return host_path


def exec_bash(
    sid: str, command: str, working_dir: str | None = None, timeout: int = EXEC_TIMEOUT
) -> tuple[int, str, str]:
    name = ensure_container(sid)
    _LAST_USED[sid] = time.time()
    wd = "/workspace"
    if working_dir:
        wd = _to_container_path(working_dir, sid)
    full = f"cd {shlex.quote(wd)} && {command}"
    r = subprocess.run(
        ["docker", "exec", name, "bash", "-lc", full],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return r.returncode, r.stdout, r.stderr


def exec_argv(sid: str, argv: list[str], cwd: str = "/workspace", timeout: int = EXEC_TIMEOUT) -> tuple[int, str, str]:
    name = ensure_container(sid)
    _LAST_USED[sid] = time.time()
    r = subprocess.run(
        ["docker", "exec", "-w", cwd, name, *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return r.returncode, r.stdout, r.stderr


def read_file(sid: str, path_in_container: str) -> str:
    name = ensure_container(sid)
    r = subprocess.run(["docker", "exec", name, "cat", path_in_container], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise FileNotFoundError(r.stderr.strip() or path_in_container)
    return r.stdout


def lsp_call(sid: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
    """POST/GET to the in-container LSP daemon at 127.0.0.1:9001."""
    import json as _json

    name = ensure_container(sid)
    _LAST_USED[sid] = time.time()
    url = f"http://127.0.0.1:9001{path}"
    if body is None:
        cmd = ["docker", "exec", name, "curl", "-sS", "-X", "POST", url]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    else:
        cmd = [
            "docker",
            "exec",
            "-i",
            name,
            "curl",
            "-sS",
            "-H",
            "Content-Type: application/json",
            "-X",
            "POST",
            "--data-binary",
            "@-",
            url,
        ]
        r = subprocess.run(cmd, input=_json.dumps(body), capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"lsp daemon call failed: {r.stderr.strip()}")
    try:
        return _json.loads(r.stdout)
    except Exception as e:
        raise RuntimeError(f"lsp daemon non-JSON reply: {r.stdout[:300]} ({e})")


def browser_call(sid: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
    """POST/GET to the in-container browser daemon at 127.0.0.1:9000."""
    import json as _json

    name = ensure_container(sid)
    _LAST_USED[sid] = time.time()
    url = f"http://127.0.0.1:9000{path}"
    if body is None:
        cmd = ["docker", "exec", name, "curl", "-sS", "-X", "POST", url]
    else:
        cmd = [
            "docker",
            "exec",
            "-i",
            name,
            "curl",
            "-sS",
            "-H",
            "Content-Type: application/json",
            "-X",
            "POST",
            "--data-binary",
            "@-",
            url,
        ]
    if body is None:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    else:
        r = subprocess.run(cmd, input=_json.dumps(body), capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"browser daemon call failed: {r.stderr.strip()}")
    try:
        return _json.loads(r.stdout)
    except Exception as e:
        raise RuntimeError(f"browser daemon non-JSON reply: {r.stdout[:200]} ({e})")


def write_file(sid: str, path_in_container: str, content: str) -> None:
    name = ensure_container(sid)
    # ensure parent dir
    parent = os.path.dirname(path_in_container) or "/workspace"
    subprocess.run(["docker", "exec", name, "mkdir", "-p", parent], capture_output=True, text=True)
    p = subprocess.Popen(
        ["docker", "exec", "-i", name, "sh", "-c", f"cat > {shlex.quote(path_in_container)}"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _, err = p.communicate(content.encode("utf-8"), timeout=60)
    if p.returncode != 0:
        raise OSError(err.decode("utf-8", "replace"))
