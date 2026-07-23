"""FallbackProvider — resilient chain of LLM providers.

Motivation
----------
Kira runs on a single upstream gateway (Unity2 via the OpenRouter adapter).
When that gateway runs out of credit (HTTP 402) or has a transient outage
(429/5xx/timeout), the bare adapter emits one `error` event and stops — Kira
goes silent with no attempt to recover. This wrapper turns a *list* of
`(provider, model)` targets into a single `LLMProvider` that:

  1. Retries transient failures on the current target with exponential backoff.
  2. Fails over to the next target when the current one is unusable.
  3. Records "balance exhausted" so /agent/health can surface it (and the
     existing health_alert.sh Telegram pipeline fires) — no new secrets, no
     new polling loop.

Safety rule: we only fail over / retry when **nothing has been emitted yet**
for the current turn. Once we've streamed assistant text or a tool_call to the
caller, restarting on another provider would corrupt the transcript, so a
mid-stream failure is surfaced verbatim (same behaviour as before).

Configuration (env)
-------------------
  KIRA_LLM_PROVIDER=fallback           enable this wrapper
  KIRA_LLM_CHAIN=<spec>                ordered chain, comma-separated targets:
        "openrouter:gpt-5.4-mini, openrouter:gpt-5.6, mock:mock-1"
     Each target is "<provider_name>:<model>". The provider name must be
     registered in llm/__init__.py. Model is passed through to that provider.
     If a target omits ":model", the caller's requested model is used.
  KIRA_LLM_FALLBACK_RETRIES=2          transient retries per target (default 2)
  KIRA_LLM_FALLBACK_BACKOFF=1.5        base backoff seconds (default 1.5)

If KIRA_LLM_CHAIN is unset we fall back to a single target built from
KIRA_LLM_PROVIDER_INNER (default "openrouter") + the requested model, so the
wrapper is safe to enable even before a chain is configured.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .base import LLMProvider, Message, StreamEvent, ToolSpec

# ---------------------------------------------------------------------------
# Shared status — read by app.py /agent/health. Kept module-level so a single
# import surfaces the latest failover state without threading it through the
# runtime. Thread-safe enough: assignments are atomic and we only ever read a
# snapshot.
# ---------------------------------------------------------------------------


@dataclass
class _ChainStatus:
    # Per-target ban bookkeeping: name -> {"until": epoch, "reason": str}
    banned: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_error: str = ""
    last_failover_at: float = 0.0
    balance_exhausted: bool = False  # sticky until a target answers again
    balance_target: str = ""
    total_failovers: int = 0

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "balance_exhausted": self.balance_exhausted,
            "balance_target": self.balance_target,
            "last_error": self.last_error[:300],
            "last_failover_ago": round(now - self.last_failover_at, 1) if self.last_failover_at else None,
            "total_failovers": self.total_failovers,
            "banned": {
                k: {"reason": v.get("reason", ""),
                    "for_seconds": max(0, round(v.get("until", 0) - now, 1))}
                for k, v in self.banned.items()
                if v.get("until", 0) > now
            },
        }


STATUS = _ChainStatus()

# How long a target stays banned after an auth/balance failure (seconds).
_BAN_SECONDS = int(os.environ.get("KIRA_LLM_FALLBACK_BAN_SECONDS", "900"))


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

# Verdicts:
#   "retry"    transient — retry same target with backoff, then fail over
#   "failover" target unusable now — skip to next target (short ban)
#   "balance"  out of credit — fail over + ban + set balance_exhausted flag
#   "fatal"    deterministic (bad request/validation) — surface, do NOT retry
_BALANCE_MARKERS = (
    "insufficient", "quota", "balance", "credit", "payment",
    "exceeded your current", "billing", "out of funds",
)


def classify_error(status: int | None, body: str | None) -> str:
    """Map an upstream failure to a recovery verdict."""
    b = (body or "").lower()
    if status == 402 or any(m in b for m in _BALANCE_MARKERS):
        return "balance"
    if status in (401, 403):
        return "failover"  # bad/expired key on this target — skip it
    if status == 429:
        return "retry"
    if status is not None and 500 <= status < 600:
        return "retry"
    if status is not None and 400 <= status < 500:
        # 400/404/422 etc. — deterministic; a retry/failover won't help and
        # may mask a real bug. Surface it.
        return "fatal"
    # No status (connection error / timeout / exception) — transient.
    return "retry"


# ---------------------------------------------------------------------------
# Chain parsing
# ---------------------------------------------------------------------------


@dataclass
class _Target:
    provider_name: str
    model: str | None  # None -> use caller's requested model
    label: str

    def resolve_model(self, requested: str) -> str:
        return self.model or requested


def parse_chain(spec: str | None) -> list[_Target]:
    """Parse "prov:model, prov2:model2" into ordered targets.

    Blank/whitespace-only entries are ignored. A target without ":" uses the
    caller's requested model at stream time.
    """
    targets: list[_Target] = []
    for raw in (spec or "").split(","):
        item = raw.strip()
        if not item:
            continue
        if ":" in item:
            prov, model = item.split(":", 1)
            prov, model = prov.strip(), model.strip() or None
        else:
            prov, model = item, None
        if not prov:
            continue
        targets.append(_Target(prov, model, item))
    return targets


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class FallbackProvider:
    """Meta-provider that streams through an ordered chain with failover."""

    name = "fallback"

    def __init__(
        self,
        chain: list[_Target] | None = None,
        *,
        retries: int | None = None,
        backoff: float | None = None,
        provider_factory=None,
    ):
        if chain is None:
            chain = parse_chain(os.environ.get("KIRA_LLM_CHAIN"))
        if not chain:
            # Degenerate but safe default: single inner provider, caller model.
            inner = os.environ.get("KIRA_LLM_PROVIDER_INNER", "openrouter")
            chain = [_Target(inner, None, inner)]
        self.chain = chain
        self.retries = (retries if retries is not None
                        else int(os.environ.get("KIRA_LLM_FALLBACK_RETRIES", "2")))
        self.backoff = (backoff if backoff is not None
                        else float(os.environ.get("KIRA_LLM_FALLBACK_BACKOFF", "1.5")))
        # Injectable for tests; defaults to the package registry.
        if provider_factory is None:
            from . import get_provider as _gp
            provider_factory = _gp
        self._factory = provider_factory
        self.supported_models = [t.label for t in self.chain]

    # -- helpers ------------------------------------------------------------

    def _get_provider(self, name: str) -> LLMProvider:
        return self._factory(name)

    def _ban(self, label: str, reason: str, seconds: int = _BAN_SECONDS) -> None:
        STATUS.banned[label] = {"until": time.time() + seconds, "reason": reason}

    def _is_banned(self, label: str) -> bool:
        b = STATUS.banned.get(label)
        return bool(b and b.get("until", 0) > time.time())

    def _maybe_clear_balance(self, answering_label: str) -> None:
        """Clear the sticky balance-exhausted flag only on a genuine recovery.

        A backup target answering does NOT count — we stay degraded (and keep
        alerting) until every balance-banned target's ban has expired. The
        answering target must also not be balance-banned itself.
        """
        if not STATUS.balance_exhausted:
            return
        now = time.time()
        still_balance_banned = any(
            v.get("reason") == "balance-exhausted" and v.get("until", 0) > now
            for v in STATUS.banned.values()
        )
        if not still_balance_banned:
            STATUS.balance_exhausted = False
            STATUS.balance_target = ""

    # -- stream -------------------------------------------------------------

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        model: str,
        cancel: asyncio.Event | None = None,
        timeout: float = 300.0,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        last_err_text = ""
        last_meta: dict[str, Any] = {}
        n_targets = len(self.chain)

        for ti, target in enumerate(self.chain):
            is_last = ti == n_targets - 1
            if self._is_banned(target.label) and not is_last:
                # Skip a recently-failed target (unless it's our last resort).
                continue

            use_model = target.resolve_model(model)
            attempt = 0
            while True:
                if cancel is not None and cancel.is_set():
                    yield StreamEvent(type="cancelled")
                    yield StreamEvent(type="done")
                    return

                try:
                    provider = self._get_provider(target.provider_name)
                except Exception as e:
                    # Registry/construction failure — treat as failover.
                    last_err_text = f"{target.label}: provider init failed: {e}"
                    last_meta = {"exception": type(e).__name__}
                    self._ban(target.label, "init-failed", 60)
                    break  # next target

                emitted_any = False
                verdict: str | None = None
                err_text = ""
                err_meta: dict[str, Any] = {}

                async for ev in provider.stream(
                    messages, tools, model=use_model,
                    cancel=cancel, timeout=timeout, extra=extra,
                ):
                    if ev.type == "error" and not emitted_any:
                        # Pre-output error: capture, decide recovery, do NOT
                        # forward yet (a retry/failover may still succeed).
                        err_text = ev.text or (ev.meta or {}).get("message", "") or "provider error"
                        err_meta = ev.meta or {}
                        verdict = classify_error(err_meta.get("http_status"),
                                                 err_meta.get("body") or err_text)
                        continue
                    if ev.type == "done":
                        if verdict is not None:
                            # Stream ended on a pre-output error — break out to
                            # the recovery logic instead of forwarding done.
                            break
                        if emitted_any:
                            # Clean, productive stream. Clear the sticky balance
                            # flag ONLY if the answer came from a target that is
                            # not itself balance-banned AND no other target is
                            # still balance-banned — i.e. we're truly recovered,
                            # not just limping on a backup (which should keep the
                            # alert firing).
                            self._maybe_clear_balance(target.label)
                            yield ev
                            return
                        # Empty-but-clean stream (no text, no tools, no error):
                        # forward as-is; nothing to recover.
                        yield ev
                        return
                    if ev.type == "cancelled":
                        yield ev
                        yield StreamEvent(type="done")
                        return
                    # Any content-bearing event marks the point of no return.
                    if ev.type in ("text", "tool_call"):
                        emitted_any = True
                    if ev.type == "error" and emitted_any:
                        # Mid-stream error after output — cannot safely restart.
                        yield ev
                        continue
                    yield ev

                # --- post-stream recovery decision ------------------------
                if verdict is None:
                    # Stream finished without a pre-output error but also never
                    # emitted a done we returned on (defensive). Treat as clean.
                    return

                last_err_text = f"{target.label}: {err_text}"
                last_meta = err_meta
                STATUS.last_error = last_err_text

                if verdict == "fatal":
                    # Deterministic — surface and stop; failover won't help.
                    yield StreamEvent(type="error",
                                      text=last_err_text, meta=err_meta)
                    yield StreamEvent(type="done")
                    return

                if verdict == "balance":
                    STATUS.balance_exhausted = True
                    STATUS.balance_target = target.label
                    self._ban(target.label, "balance-exhausted")
                    STATUS.total_failovers += 1
                    STATUS.last_failover_at = time.time()
                    yield StreamEvent(type="throttle", meta={
                        "reason": "balance", "target": target.label,
                        "message": f"{target.label} out of credit — failing over",
                    })
                    break  # next target

                if verdict == "failover":
                    self._ban(target.label, "auth-or-unusable")
                    STATUS.total_failovers += 1
                    STATUS.last_failover_at = time.time()
                    yield StreamEvent(type="throttle", meta={
                        "reason": "failover", "target": target.label,
                        "message": f"{target.label} unusable — failing over",
                    })
                    break  # next target

                # verdict == "retry"
                if attempt < self.retries:
                    sleep = self.backoff * (2 ** attempt)
                    yield StreamEvent(type="throttle", meta={
                        "reason": "retry", "target": target.label,
                        "attempt": attempt + 1, "sleep": round(sleep, 2),
                    })
                    # Interruptible sleep so /agent/stop is responsive.
                    try:
                        if cancel is not None:
                            await asyncio.wait_for(cancel.wait(), timeout=sleep)
                            # cancel fired
                            yield StreamEvent(type="cancelled")
                            yield StreamEvent(type="done")
                            return
                        else:
                            await asyncio.sleep(sleep)
                    except TimeoutError:
                        pass  # slept the full backoff, retry
                    attempt += 1
                    continue  # retry same target
                else:
                    # Retries exhausted — fail over.
                    STATUS.total_failovers += 1
                    STATUS.last_failover_at = time.time()
                    break  # next target

        # All targets exhausted.
        STATUS.last_error = last_err_text or "all fallback targets exhausted"
        yield StreamEvent(
            type="error",
            text=f"all LLM targets failed; last: {last_err_text or 'unknown'}",
            meta={**last_meta, "chain_exhausted": True},
        )
        yield StreamEvent(type="done")

    # -- introspection ------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": "critical" if STATUS.balance_exhausted else "ok",
            "chain": [t.label for t in self.chain],
            "fallback": STATUS.snapshot(),
        }

    async def usage(self) -> dict[str, Any]:
        # Delegate usage to the first target that supports it.
        for t in self.chain:
            try:
                u = await self._get_provider(t.provider_name).usage()
                if u.get("supported", True):
                    return {**u, "via": t.label}
            except Exception:
                continue
        return {"supported": False, "provider": self.name}


# Protocol conformance check (constructed lazily to avoid env coupling).
def _proto_check() -> LLMProvider:
    return FallbackProvider(chain=[_Target("mock", "mock-1", "mock:mock-1")])
