"""Pure-Python helper for the /eval endpoint in browser_daemon.

Kept in a separate module so unit tests can import it without dragging in
Playwright (which is only installed inside the sandbox container).
"""

import re


def block_wrap(code: str) -> str:
    """Wrap a multi-statement JS body in an async IIFE so Playwright can
    page.evaluate() it. If the body has no explicit return statement, we
    patch one onto its last expression-like line so the caller still gets
    a value back (REPL-style ergonomics)."""
    stripped = code.rstrip().rstrip(";")
    if "return" not in stripped:
        lines = stripped.split("\n")
        for i in range(len(lines) - 1, -1, -1):
            ln = lines[i].strip()
            if ln and not ln.startswith("//"):
                if not re.match(
                    r"^(const|let|var|function|if|for|while|switch|try|return|throw)\b", ln
                ):
                    lines[i] = "return " + ln
                break
        stripped = "\n".join(lines)
    return f"(async () => {{ {stripped}; }})()"


def expression_wrap(expression: str) -> str:
    """Fast-path wrapper: treat input as a single expression."""
    return f"(async () => {{ return ({expression}); }})()"
