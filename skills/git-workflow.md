---
name: git-workflow
description: Use when making code changes in a git repo. Defines the commit-per-plan-item discipline, message conventions, and recovery workflows.
---

# Git workflow for Kira

## Per plan item
1. Mark plan item `in_progress`.
2. Make the change with `patch` or `fs_write`.
3. `lint(paths=[...])` on edited files.
4. `verify_change` (with relevant py_files / shell / present_in).
5. `git status` — review what changed.
6. `git_commit(message="<scope>: <imperative one-liner>")`.
7. Mark plan item `done`.

If the plan item is too big for a single commit, break it into sub-steps with `plan op=add` and commit each sub-step.

## Message conventions
- `feat(agent): add llm_one_shot tool`
- `fix: rollback should not delete pre-rollback backup`
- `refactor: extract _maybe_diff into helper`
- `test: add cases for plan.add with custom status`
- `docs(notebook): record Iteration B`
- `chore(deps): bump pytest to 9.0.3`
- `ops: add daily backup timer`

One line. Imperative. Lowercase scope. ≤70 chars in the subject. Body optional.

## Recovery
- Lost the last change? `git diff` to confirm; `git stash` if needed.
- Want to undo last commit but keep changes: `git reset --soft HEAD~1`.
- Want to discard local edits in a file: `git restore <file>`.
- Want to re-stage a specific subset: `git restore --staged <file>` then `git add <hunks>`.

## Don'ts
- Never `git push --force` without explicit user confirmation.
- Never `git clean -fdx` (wipes ignored files).
- Never delete `.git/`.
- Don't `git init` silently in a project that's not yours.

## When the repo doesn't exist yet
Ask the user. If they want it: `git init && git add -A && git_commit(message="chore: initial commit")`.
