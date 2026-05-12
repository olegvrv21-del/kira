# Kira — Repository Guide

A map of the codebase so you can navigate without grep-blindness.
Read this once, then jump to the file you actually need.

---

## 1. Bird's-eye view

```
      ┌──────────────────────────────────────────────────────┐
      │  Frontends                                           │
      │   • Web UI         (index.html + static/*.js)        │
      │   • Telegram bot   (ops/tg_bot.py → @kira26ai_bot)    │
      └────────────────────────┬─────────────────────────────┘
                               │ HTTP / SSE
                               ▼
      ┌──────────────────────────────────────────────────────┐
      │  HTTP layer            app.py  (FastAPI, ~30 routes) │
      │  Auth                  agent_auth.py                 │
      └────────────────────────┬─────────────────────────────┘
                               ▼
      ┌──────────────────────────────────────────────────────┐
      │  Agent core            agent_runtime.py              │
      │   ├─ tool dispatch     agent_tools.py                │
      │   ├─ sandbox tools     sandbox_tools.py (38 tools)   │
      │   ├─ subagents         q_client.py                   │
      │   ├─ skills            agent_skills.py + skills/*.md │
      │   ├─ hooks             agent_hooks.py + hooks.json   │
      │   ├─ critic            agent_critic.py               │
      │   ├─ memory (SQLite)   agent_memory.py               │
      │   ├─ session store     agent_store.py                │
      │   └─ coverage          agent_coverage.py             │
      └────────────────────────┬─────────────────────────────┘
                               ▼
              SQLite (agent_sessions.db) + filesystem sandbox/
```

---

## 2. Entry points — where execution starts

| Goal                          | File                          |
|-------------------------------|-------------------------------|
| Run the web server            | `app.py` (uvicorn `app:app`)  |
| Run the Telegram bot          | `ops/tg_bot.py`               |
| Build a Docker image          | `Dockerfile`                  |
| Deploy from scratch           | `install.sh` (docker/venv/systemd) |
| Run tests                     | `pytest` (config in `pyproject.toml`) |
| Smoke-test live prod          | `tests/smoke_live.sh`         |

---

## 3. HTTP API surface (`app.py`)

Grouped by purpose. All `/agent/*` require `Authorization: Bearer $KIRA_AUTH_TOKEN`.

**Public / health**
- `GET /healthz` — liveness
- `GET /` — web UI (HTML)
- `GET /models` — available LLM models
- `GET /skills`, `GET /skills/{name}` — skill listing/content

**Chat & agent**
- `POST /chat` — non-agentic single-turn chat
- `POST /agent` — **main entry point**: SSE stream of `{type:"text"|"tool"|"meta"|"done", ...}`
- `POST /agent/stop/{sid}` — cancel running session
- `POST /agent/reset` — wipe session
- `GET /agent/sessions`, `GET /agent/sessions/{sid}` — list / fetch transcripts
- `POST /agent/sessions/{sid}/rename`
- `GET /agent/file/{sid}/{path}` — read sandbox file
- `POST /agent/upload/{sid}` — upload file into sandbox

**Introspection**
- `GET /agent/health` — full component health (used by `ops/health_alert.sh`)
- `GET /agent/hooks`, `/agent/memory`, `/agent/memory/search`
- `GET /agent/keys`, `POST /agent/keys/reload`
- `GET /agent/metrics`, `/agent/metrics/{sid}`, `/agent/auth_status`, `/agent/limits`
- `GET /agent/plan/{sid}` — current plan tree
- `GET /agent/actions`, `/agent/actions/{aid}`, `POST /agent/actions/{aid}/rollback`
- `GET /agent/coverage`, `/agent/coverage/file`, `POST /agent/coverage/run`
- `GET /usage` — token / cost accounting
- `POST /admin/restart` — graceful self-restart (used by self-edit flow)

---

## 4. The 38 sandbox tools (`sandbox_tools.py`)

Kira's hands. Every tool runs inside `sandbox/` with audit logging.

**Filesystem & search** — `fs_read`, `fs_write`, `patch`, `glob`, `grep`, `keyword_search`, `outline`, `change_dir`

**Code intelligence** — `find_definition`, `find_references`, `rename_symbol`, `diagnostics`, `lint`

**Execution** — `execute_bash`, `run_tests`, `verify_change`, `dev_loop`, `coverage_status`, `review_changes`

**Version control** — `git`, `git_commit`

**Planning & delegation** — `plan`, `use_subagent`, `llm_one_shot`, `load_skill`

