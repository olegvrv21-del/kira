---
name: autoresearch
description: Karpathy-style autonomous experiment loop. Use overnight or when Oleg says "go improve yourself" — pick one TODO, branch, change, test, measure, keep-or-revert, log to experiments.tsv, repeat. Hard 15-minute budget per experiment.
allowed-tools: [execute_bash, fs_read, fs_write, grep, self_status, propose_improvement, gh_pr_open, prod_observe]
---

# autoresearch — autonomous self-improvement loop

This skill is a port of the pattern from karpathy/autoresearch (May 2026). The idea: take open-ended self-improvement work and turn it into a disciplined experimental loop with one numeric metric, a hard time budget, and a TSV journal.

Use this when:
- Oleg says "поработай ночью" / "let's run autoresearch" / "improve yourself for N hours"
- You have an empty queue and several candidate TODOs in `~/notebook/TODO.md`
- You want to make many small experiments cheaply rather than one big speculative change

Do NOT use this for:
- Bug fixes triggered by Oleg in real-time (those are immediate, not experimental)
- Anything that touches money, auth, or guardrails (those need human review every step, not a loop)

## Setup (once per session)

1. **Agree on a session tag** with Oleg. If Oleg is asleep, use today's date + hour, e.g. `0514-23` for May 14, 23:00 UTC.
2. **Create the session branch off main**: `git checkout main && git pull && git checkout -b kira/auto-<tag>`. If branch already exists, suffix `-2`, `-3`, etc.
3. **Initialise the experiment journal** at `~/notebook/experiments.tsv` if it does not yet exist. Header (tab-separated, NOT comma-separated):
   ```
   ts	branch	sha_before	sha_after	tests_before	tests_after	cov_before	cov_after	lint_before	lint_after	status	pr	description
   ```
