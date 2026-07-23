"""Tool quality assurance — make Kira's tool feedback accurate and actionable.

Two intelligence upgrades that stop the agent from reasoning on false premises:

1. validate_args(name, args)
   Lightweight JSON-Schema check against agent_tool_specs.json BEFORE a tool
   runs. Instead of a raw KeyError/TypeError deep inside a tool (which the model
   can't cleanly recover from), it returns a precise, human-readable error:
       "missing required parameter: 'path'"
       "parameter 'insert_line' must be integer, got str"
   A model that hears exactly what's wrong fixes it in one turn.

2. semantic_status(name, status, output)
   Many tools "run" (don't raise) yet the *operation* failed: execute_bash with
   exit 1, run_tests with TESTS=FAIL, git with a non-zero exit. The dispatcher
   used to record all of those as success, which poisons metrics AND the agent's
   own view of the world. This reads the tool's own output markers and demotes
   success→error when the operation actually failed.

Both are deliberately dependency-free (only stdlib + the specs file) and
fail-open: if anything is uncertain, they return the least-surprising result
(valid / unchanged status) so they can never block a legitimate call.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache

_SPECS_PATH = os.path.join(os.path.dirname(__file__), "agent_tool_specs.json")


# ---------------------------------------------------------------------------
# 1. Argument validation
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _schema_map() -> dict[str, dict]:
    """name -> JSON schema (the inner {type:object, properties, required})."""
    out: dict[str, dict] = {}
    try:
        with open(_SPECS_PATH, encoding="utf-8") as fh:
            specs = json.load(fh)
    except Exception:
        return out
    for s in specs:
        ts = s.get("toolSpecification") or s.get("function") or s
        name = ts.get("name")
        schema = (ts.get("inputSchema") or {}).get("json") or ts.get("parameters") or {}
        if name and isinstance(schema, dict):
            out[name] = schema
    return out


_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _type_ok(value, json_type: str) -> bool:
    py = _JSON_TYPES.get(json_type)
    if py is None:
        return True  # unknown type spec → don't second-guess
    if json_type == "integer" and isinstance(value, bool):
        return False  # bool is a subclass of int, but not an integer arg
    if json_type == "number" and isinstance(value, bool):
        return False
    return isinstance(value, py)


def validate_args(name: str, args) -> str | None:
    """Return an error string if `args` violates the tool's schema, else None.

    Fail-open: unknown tool or missing schema → None (allow).
    """
    schema = _schema_map().get(name)
    if not schema:
        return None
    if not isinstance(args, dict):
        return f"arguments for '{name}' must be an object, got {type(args).__name__}"

    required = schema.get("required") or []
    for req in required:
        if req not in args or args[req] is None:
            return f"missing required parameter: '{req}'"

    props = schema.get("properties") or {}
    for key, val in args.items():
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue  # extra/unknown params are tolerated (many tools accept them)
        jtype = spec.get("type")
        if isinstance(jtype, str) and val is not None and not _type_ok(val, jtype):
            return (f"parameter '{key}' must be {jtype}, "
                    f"got {type(val).__name__}")
        enum = spec.get("enum")
        if enum and val not in enum:
            return f"parameter '{key}' must be one of {enum}, got {val!r}"
    return None


# ---------------------------------------------------------------------------
# 2. Semantic status
# ---------------------------------------------------------------------------

# Output-marker patterns that indicate the operation FAILED even though the
# tool itself didn't raise. Ordered, checked case-insensitively where sensible.
_EXIT_RE = re.compile(r"---\s*exit\s+(-?\d+)\s*---")
_RC_RE = re.compile(r"\brc=(-?\d+)\b")
_TESTS_FAIL_RE = re.compile(r"\bTESTS=FAIL\b")

# Tools whose output uses the "--- exit N ---" / rc= convention and whose
# non-zero code genuinely means failure of the requested operation.
_EXIT_AWARE_TOOLS = {
    "execute_bash", "git", "git_commit", "dev_loop", "verify_change",
}
_TEST_TOOLS = {"run_tests", "verify_change", "dev_loop"}


def semantic_status(name: str, status: str, output: str) -> tuple[str, str | None]:
    """Return (status, reason). Demotes 'success'→'error' when the tool output
    shows the operation actually failed. `reason` explains the demotion (None if
    unchanged). Never promotes error→success.
    """
    if status != "success" or not isinstance(output, str):
        return status, None

    # Test failures.
    if name in _TEST_TOOLS and _TESTS_FAIL_RE.search(output):
        return "error", "tests failed (TESTS=FAIL)"

    # Non-zero exit / rc markers.
    if name in _EXIT_AWARE_TOOLS:
        m = _EXIT_RE.search(output)
        if m and int(m.group(1)) != 0:
            return "error", f"non-zero exit {m.group(1)}"
        m = _RC_RE.search(output)
        if m and int(m.group(1)) != 0:
            return "error", f"non-zero rc {m.group(1)}"

    return status, None
