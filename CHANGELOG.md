# Changelog

All notable changes to **Кира** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Adheres to [SemVer](https://semver.org/).

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
