<p align="center">
  <img src="assets/kira-social.jpg" alt="Кира" width="640" />
</p>

<h1 align="center">🌸 Кира</h1>

<p align="center">
  <i>самомодифицирующийся AI-агент с веб-интерфейсом, sandbox и долговременной памятью</i>
</p>

<p align="center">
  <a href="https://github.com/olegvrv21-del/kira/actions/workflows/ci.yml"><img src="https://github.com/olegvrv21-del/kira/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <img src="https://img.shields.io/badge/coverage-93.5%25-brightgreen" alt="coverage" />
  <img src="https://img.shields.io/badge/tests-646-blue" alt="tests" />
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="python" />
</p>

---

Кира — это полноценный AI-агент, который умеет редактировать собственный
исходный код, гонять тесты, коммитить, ходить в браузер и помнить контекст
между сессиями. Работает как FastAPI-сервис, общается с [Kiro Q](https://kiro.dev/)
по HTTPS и поднимает чат-UI с 29 инструментами в Docker-песочнице.

## Stack

- **Backend**: FastAPI + uvicorn (Python 3.11+)
- **Storage**: SQLite (`agent_sessions.db`) — sessions, actions, session_meta
- **LLM**: Amazon Q API (`q.us-east-1.amazonaws.com`), Bearer ksk token
- **Sandbox**: Docker (`kira-sandbox:latest`, based on Playwright Python image) —
  each session gets its own container with a writable bind-mount of the source tree
- **Frontend**: vanilla HTML/JS, SSE for streaming, plain CSS

## Tools (29)

| Category | Tools |
|---|---|
| Shell / FS | `execute_bash`, `fs_read`, `fs_write`, `glob`, `grep`, `change_dir`, `patch` |
| Code intel | `keyword_search`, `outline`, `verify_change` |
| Browser    | `browser_navigate`, `browser_text`, `browser_eval`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_console_logs`, `browser_network`, `browser_accessibility`, `browser_emulate` |
| Git        | `git`, `git_commit` |
| Build / QA | `run_tests`, `lint` |
| Meta       | `use_subagent`, `load_skill`, `plan`, `llm_one_shot`, `output_iframe` |

## Features

- **Action history & rollback** — every `fs_write` / `patch` stores a backup;
  one click restores any past edit, with inline unified diff.
- **Plan UI** — agent maintains a visible checklist (pending / in_progress / done / skipped).
- **Self-edit** — with `KIRA_SELF_EDIT=1` the source tree is bind-mounted into
  the sandbox at `/host/webchat:rw`, so Кира can modify her own files and commit
  them via `git_commit`.
- **Long-term memory** — notebook directory mounted at `/host/notebook` for
  cross-session notes (`STATUS.md`, `JOURNAL.md`, `TODO.md`, ...).
- **Skills** — markdown playbooks loaded on demand (`browser-automation`,
  `git-workflow`, `testing`, `python-project`, ...).
- **Daily backups** — systemd timer dumps source + DB + notebook to `~/backups/`,
  keeps 14 days.
- **Models selector** — UI lets you pick model tier (Opus / Sonnet / Haiku) per task.
- **Multimodal** — image input via base64 in the request body.

## Layout

```
agent_runtime.py      # SSE chat loop, tool dispatch, action logging
agent_tools.py        # host-mode tools
agent_tool_specs.json # OpenAPI-style tool schema sent to Q
agent_system_prompt.txt
agent_store.py        # SQLite layer
sandbox_runtime.py    # spawns + talks to per-session Docker container
sandbox_tools.py      # sandbox-mode tools (extended browser, git, run_tests, ...)
sandbox/              # Dockerfile, browser_daemon.py, entrypoint.sh
app.py                # FastAPI endpoints, SSE
index.html            # frontend
q_client.py           # Kiro Q API client
ops/                  # systemd units, backup.sh
skills/               # markdown playbooks
tests/                # 52 pytest + 12-check smoke script
```

## Running

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
KIRO_API_KEY=ksk_xxxx .venv/bin/uvicorn app:app --host 0.0.0.0 --port 3000
```

For sandbox mode also set `KIRA_SANDBOX=1` and build the image:

```bash
docker build -t kira-sandbox:latest sandbox/
```

## Tests

```bash
make test    # 52 pytest
make smoke   # 12 live checks against running service
make all     # both
```

## Status

Phases 1–5 + iterations A/B/C complete. All tests green. Next planned:
real LSP integration (pyright + typescript-language-server).
