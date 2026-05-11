---
name: subagents
description: Use when a task has independent parts that can be researched or executed in parallel, to avoid context bloat and finish faster.
---

`use_subagent` spawns isolated agent instances that run in parallel (up to 4) and return summaries. Each subagent has its own conversation but shares the same `/workspace`.

## When to delegate

- Reading and summarizing 3+ files where each can be processed independently.
- Running parallel searches over different parts of a codebase.
- Trying multiple approaches and picking the best result.
- Anything where intermediate output is large (logs, file dumps) and would pollute the main context.

## When NOT to delegate

- A single short task. Just do it inline.
- Steps with dependencies (output of A feeds into B). Run sequentially in the main loop.
- Anything that needs the conversation context (subagents start fresh).

## Invocation

```
use_subagent({
  command: "InvokeSubagents",
  content: {
    subagents: [
      { query: "Summarize ~/notes/a.md, focusing on …",
        relevant_context: "User cares about X" },
      { query: "Read all .py files in src/, list public functions" },
    ]
  }
})
```

Subagents do NOT have `use_subagent` themselves (no recursion). They have all other tools including browser_*.

## Anti-patterns

- Don't pass the entire conversation as `relevant_context`. Only the minimum needed.
- Don't issue more than 4 subagents at once — the 5th+ are dropped.
- Don't ask a subagent to commit to git or modify shared state — keep them read-only when possible.
