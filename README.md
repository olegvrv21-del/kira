<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/kira-noir.jpg" />
    <img src="assets/kira-social.jpg" alt="Kira" width="640" />
  </picture>
</p>

<h1 align="center">🌸 Kira</h1>

<p align="center">
  <i>a self-modifying AI agent with a web UI, sandboxed tools, and long-term memory</i>
</p>

<p align="center">
  🇬🇧 English
  &nbsp;·&nbsp;
  <a href="README.ru.md">🇷🇺 Русский</a>
  &nbsp;·&nbsp;
  <a href="ARCHITECTURE.md">🗺️ Architecture guide</a>
</p>

<p align="center">
  <a href="https://github.com/olegvrv21-del/kira/actions/workflows/ci.yml"><img src="https://github.com/olegvrv21-del/kira/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <img src="https://img.shields.io/badge/coverage-94%25-brightgreen" alt="coverage" />
  <img src="https://img.shields.io/badge/tests-823-blue" alt="tests" />
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="python" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license" />
</p>

---

Kira is a full-fledged AI agent that can edit its own source code, run
tests, commit to git, drive a browser, and remember context across
sessions. She runs as a FastAPI service, streams responses over SSE,
and exposes a chat UI with **38 tools** executed inside per-session
Docker sandboxes.

## Why Kira?

|                          | Kira                             | OpenHands / Aider / Open Interpreter |
|--------------------------|----------------------------------|--------------------------------------|
| **Long-term memory**     | ✅ SQLite + notebook              | ❌                                   |
| **Self-modify own code** | ✅ source bind-mounted            | ❌                                   |
| **Tools**                | **38** (shell, FS, browser, git, tests, plan, subagents, skills, memory, code-intel) | ~10–15                |
| **Skills system**        | ✅ markdown playbooks             | ❌                                   |
| **Action history**       | ✅ one-click rollback             | partial                              |
| **Per-session sandbox**  | ✅ Docker, 512MB / 1 CPU          | varies                               |
| **Key pool & cost caps** | ✅ rotation, ban-lists, forecast | ❌                                   |
| **Observability**        | ✅ `/agent/health`                | ❌                                   |
| **Branding & UX**        | RU/EN UI, animated avatar        | bare-bones                           |
| **Tests / Coverage**     | **823 tests, ~94%**              | varies                               |
| **LLM provider abstraction** | ✅ pluggable (Q live; OpenRouter shipped) | partial          |
| **Multi-user (lite)**    | ✅ per-token session isolation    | partial                              |
| **Telegram frontend**    | ✅ markdown + photo + voice       | varies                               |
| **Push-to-prod CD**      | ✅ GitHub Actions, ~50s           | varies                               |

## Screenshots

<table>
<tr>
  <td align="center" width="33%"><img src="assets/screenshots/01-main.png" alt="Chat" /><br/><sub><b>Chat</b> — branded UI, RU/EN, animated avatar</sub></td>
  <td align="center" width="33%"><img src="assets/screenshots/04-models.png" alt="Models" /><br/><sub><b>Models</b> — pick LLM with cost multipliers + task tags</sub></td>
  <td align="center" width="33%"><img src="assets/screenshots/03-skills.png" alt="Skills" /><br/><sub><b>Skills</b> — pluggable playbooks for specific domains</sub></td>
</tr>
</table>

## Stack

- **Backend**: FastAPI + uvicorn (Python 3.11+)
- **Storage**: SQLite (`agent_sessions.db`) — sessions, actions, session metadata
- **LLM**: Amazon Q API (`q.us-east-1.amazonaws.com`) via Bearer `ksk_` token
- **Sandbox**: Docker (`kira-sandbox:latest`, Playwright Python base) —
  one container per session with a writable bind-mount of the source tree
- **Frontend**: vanilla HTML/JS, SSE streaming, plain CSS

## Tools (38)

| Category   | Tools                                                                                                                                                              |
|------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Shell / FS | `execute_bash`, `fs_read`, `fs_write`, `patch`, `glob`, `grep`, `keyword_search`, `change_dir`                                                                     |
| Code intel | `outline`, `find_definition`, `find_references`, `rename_symbol`, `diagnostics`, `lint`                                                                            |
| Execution  | `run_tests`, `verify_change`, `dev_loop`, `coverage_status`, `review_changes`                                                                                      |
| Git        | `git`, `git_commit`                                                                                                                                                |
| Browser    | `browser_navigate`, `browser_click`, `browser_type`, `browser_text`, `browser_eval`, `browser_screenshot`, `browser_console_logs`, `browser_network`, `browser_accessibility`, `browser_emulate`, `output_iframe` |
| Planning   | `plan`, `use_subagent`, `llm_one_shot`, `load_skill`                                                                                                               |
| Memory     | `memory_add`, `memory_search`                                                                                                                                      |

## Features

- **Action history & rollback** — every `fs_write` / `patch` stores a backup;
  one click restores any past edit with an inline unified diff.
- **Plan UI** — the agent maintains a visible checklist (pending / in_progress / done / skipped).
- **Self-edit** — with `KIRA_SELF_EDIT=1` the source tree is bind-mounted
  into the sandbox at `/host/webchat:rw`, so Kira can modify her own files
  and commit them via `git_commit`.