**Memory** — `memory_add`, `memory_search`

**Browser automation** (via `browser_daemon.py` + Playwright) — `browser_navigate`, `browser_click`, `browser_type`, `browser_text`, `browser_eval`, `browser_screenshot`, `browser_console_logs`, `browser_network`, `browser_accessibility`, `browser_emulate`, `output_iframe`

Specs live in `agent_tool_specs.json` (OpenAI/Anthropic function-calling shape).

---

## 5. Core modules — what each file owns

| File                       | LOC  | Responsibility                                                  |
|----------------------------|------|-----------------------------------------------------------------|
| `app.py`                   | 1032 | FastAPI routes, SSE streaming, auth, request validation          |
| `agent_runtime.py`         | 1273 | Agent loop, tool-call dispatch, streaming protocol, subagents    |
| `sandbox_tools.py`         | 1430 | Implementation of all 38 tools                                   |
| `agent_tools.py`           |  515 | Tool registry, schema validation, audit log                      |
| `agent_memory.py`          |  364 | Long-term memory: SQLite + optional embeddings (`memory_*`)      |
| `agent_store.py`           |  471 | Session/transcript persistence (`agent_sessions.db`)             |
| `agent_hooks.py`           |  308 | Pre/post-tool hooks, configured in `hooks.json`                  |
| `agent_critic.py`          |   ~  | Self-review pass over generated diffs                            |
| `agent_skills.py`          |   ~  | Loads `skills/*.md` on demand                                    |
| `agent_coverage.py`        |   ~  | pytest-cov integration for self-test                             |
| `agent_auth.py`            |   ~  | Bearer-token middleware                                          |
| `agent_keys.py`            |   ~  | API key rotation / reload                                        |
| `q_client.py`              |   ~  | Subagent / LLM client wrapper                                    |
| `sandbox_runtime.py`       |   ~  | Sandbox path resolution, security checks                         |
| `browser_daemon.py`        |   ~  | Persistent Playwright instance for browser_* tools               |
| `agent_system_prompt.txt`  | 9.5K | The system prompt — read this to understand Kira's "personality" |

---

## 6. Frontend

| File                  | Purpose                                                |
|-----------------------|--------------------------------------------------------|
| `index.html`          | Single-page web UI (chat, models, skills, sessions)    |
| `static/app.js`       | Chat logic, SSE parsing, tool-call rendering           |
| `static/auth.js`      | Token entry / storage                                  |
| `static/i18n.js`      | RU/EN switcher                                         |
| `static/brand-mark.jpg` | Avatar in topbar (orange pulse animation)            |
| `static/og-card.jpg`  | Social preview                                         |
| `static/favicon*.png` + `apple-touch-icon.png` | Icons                          |

---

## 7. Skills (`skills/*.md`)

Markdown playbooks loaded via the `load_skill` tool when relevant:

- `browser-automation.md` · `deploy-port.md` · `exe-dev-vm.md`
- `git-basics.md` · `git-workflow.md` · `python-project.md`
- `subagents.md` · `testing.md` · `webchat-self-edit.md`

Think of skills as "just-in-time documentation" the agent pulls when the
user's task matches the description.

---

## 8. Ops & deployment (`ops/`)

| File                              | Role                                              |
|-----------------------------------|---------------------------------------------------|
| `tg_bot.py`                       | Telegram frontend (long-poll, edits one message)  |
| `kira-tg-bot.service`             | systemd unit for the bot                          |
| `health_alert.sh`                 | Polls `/agent/health`, alerts on state changes    |
| `kira-health-alert.{service,timer}` | Runs the alerter every 5 min                    |
| `backup.sh` + `webchat-backup.*`  | DB / sandbox backup timer                         |

Production lives on `disk-photon.exe.xyz` as `webchat.service`
(port 3000, `WorkingDirectory=/home/exedev/webchat`).

---

## 9. Tests (`tests/`)

646 tests, 93.5% coverage. Naming mirrors modules:
`test_<module>.py` covers `<module>.py`. Highlights:

- `test_app_*.py` — HTTP layer (endpoints, streaming, helpers)
- `test_runtime_*.py` — agent loop, tool dispatch, subagents, streaming
- `test_sandbox_tools*.py` — every tool's happy + error paths
- `test_memory*.py` — SQLite + embedding store
- `test_hooks*.py`, `test_critic.py`, `test_plan.py`, `test_skills_coverage.py`
- `smoke_live.sh` — 19 live-prod assertions (run after deploy)

Gaps still open: `agent_critic` 86.4%, `agent_store` 87.6%, `agent_keys` 89.2%.

