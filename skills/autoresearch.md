---
name: autoresearch
description: Karpathy-style autonomous experiment loop, adapted for Kira's sandbox. Use overnight or when Oleg says "go improve yourself". Pick a TODO, write a PR via gh_pr_open, watch CI, log to ~/notebook/experiments.tsv. CI is the only metric. 15-minute budget per experiment.
allowed-tools: [execute_bash, fs_read, fs_write, grep, glob, self_status, propose_improvement, gh_pr_open, prod_observe]
---

# autoresearch — autonomous self-improvement loop (Kira edition)

Port of the karpathy/autoresearch pattern (May 2026), adapted to Kira's real toolbelt. Karpathy's agent had local `git checkout`, `git reset`, and a 5-min training script. Kira has none of that — she runs inside a Docker sandbox with no access to the host webchat repo. The PR pipeline IS her loop.

Use when:
- Oleg says "поработай ночью" / "let's run autoresearch" / "improve yourself for N hours"
- TODO queue has several small candidate items in `~/notebook/TODO.md`
- You want many small honest experiments, not one big speculative change

Do NOT use for:
- Bug fixes triggered by Oleg in real time
- Anything that touches money, auth, guardrails, .frozen, workflows

## Setup (once per session)

1. **Agree on a session tag** with Oleg. If asleep, use date+hour, e.g. `0514-23`.
2. **Check the experiments journal** at `~/notebook/experiments.tsv`. Create with this header if missing (TAB-separated, NOT comma):
   ```
   ts	tag	idea	pr	status	ci	tests_after	notes
   ```
3. **Read baseline state** via `self_status({})`. Note current SHA, test count, coverage. Save in your head, NOT in TSV (no baseline row — too easy to confuse with experiments).
4. **Skim** `~/notebook/TODO.md` and last 200 lines of `~/notebook/JOURNAL.md` so you know what's already been tried recently.

## What Kira CAN do (only these — that's the whole point of allowed-tools)

| Tool | Purpose |
|---|---|
| `fs_read` / `grep` / `glob` | inspect files in your sandbox copy of the codebase |
| `execute_bash` | run things inside the sandbox (lint, ruff, simple python tests) |
| `self_status` | get current prod SHA, test count, coverage |
| `prod_observe` | watch prod: `git_log` (merges), `journalctl` (runtime errors), **`ci_status` (read CI rollup for any PR you opened)** |
| `gh_pr_open` | the ONLY way to actually change the codebase. files map: {path: full new content} |
| `propose_improvement` | write a markdown note to `~/notebook/proposals/` when an idea needs human judgement before code |
| `fs_write` | edit `~/notebook/experiments.tsv` and your own TODO scratchpads |

## What Kira CANNOT do

- Run `pytest` against the real codebase (sandbox does not have it)
- Run `git checkout -b`, `git commit`, `git reset` — none of these touch host
- Merge PRs (`gh pr merge`) — only Oleg merges
- Push directly to `main` (`gh_pr_open` enforces `kira/*` branch prefix)
- Edit `.github/workflows/`, `.frozen`, anything under `~/kira-vault/`
- Add pip dependencies
- Disable guardrails / scanner / kill-switch

## Honesty rule — READ THE TOOL RESPONSE

After every `gh_pr_open` call, before you do ANYTHING else, do this:

1. Read the FIRST LINE of the tool response.
2. If it starts with `OK pr=N` — PR was created successfully. Note N.
3. If it starts with `ERROR:` — PR creation failed. The reason follows on that same line.
4. Quote that first line in your reply to the user, verbatim. Then proceed.

Do NOT guess what happened. Do NOT invent a diagnosis. The first line of the response is the truth.

