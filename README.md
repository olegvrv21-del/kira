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
  <img src="https://img.shields.io/badge/tests-858-blue" alt="tests" />
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