---

## 10. Data on disk

```
agent_sessions.db   SQLite — sessions, messages, memory, audit log
sandbox/            agent's working filesystem (per-session subdirs)
assets/             brand artwork (hero, noir, icon, social card)
```

---

## 11. Suggested reading order

If you have 30 minutes and want to grok the whole thing:

1. `README.md` — what & why
2. `agent_system_prompt.txt` — how Kira thinks
3. `app.py` lines 1–300 (setup) + `POST /agent` handler (~line 738)
4. `agent_runtime.py` — find the main agent loop (`async def run` or similar)
5. Pick one tool from `sandbox_tools.py` (e.g. `fs_read` or `patch`) — see the pattern
6. `ops/tg_bot.py` — minimal client, shows the SSE contract clearly
7. `tests/smoke_live.sh` — what "working" looks like end-to-end

---

## 12. Where to ask questions

- Notebook on prod: `~/notebook/JOURNAL.md`, `STATUS.md`, `WEBCHAT.md`, `TODO.md`
- GitHub issues: https://github.com/olegvrv21-del/kira/issues
- Live demo: https://t.me/kira26ai_bot

---

## 13. Known limitations & roadmap

Be honest about what's still rough:

| # | Limitation | Status / plan |
|---|---|---|
| 1 | ~~**LLM vendor lock-in on Amazon Q**~~ | **Done (all phases).** `llm/` abstraction layer (see [`llm/README.md`](llm/README.md)): `base.py` (Message/ToolCall/ToolSpec/StreamEvent/Usage + `LLMProvider` protocol), `q_provider.py` (with bidirectional Q‑dict↔Message[] converters), `mock_provider.py`, `__init__.get_provider()` selects via `KIRA_LLM_PROVIDER`. `_llm_one_shot`, `_run_subagent_silent`, and the main `run_agent` all operate on canonical `list[Message]`; runtime no longer touches Q‑shape. SQLite still stores Q dicts for back‑compat (`agent_session_get` parses them). Adding a new provider = `<vendor>_provider.py` with a converter + StreamEvent parser; runtime stays untouched. Coverage 91.4% on `llm/`. |
| 2 | ~~**Frontend is 1685 LOC of vanilla JS**~~ | **In progress (phase 1 done).** Native ESM, no bundler. Extracted `static/utils.js` (pct/fmtSize/makeId via `crypto.getRandomValues`/fileToDataUrl/copyToClipboard/downloadFile/safeFilename) and `static/markdown.js` (renderMarkdown + DOMPurify wrappers). `app.js` 1685 → 1656 LOC. Remaining ~1656 LOC to split by domain (sessions / SSE / plan / dashboard / tools) over future sessions. |
| 3 | ~~**No multi-user**~~ | **Done (multi-user lite).** Bearer token → `sha256(token)[:12]` = `user_id`; empty token → `anon`. `sessions.owner_id` column + `user_credits(user_id, day)` table. All session/credit/plan/file/upload endpoints owner-scoped; `/agent` rejects foreign sids with 403; in-memory cache key is `(user_id, sid)`. Legacy NULL-owner rows visible to everyone for back-compat and claimed on first authed save. No users table required. |
| 4 | ~~**No CD**~~ | **Done.** `.github/workflows/deploy.yml`: push to `main` → rsync via SSH → `systemctl restart webchat` + `kira-tg-bot` → smoke `curl http://localhost:3000/healthz` over SSH (public URL is OAuth-walled and returns 503 to Actions). TG alert on failure. Repo secrets: `PROD_SSH_KEY`, `PROD_HOST`, `PROD_USER`, `KIRA_URL`, `KIRA_AUTH_TOKEN`. Push-to-prod latency ~50s. |
| 5 | Coverage gaps in `agent_critic` (86%), `agent_store` (88%), `agent_keys` (89%) | Lower priority. |
| 6 | ~~TG bot: no markdown, no chunking, no voice/file input~~ | **Done.** Markdown parse_mode with fallback to plain on 400; `split_into_chunks` (3900-char limit, break on `\n\n` > `\n` > space, balance code fences across chunks). Photo upload → base64 → `/agent images:[]` (magic-byte format detection, 8MB cap). Voice transcription pluggable: `KIRA_TG_WHISPER=faster-whisper` (local CPU tiny ~75MB) or `groq` (Whisper-large-v3-turbo); off by default, transcript echoed in italics before processing. |

See `~/notebook/TODO.md` on the prod VM for the live work queue.