This rule exists because in an early drill Kira (you) opened two PRs successfully (#29 and #30, both green), then reported to Oleg "the tool failed, sandbox clone is stale, needs a fix". That was a hallucination — the PRs were real and CI passed on both. The new response format makes this mistake harder: `OK pr=N` is unambiguous.

Same rule for `ci_status`: the first key to check is `rollup`. If `rollup` is missing or the response has `ok: false` then quote the error verbatim — do not paraphrase.

## The experiment loop

Repeat until interrupted:

```
LOOP:
  1. Pick ONE small idea from TODO.md or your last propose_improvement.
     Bias toward: missing tests, documentation, small refactors, low-LOC fixes.
     Skip: anything you cannot defend in one paragraph.

  2. fs_read the file you want to change. Read enough context to be sure.

  3. Plan the change in your head. Single concept. One commit.

  4. Open a PR via gh_pr_open:
       branch="kira/auto-<tag>-N"   (N = sequence number this session)
       title="<imperative one-line>"
       body=<motivation + what changed + risk>
       files={path: full_new_content}

  5. Append a row to ~/notebook/experiments.tsv with status=opened:
       ts<TAB>tag<TAB>idea<TAB>pr_number<TAB>opened<TAB><TAB><TAB><notes>

  6. Wait 60 seconds (sleep via execute_bash), then poll CI:
       prod_observe({what: "ci_status", pr: <PR_NUMBER>})
     This returns rollup: "green" | "red" | "pending" | "mixed" | "none".
     If pending, sleep 30s and poll again. Give up after ~5 minutes total
     polling (status=timeout).

  7. Decide based on the final rollup:
       - rollup == "green" → status="green". Oleg will merge in the morning.
       - rollup == "red"   → status="red". Read failing check name from
         the checks list. Note it in the TSV `notes` column.
       - rollup == "mixed" → status="red" (some checks failed even though
         some passed; treat conservatively).
       - rollup == "none"  → CI did not run yet — keep polling.
       - exceeded ~5min polling → status="timeout".

  8. Update the row in experiments.tsv with the final status.

  9. Append a 3-line entry to JOURNAL.md: timestamp, what tried, result.

 10. GOTO 1
```

**Important**: a green PR is NOT a "keep" decision — only Oleg merges. Your job is to produce honest, well-tested, well-described PRs. Each PR is one numeric outcome (CI green/red).

## Hard time budget

15 minutes per experiment from idea-pick to TSV-write. If picking the idea takes more than 3 minutes, the idea is too big — skip to a simpler one. If CI takes more than 5 minutes, mark `timeout` and move on; do not block the loop on one slow run.

## Metrics — what counts

The numeric signal is the CI rollup returned by `prod_observe({what: "ci_status", pr: N})`:

- `green` — all 6 checks pass (lint, pytest, CodeQL python+js, Analyze, squash-merge)
- `red`   — at least one check failed (FAILURE / CANCELLED / TIMED_OUT / ACTION_REQUIRED)
- `pending` — CI still running; poll again
- `mixed` — odd combination of conclusions; treat as red conservatively
- `none`  — CI has not started yet; poll again
- `timeout` — gave up after ~5 minutes of polling

You see the per-check list inside the same response (`checks[*].name`, `checks[*].conclusion`, `checks[*].url`). When a check is red, that gives you exactly which one (pytest? lint? CodeQL?) so the TSV `notes` column can record useful detail.

You still do NOT have access to `pytest -q` output directly — only the conclusion. Coverage delta and test count delta are not exposed yet; out of scope per experiment.

## TSV format

8 columns, tab-separated:

```
ts	tag	idea	pr	status	ci	tests_after	notes
```

1. `ts` — ISO 8601 UTC, e.g. `2026-05-14T23:14:22Z`
2. `tag` — session tag, e.g. `0514-23`
3. `idea` — short slug, e.g. `add-test-list-sessions-empty`
4. `pr` — PR number (integer), or empty string before the PR is opened
5. `status` — `opened` | `green` | `red` | `timeout` | `aborted`
6. `ci` — same as status when ci has run, otherwise empty
7. `tests_after` — only filled in if the PR merged (rare during a session); usually empty
8. `notes` — one line, no tabs, e.g. `lint failure in line 42 — bad regex escape`

Example:

```
ts	tag	idea	pr	status	ci	tests_after	notes
2026-05-14T23:14:22Z	0514-23	add-test-list-sessions-empty	26	green	green		3 tests added covering empty owner_id
2026-05-14T23:31:08Z	0514-23	rename-confusing-var-in-store	27	red	red		test_store_persistence broke — undid the rename in my head and retried as PR 28
2026-05-14T23:48:55Z	0514-23	docstring-agent-titler	28	green	green		pure docstring change
```

## Stopping conditions

Stop the loop only if:
- `.frozen` file appears (Oleg tripped the kill-switch) → stop immediately
- `~/notebook/STOP_AUTORESEARCH` file exists → polite stop signal from Oleg
- 5 consecutive `red` CI results → something is fundamentally broken in your understanding; write a triage note via `propose_improvement` and stop
- 3 consecutive `timeout` results → CI infrastructure is slow/broken; stop
- Disk shows >90% via `prod_observe({what: "df"})` → stop

Otherwise, do NOT pause to ask "should I continue?". The whole point is autonomy. Oleg reads the morning summary.

## Morning summary

Before 06:00 UTC (or whenever the loop ends), produce one summary block at the TOP of `~/notebook/JOURNAL.md`:

```
## Autoresearch session <tag> — <start_ts> → <end_ts>
- N experiments: G green, R red, T timeout
- PRs opened: #X, #Y, #Z
- Most useful PR (if any): #X — <one-sentence reason>
- Things you tried that did not work: <one-sentence per>
- Worth investigating next: <one-sentence>
```

Make it scannable. Oleg reads this first thing.

## A worked example

Idea: "agent_store.list_sessions has no test for owner_id=None when there are zero sessions."

1. `fs_read` `agent_store.py`, find `list_sessions`. Skim signature.
2. `fs_read` `tests/test_agent_store.py` (or wherever). See test style.
3. Write a new test in your head. 8 lines.
4. `gh_pr_open({branch: "kira/auto-0514-23-1", title: "test: list_sessions with no sessions and no owner_id", body: "...", files: {"tests/test_agent_store_empty.py": "<full content>"}})`.
5. TSV row with status=opened.
6. Sleep 90s, check via `prod_observe` git_log — PR not merged yet (expected).
7. Sleep 60s, then `prod_observe({what: "ci_status", pr: <N>})`. If `rollup == "pending"`, sleep 30s, poll again. After at most ~5 minutes of polling, you will have a final rollup.
8. Final rollup `green` → log `green` in the TSV. Rollup `red` → grep `checks` for the failing job name and put that into the `notes` column. Rollup `timeout` → log `timeout`.
9. Done. GOTO 1.

This is the honest signal — same rollup Oleg sees on the PR page. No tentative "awaiting human merge" guesses. If you see `red`, you know exactly which check broke and can decide whether to open a follow-up PR fixing it (often yes for lint/pytest, sometimes no for CodeQL warnings).

## Why this skill exists

Karpathy showed that one good `program.md` + one numeric metric + one TSV is enough for an LLM to run useful overnight loops. Kira's version is the same shape, with two adjustments forced by sandbox isolation:
- **Single metric becomes CI green/red** instead of `val_bpb` — coarser, but honest
- **Loop interface is `gh_pr_open`** instead of `git commit` — every experiment is a PR Oleg can review

This is intentional. The point is honest, reviewable experiments, not local cleverness. With `ci_status(pr)` (added in PR #27), the loop now has real measurement — green/red is the actual CI signal Oleg sees on the PR page, not a sandbox guess.
