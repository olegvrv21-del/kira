# Changelog

All notable changes to **Кира** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Adheres to [SemVer](https://semver.org/).

---

## [0.3.0] — 2026-05-12 — «Provider-agnostic, multi-user, push-to-prod»

Large architectural session that closed five of the six items on the
ARCHITECTURE.md roadmap.

### Added
- **`llm/` provider abstraction layer** (see `llm/README.md`)
  - `base.py`: canonical `Message`, `ToolCall`, `ToolSpec`, `StreamEvent`,
    `Usage`, `LLMProvider` protocol.
  - `q_provider.py`: Amazon Q adapter. Includes bidirectional Q-dict↔Message[]
    converters (`q_history_to_messages`, `messages_to_q_history`,
    `messages_to_q_body` with `wrap_text` flag). Streams `text`, `tool_call`,
    `metering`, `context_usage`, `message_id`, `usage`, `throttle`, `done`.
  - `mock_provider.py`: deterministic, snapshot-safe.
  - `__init__.get_provider()`: selectable via `KIRA_LLM_PROVIDER`.
- **Multi-user lite**: bearer token → `sha256(token)[:12] = user_id`. All
  session/credit/plan/file/upload endpoints owner-scoped. Legacy NULL-owner
  rows visible to all and claimed on first authed save. New `user_credits`
  table. New columns: `sessions.owner_id` (with index).
- **Telegram bot multimodal**: photo upload (base64 → `/agent images:[]`,
  magic-byte format detection, 8MB cap); pluggable voice transcription
  (`faster-whisper` local or Groq Whisper API, opt-in via `KIRA_TG_WHISPER`).
- **Telegram bot UX**: markdown `parse_mode` with graceful fallback to plain
  on 400; `split_into_chunks()` for replies >4096 chars (breaks on `\n\n` >
  `\n` > space, balances code fences across chunks).
- **Frontend modularisation (phase 1)**: `static/utils.js` (pct, fmtSize,
  `crypto.getRandomValues`-based `makeId`, fileToDataUrl, copyToClipboard,
  downloadFile, safeFilename) + `static/markdown.js` (renderMarkdown +
  DOMPurify wrappers). Native ESM, no bundler.
- **GitHub Actions CD**: `.github/workflows/deploy.yml`. Push to `main` →
  rsync via SSH → `systemctl restart webchat` + `kira-tg-bot` → smoke
  `curl /healthz` over SSH → TG alert on failure. ~50s push-to-prod.
- **`ARCHITECTURE.md`**: bird's-eye repo guide, all ~30 HTTP routes, 38 tools
  categorised, module LOC table, 30-min reading order, roadmap table.
- **`llm/README.md`**: layer diagram, canonical types reference, provider
  protocol, Q-dict converters, how to add a new provider, migration history.
- Tests: +130 across `tests/test_llm_*`, `tests/test_multiuser.py`,
  `tests/test_phase3c2_history.py`, `tests/test_tg_bot.py`.

### Changed
- **`agent_runtime.run_agent`** now operates internally on canonical
  `list[Message]`; the Q-dict `history` is kept in lockstep purely for
  SQLite + `/agent/sessions/{sid}` back-compat. Inline `body =
  {conversationState: …}` construction is gone — single call to
  `messages_to_q_body(… wrap_text=True)`. Runtime no longer speaks Bedrock
  dialect anywhere.
- `_llm_one_shot`, `_run_subagent_silent` already migrated to canonical messages.
- `agent_runtime.py` no longer imports `q_client` directly.
- `agent_store`: `load_history`, `save_session`, `list_sessions`,
  `rename_session`, `delete_session`, `get_session_credits`, `record_credits`
  all accept optional `owner_id`. New `session_owner(sid)` helper. New
  migration adds `owner_id` column + `idx_sessions_owner` index.
- `agent_auth`: middleware stamps `request.state.kira_user_id` on every
  request (works whether auth is on or off; off → `anon`).
- README EN/RU: tool count 29 → 38, test/coverage badges updated to 710/~94%.

### Fixed
- README EN/RU referenced "29 tools" — actual count is 38.
- `agent_store.init`: `CREATE INDEX idx_sessions_owner` was inside
  `executescript()` before the `ALTER TABLE` migration, so existing DBs
  failed with `no such column: owner_id` on first startup. Index now
  created after the column-add guard.
- `static/utils.js::makeId` uses `crypto.getRandomValues` (was `Math.random`,
  CodeQL py/insecure-randomness equivalent).

### Stats
- Tests: 646 → **710** passed, 1 skipped.
- Coverage: 93.5% → **~94%** (`llm/` 91–96% per module).
- LOC added: ~2.5k (mostly tests, docs, `llm/`).

---

## [0.2.0] — 2026-05-12 — «Brand & Observability»

Major session focused on test coverage, observability, mobile UX, and full brand identity.

### Added
- **Visual identity** 🌸
  - Anime-cyberpunk hero portrait (`assets/kira-social.jpg`, 1280×1280)
  - Noir b&w variant for dark mode (`assets/kira-noir.jpg`)
  - Social card 1280×640 for og:image / TG previews
  - Vertical 9:16 splash (`assets/kira-hero.jpg`)
  - Logo mark: glowing eye + sakura (`assets/kira-icon-512.png`)
  - Favicons 16/32/64 + apple-touch-icon 180×180 in `static/`
  - Animated avatar in topbar with orange `#ff8a3d` neon pulse (respects `prefers-reduced-motion`)
  - README hero with `<picture>` light/dark switching, badges, Russian tagline
- **`/agent/health` endpoint**: status (`ok`/`degraded`/`critical`), uptime, in-flight count, key pool state, credit forecast (day/month), 24h tool stats
- **Mobile UX**: `viewport-fit=cover`, safe-area insets, 16px inputs (no iOS zoom), 40px tap targets, `@media (hover: none)` reveals message actions on touch, `100dvh`, table horizontal scroll
- **Streaming tests**: 13 tests with mocked httpx + manually-built AWS event-stream frames
- **Sandbox tool tests**: +37 tests covering edge cases
- **CI**: ripgrep installation with `continue-on-error`, gated by `@skipif(not shutil.which("rg"))`

### Changed
- App startup migrated from deprecated `@app.on_event` to FastAPI lifespan
- All `datetime.utcnow()` → `datetime.now(timezone.utc)` (deprecation fix)
- Critic now reviews the diff that `git_commit` will actually create

### Coverage
- **Total**: 81.6% → **93.5%** (+11.9%)
- **Tests**: 494 → **646** (+152)
- `app.py`: 78.8% → 93.6%
- `agent_runtime.py`: 68.3% → 87.5%
- `sandbox_tools.py`: 21.2% → 94.9%
- `agent_hooks`/`agent_skills`/`agent_coverage`: → 100%

### Fixed
- Auth: auto-prompt for token on 401 and retry
- Critic: properly review staged-but-not-committed diff
- CodeQL: cleaned 5 note-level alerts in test files

---

## [0.1.0] — 2026-05 (initial public release)

### Added
- FastAPI service on port 3000 with SSE chat UI
- 29 tools: shell, FS, grep, browser (Playwright daemon), git, run_tests, lint, skills, plan, subagents
- Per-session Docker sandbox (`kira-sandbox:latest`)
- Long-term memory: SQLite (`agent_sessions.db`) + mounted `~/notebook/`
- Skills system (markdown playbooks loaded on demand)
- Action history with one-click rollback (backups for every `fs_write`/`patch`)
- Plan UI (pending / in_progress / done / skipped checklist)
- Self-edit mode (`KIRA_SELF_EDIT=1`): source tree bind-mounted into sandbox
- Models selector (Opus/Sonnet/Haiku per task)
- Multimodal image input
- Daily backups (systemd timer, 14-day retention)
- Key pool (`agent_keys`) with rotation, ban-lists, daily/monthly limits, forecast
- Optional Bearer auth + per-IP rate limiting
- Semantic memory with embeddings
- Frontend ES modules + settings modal

---

[0.2.0]: https://github.com/olegvrv21-del/kira/compare/0.1.0...main
[0.1.0]: https://github.com/olegvrv21-del/kira/releases/tag/0.1.0