4. **Record baseline**: run `self_status` and `execute_bash` for `python -m pytest -q | tail -3` + `python -m ruff check . 2>&1 | tail -1`. Append a `baseline` row to the TSV with status `baseline`.
5. **Confirm setup OK**: one short message to Oleg via the chat (he may be asleep — that's fine, just leave a trace in JOURNAL.md).

## The experiment loop

After setup, run forever until interrupted:

```
LOOP:
  1. Read ~/notebook/TODO.md + tail of ~/notebook/JOURNAL.md (last 200 lines)
  2. Pick ONE small idea. Prefer items already marked as small or low-risk.
     If TODO is empty, call propose_improvement on a recent transcript.
  3. Decide budget: 15 minutes wall clock max for code + test.
  4. Make the change. Single concept. Single file when possible.
  5. git add -A && git commit -m "exp: <one-line description>"
     Record the new SHA.
  6. Run tests: python -m pytest -q --timeout=300 > /tmp/run.log 2>&1
     If grep "^==.* passed" /tmp/run.log returns nothing → status=crash.
  7. Read metrics:
     - tests passed/failed (grep "passed" /tmp/run.log)
     - lint clean? (python -m ruff check . | tail -1)
     - coverage delta if available
  8. Decide:
     - tests went up (more passing) AND lint same-or-better → status=keep, advance
     - tests stayed equal but code is simpler (fewer LOC) → status=keep
     - tests dropped OR new lint errors → status=discard, git reset --hard HEAD~1
     - run failed → status=crash, git reset --hard HEAD~1
  9. Append row to ~/notebook/experiments.tsv
 10. If status=keep and the change is non-trivial (>20 LOC or touches public
     API), open a PR with gh_pr_open. Otherwise stay on the branch and
     accumulate small wins for one batch PR at the end.
 11. Append a 3-line entry to JOURNAL.md: timestamp, what tried, result.
 12. GOTO 1
```

## Metrics — keep it numeric

The only thing that decides keep vs discard is a number. No vibes. Available signals in order of priority:

1. **`tests_after > tests_before`** — strongest signal. New test passing means new behaviour locked in.
2. **`lint_after <= lint_before`** — never regress lint count.
3. **`cov_after >= cov_before`** — coverage non-decreasing.
4. **LOC delta** — fewer lines for equal-or-better metrics is a `keep`. Karpathy's simplicity criterion.
5. **healthz still 200** — `curl -s http://localhost:3000/healthz` after rsync if you pushed to prod.

If metrics conflict (e.g. +1 test, -2 LOC, +1 lint error), discard. Strict mode.

## Time budget

Hard 15 minutes per experiment, including thinking time. If you find yourself debugging the same crash for more than two attempts, mark it `crash`, revert, and move on. Do not fall into rabbit holes overnight.

Total session budget: assume Oleg sleeps 8 hours. At ~15 min/experiment that's ~30 experiments. Plan accordingly — pick a mix of easy wins (test additions, docstrings, small refactors) and one or two interesting risks per session.

## What you CAN edit

Anything in `~/webchat/` that you would normally touch in a PR, with these limits:
- prefer `tests/`, `agent_*.py` helpers, `sandbox_tools.py` documentation, README files
- avoid: `agent_guardrails.py`, `agent_skill_scanner.py`, auth code, anything in `.github/workflows/`

## What you CANNOT do in autoresearch mode

- Direct push to `main` (use PRs only, even on this branch)
- Run `gh pr merge` yourself — Oleg merges in the morning
- Disable guardrails, scanner, or the kill-switch
- Add new pip dependencies
- Modify `.frozen`, `.github/workflows/`, or anything under `~/kira-vault/`
- Send messages to external services beyond what `prod_observe` already does

## Status logging — TSV format

Status values (third-to-last column):
- `baseline` — only the first row, records starting state
- `keep` — metrics improved or stayed equal with simpler code, change retained
- `discard` — metrics regressed, change reverted
- `crash` — pytest crashed or never reported, reverted
- `pr` — change was significant enough to open its own PR (PR number in the `pr` column)
- `skip` — idea evaluated and not worth trying, no commit made

Use 7-char short SHAs. Use `.6f` for floats. Empty string for non-applicable cells.

Example:
```
ts	branch	sha_before	sha_after	tests_before	tests_after	cov_before	cov_after	lint_before	lint_after	status	pr	description
2026-05-14T23:00:00	kira/auto-0514-23	0886eec	0886eec	995	995	93.5	93.5	0	0	baseline		baseline
2026-05-14T23:14:22	kira/auto-0514-23	0886eec	a1b2c3d	995	998	93.5	93.6	0	0	keep		add 3 tests for agent_store.list_sessions edge cases
2026-05-14T23:31:08	kira/auto-0514-23	a1b2c3d	a1b2c3d	998	995	93.6	93.5	0	2	discard		try removing _owner_ok shortcut — broke 3 tests
```

## Reporting in the morning

Before Oleg wakes up (target 06:00 UTC), produce a short summary at the top of JOURNAL.md:

```
## Autoresearch session 2026-05-14 23:00 → 2026-05-15 06:00
- 28 experiments total: 11 keep, 14 discard, 2 crash, 1 pr
- tests: 995 → 1019 (+24)
- coverage: 93.5% → 94.1%
- lint: 0 → 0
- PR opened: #23 (refactor agent_store helpers)
- Most interesting finding: <one-sentence>
- Worth investigating next: <one-sentence>
```

This is the artefact Oleg reads. Make it scannable.

## Stopping conditions

Stop the loop only if:
- 5+ consecutive experiments crashed → infrastructure broke, stop and write a triage note
- `.frozen` file appears → kill-switch tripped, stop immediately
- `~/notebook/STOP_AUTORESEARCH` file exists → polite stop signal from Oleg
- Disk over 90% full → stop and clean up
- Tests baseline drops below 950 → something seriously broke, stop

Otherwise keep going. The whole point is that you do not pause to ask. Oleg will read the journal in the morning.

## Why this skill exists

Karpathy showed that one good `program.md` + one well-scoped change surface + one numeric metric + one TSV is enough for an LLM to do useful autonomous research. Kira's analogue is: this skill + Kira's repo + (tests, coverage, lint) + experiments.tsv. The hard part is not the loop — it is the discipline of writing one number to a TSV after every try. That number is what makes the difference between "agent did stuff overnight" and "agent measurably improved".
