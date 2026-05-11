---
name: testing
description: Use when adding features or fixing bugs in code with a test suite. Defines the test-first workflow, how to interpret run_tests output, and the structure of a good test.
---

# Testing workflow

## Order of operations
1. **Read** the existing test file structure (`outline tests/test_foo.py` or `glob 'tests/test_*.py'`).
2. **Write a failing test FIRST** for the bug/feature (when feasible). Run `run_tests` — confirm it fails for the right reason.
3. **Implement** the change.
4. **Run `run_tests`** until `TESTS=PASS`.
5. **Lint** the touched files.
6. **Commit** with `git_commit`.

## Parsing `run_tests` output
Header format:
```
TESTS=PASS|FAIL runner=pytest passed=N failed=N errors=N skipped=N duration=Xs
```
If FAIL, the next lines list `FAILED <nodeid>` items. Use those nodeids to focus debugging:
- `run_tests(target="tests/test_foo.py::test_bar")` to re-run just one.

## Good test characteristics
- One assertion per concept (or grouped via subtests).
- Test names describe behavior, not implementation: `test_empty_plan_returns_empty_dict`.
- Use fixtures (conftest.py) for shared setup; never copy-paste boilerplate.
- Use `tmp_path` for filesystem tests (never write to repo root).
- For HTTP: use `TestClient`; never spin up a real server in tests.

## Smoke tests (live service)
The `tests/smoke_live.sh` script tests the running webchat. Run it AFTER `systemctl restart webchat` and AFTER pytest passes. It's the final gate before saying "done".

## When tests are missing
If the project has no tests yet, propose adding them as a separate plan item: "Add baseline tests for X". Don't silently ship untested code.
