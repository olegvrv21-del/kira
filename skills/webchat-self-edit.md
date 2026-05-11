---
name: webchat-self-edit
description: Use when the user asks to add, change, or fix features of this web chat (Кира) itself — the UI, the API endpoints, the agent runtime, the skills system. Anything in the codebase that runs THIS service.
---

You are running INSIDE the Кира web chat. The source code is mounted read-write at **`/host/webchat`** in your sandbox.

## Layout (`/host/webchat`)

| File | What it does |
|---|---|
| `app.py` | FastAPI: `/chat`, `/agent`, `/agent/...`, `/skills`, `/usage`, `/admin/restart` |
| `agent_runtime.py` | The agent loop (event-stream parse, tool dispatch, subagents, cancel, cost limits). Builds the system prompt with `_build_system_prompt()` that includes the skills section. |
| `q_client.py` | Streaming HTTP to AWS Q with retry/backoff. |
| `agent_tools.py` | Host-side tool runtime (`run_tool(name, args, cwd)`). |
| `sandbox_tools.py` | Docker-routed runtime (this is what YOU actually use). Same dispatcher. |
| `sandbox_runtime.py` | Container lifecycle (start, exec, reap). |
| `agent_store.py` | SQLite for sessions, history, daily credits. |
| `agent_tool_specs.json` | JSON-Schemas for every tool. **If you add a tool, you must add a spec here AND a handler in both `agent_tools.py` and `sandbox_tools.py`.** |
| `agent_system_prompt.txt` | Base system prompt. The skills section is appended dynamically. |
| `index.html` | The full SPA: drawer, chat list, agent UI, dashboard, dropzone, skills modal. Single file with inline CSS+JS. |
| `skills/*.md` | Skill definitions (frontmatter + body). Just drop a new file here — it auto-appears in the system prompt on next restart. |
| `requirements.txt` | Python deps. If you install a new pip package, also `pip install` it into the host venv via execute_bash. |

Workspaces live in `/host/webchat/workspaces/<sid>/`. **Don't touch other sessions' workspaces.**

## Editing flow

1. Read the relevant file: `fs_read /host/webchat/<file>`.
2. Make a small, focused change with `fs_write` (`str_replace` is safest; `create` overwrites).
3. Validate locally:
   - Python: `execute_bash` → `python3 -c "import ast; ast.parse(open('/host/webchat/<f>.py').read())"`
   - HTML/JS: extract the inline `<script>` and run `node --check`.
4. Restart the service (see below).
5. Verify: `curl http://host.docker.internal:3000/healthz` and the endpoint you changed.

## Restart

The agent CANNOT run `systemctl` directly from the sandbox. Use the auth-token HTTP endpoint:

```bash
TOKEN=$(cat /host/webchat/.restart_token)
curl -sS -X POST "http://host.docker.internal:3000/admin/restart?token=$TOKEN"
```

**Important:** the moment systemd restarts the service, YOUR OWN current request (this SSE stream that you're talking to the user through) will be killed. So treat `/admin/restart` as a fire-and-forget terminal action. **DO NOT** call `curl /healthz` afterwards in the same agent turn — the response will arrive AFTER you've already lost connection, and the user sees `network error`.

Instead, finish your assistant message with something like:
> "Перезапуск запланирован. Через ~2 секунды перезагрузи страницу в браузере и проверь."

The new sessions will reuse the same SQLite history, so the user just continues in the same chat.

If `host.docker.internal` doesn't resolve, fall back to the host gateway: `http://172.17.0.1:3000/`.

## Validate before restart

Never restart with broken syntax — systemd will retry on failure but the user gets stuck. Always validate first:
- Python: `python3 -c "import ast; ast.parse(open('/host/webchat/<f>.py').read())"`
- HTML+JS: extract the inline `<script>` and run `node --check`. Node is available at `node` (playwright-bundled v22).
- JSON: `python3 -c "import json; json.load(open('<f>.json'))"`

## Rules

- **Always back up** before a non-trivial change: `cp /host/webchat/<f> /host/webchat/<f>.bak`.
- **Never** delete `app.py`, `agent_runtime.py`, `index.html`, or `agent_sessions.db`.
- **Never** edit `.restart_token` — it's regenerated on each restart.
- Don't commit secrets. The only secret-ish file is `.restart_token` and the systemd override on the host (not visible here).
- If you add a tool: spec in `agent_tool_specs.json`, handler in BOTH `agent_tools.py` AND `sandbox_tools.py`, then register in the `TOOLS = {}` dict at the bottom of each.
- If you add a skill: just a `.md` file in `/host/webchat/skills/` with valid frontmatter (`name:`, `description:`). Restart needed for the system prompt to pick it up.
- After every change, tell the user the URL of what changed (e.g. "reload https://disk-photon.exe.xyz:3000/").

## Common tasks

- **Add a new endpoint**: edit `app.py`, declare it with `@app.get("/foo")` or `@app.post("/foo")`.
- **Add a button in the UI**: edit the `<body>` section of `index.html`, then wire its `addEventListener` in the inline `<script>`. CSS goes in the `<style>` block.
- **Tweak the agent system prompt**: edit `agent_system_prompt.txt`.
- **Add a Russian/English label**: there are two dictionaries at the top of the `<script>` block in `index.html` keyed by short ids (`I18N.ru` and `I18N.en`). Add the key to both.
- **Change the model list**: edit `_KR_MODELS` / `_Q_MODELS` in `app.py`.
