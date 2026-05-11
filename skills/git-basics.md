---
name: git-basics
description: Use when initializing a new project, committing changes, or recovering from a git mistake.
---

## New project

```bash
git init
git add -A
git commit -m "initial commit"
```

Set identity if missing:

```bash
git config user.email "agent@kira.local"
git config user.name "Kira Agent"
```

## Sensible commits

- Stage related changes together; split unrelated work into separate commits.
- Short imperative subject (≤50 chars), blank line, body if needed.
- Don't commit secrets, virtualenvs, node_modules, large binaries, build artifacts.

## Useful inspections

```bash
git status
git diff                 # unstaged
git diff --cached        # staged
git log --oneline -20
git show <hash>
```

## Undo

- Discard unstaged changes in file: `git checkout -- <file>`
- Unstage: `git restore --staged <file>`
- Amend last commit: `git commit --amend`
- Revert public commit safely: `git revert <hash>`
