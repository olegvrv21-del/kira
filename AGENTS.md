# Working on Kira — notes for agents (human or AI)

Read `ARCHITECTURE.md` first for the map. This file is about *discipline*,
distilled from real mistakes.

## 💸 Spend the cheapest resource that can do the job

Kira talks to paid LLM gateways with **real, exhaustible balances**. A careless
burst of top-tier calls can drain an account in minutes.

- **When testing / probing live models, use the CHEAPEST model available**
  (`gpt-5.4-mini`, `gpt-5.4-nano`, `claude-haiku-*`). **Never** smoke-test with
  `opus` / `gpt-5.6` / `gpt-5.5` — those are for real hard work, not for
  "does the key work?" checks. (This rule exists because an agent once drained
  a whole account probing with `claude-opus`.)
- Prefer `mock` provider (`KIRA_LLM_PROVIDER=mock`) or unit tests over hitting a
  real gateway at all.
- The `agent_frugal` guard enforces a daily cap on expensive-tier calls and
  downgrades gracefully — but don't rely on it as a licence to be wasteful.

## ✅ Verify by evidence, not by assertion

Don't say "it works" until you've seen it work. Prefer a live probe / passing
test over reasoning about what *should* happen. `self_status`, `/agent/health`,
and the test suite exist for exactly this.

## 🗣️ Flag caveats honestly

If something is a partial fix, a dead upstream, a shared wallet, or an
assumption — say so plainly. Half-truths cost more than admitting limits.

## 🔧 Change safely

- Branch, PR, let CI (pytest + ruff + CodeQL) go green, then merge. `main`
  auto-deploys to prod (~50s), so a red merge is a prod incident.
- Keep new source ruff-clean. Add tests for new behaviour.
- Back up prod config before editing (`override.conf.bak.*`).