- **Long-term memory** — a notebook directory is mounted at `/host/notebook`
  for cross-session notes (`STATUS.md`, `JOURNAL.md`, `TODO.md`, …).
- **Skills** — markdown playbooks loaded on demand (`browser-automation`,
  `git-workflow`, `testing`, `python-project`, …).
- **Daily backups** — a systemd timer dumps source + DB + notebook to
  `~/backups/`, keeping 14 days of history.
- **Model selector** — the UI lets you pick a model tier (Opus / Sonnet / Haiku) per task.
- **Multimodal** — image input via base64 in the request body.
- **Health endpoint** — `/agent/health` returns status, in-flight count,
  key-pool state, credit forecast, and 24h tool stats.
- **Telegram alerts** — systemd timer polls `/agent/health` every 5 min
  and pings you on status transitions (see `ops/`).
- **Telegram bot frontend** (`ops/tg_bot.py`) — chat with Kira from TG with
  markdown rendering, automatic chunking of replies >4096 chars (code-fence
  safe), photo uploads (base64 → `/agent images:[]`), and pluggable voice
  transcription (`faster-whisper` local or Groq Whisper API).
- **Multi-user (lite)** — bearer token → `sha256(token)[:12] = user_id`; every
  session, plan, credit counter, uploaded file, and `/agent` call is scoped by
  owner. Legacy NULL-owner rows stay visible until first authed save claims them.
- **LLM provider abstraction** (`llm/`) — see [`llm/README.md`](llm/README.md).
  `base.py` (Message / ToolCall / StreamEvent / `LLMProvider` protocol) +
  `q_provider.py` (with bidirectional Q-dict↔Message[] converters) +
  `mock_provider.py` + `openrouter_provider.py` (BYOK: 100+ models via
  OpenRouter), selectable via `KIRA_LLM_PROVIDER`. The entire runtime
  (`run_agent`, `_llm_one_shot`, `_run_subagent_silent`) operates on canonical
  messages — vendor lock-in is broken. Adding a new provider = drop in
  `<vendor>_provider.py`, no runtime changes.
- **Off-VM disaster recovery** — private `kira-vault` repo with `git-crypt`
  encrypts all configs/secrets/notebook; daily systemd timer pushes;
  `RESTORE.md` documents bare-metal recovery.
- **TG multi-user whitelist** — `KIRA_TG_ALLOWED_USERS` gates derived
  bearer tokens (one per TG user id), so the bot can be safely public.
- **Push-to-prod CD** — `.github/workflows/deploy.yml`: push to `main` → rsync
  over SSH → `systemctl restart webchat` + `kira-tg-bot` → smoke `/healthz`
  → TG alert on failure. ~50s latency, fully automated.

## Layout

```
agent_runtime.py      # SSE chat loop, tool dispatch, action logging
agent_tools.py        # host-mode tools
agent_tool_specs.json # OpenAPI-style tool schema sent to Q
agent_system_prompt.txt
agent_store.py        # SQLite layer
sandbox_runtime.py    # spawns + talks to per-session Docker container
sandbox_tools.py      # sandbox-mode tools (extended browser, git, run_tests, …)
sandbox/              # Dockerfile, browser_daemon.py, entrypoint.sh
app.py                # FastAPI endpoints, SSE
index.html            # frontend
q_client.py           # Kiro Q API client
ops/                  # systemd units, backup.sh, health_alert.sh
skills/               # markdown playbooks
tests/                # pytest + smoke script
```

## Quick start

### 🐳 Docker (recommended)

```bash
git clone https://github.com/olegvrv21-del/kira.git && cd kira
./install.sh docker          # builds image, generates auth token, starts
```

→ open http://localhost:3000/. The auth token is in `.env` (`KIRA_AUTH_TOKEN`).

### 🐍 Venv (without Docker)

```bash
./install.sh venv
source .env && .venv/bin/uvicorn app:app --host 0.0.0.0 --port 3000
```

### 🚀 Systemd (production)

```bash
./install.sh systemd         # venv mode + registers kira.service
systemctl status kira
```

### Manual

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
KIRO_API_KEY=ksk_xxxx .venv/bin/uvicorn app:app --host 0.0.0.0 --port 3000
```

For full sandbox isolation per session also set `KIRA_SANDBOX=1` and build the sandbox image:

```bash
docker build -t kira-sandbox:latest sandbox/
```

See `.env.example` — every environment variable is documented there.

## Tests

```bash
make test    # pytest suite (823 tests, ~94% coverage)
make smoke   # 19 live HTTP checks against running service
make all     # both
```

## Status

Production-grade. **823 tests** at ~94% coverage (critic/keys/store all >97%),
CI green, push-to-prod CD active, deployed on `disk-photon.exe.xyz`. LLM
abstraction complete with **two providers shipped** (Q live, OpenRouter ready);
multi-user lite + TG whitelist live; Telegram frontend supports markdown + image +
voice; off-VM disaster recovery via encrypted `kira-vault`. Frontend split through
phase 3 (`app.js` 1685 → 1174 LOC across 9 ES modules). Next: real LSP
integration (pyright + typescript-language-server) and frontend phase 4.

## Documentation

- [CHANGELOG.md](CHANGELOG.md) — release history (current: **0.4.0** — Resilience & Coverage)
- [LICENSE](LICENSE) — MIT

## License

MIT © 2026 Oleg Vorobiev. See [LICENSE](LICENSE).
