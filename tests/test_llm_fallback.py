"""Tests for llm/fallback_provider.py — resilient provider chain.

We drive the wrapper with MockProvider instances (via an injected factory)
so we exercise the real failover / retry / classification logic without any
network. Balance state is asserted through the shared STATUS singleton.
"""
from __future__ import annotations

import asyncio

import pytest

from llm.base import Message
from llm.fallback_provider import (
    STATUS,
    FallbackProvider,
    _Target,
    classify_error,
    parse_chain,
)
from llm.mock_provider import MockProvider


@pytest.fixture(autouse=True)
def _reset_status():
    STATUS.banned.clear()
    STATUS.balance_exhausted = False
    STATUS.balance_target = ""
    STATUS.total_failovers = 0
    STATUS.last_error = ""
    STATUS.last_failover_at = 0.0
    yield


def _factory(providers: dict[str, MockProvider]):
    def get(name: str):
        if name not in providers:
            raise KeyError(name)
        return providers[name]
    return get


async def _collect(provider, model="m1"):
    out = []
    async for ev in provider.stream([Message(role="user", content="hi")], [], model=model):
        out.append(ev)
    return out


# ---------------- classify_error -------------------------------------------


@pytest.mark.parametrize("status,body,expected", [
    (402, "", "balance"),
    (200, "insufficient credits", "balance"),
    (None, "Your balance is too low", "balance"),
    (401, "", "failover"),
    (403, "forbidden", "failover"),
    (429, "rate limited", "retry"),
    (500, "", "retry"),
    (503, "upstream down", "retry"),
    (400, "bad request", "fatal"),
    (404, "no such model", "fatal"),
    (422, "validation", "fatal"),
    (None, "connection reset", "retry"),
])
def test_classify_error(status, body, expected):
    assert classify_error(status, body) == expected


# ---------------- parse_chain ----------------------------------------------


def test_parse_chain_basic():
    tg = parse_chain("openrouter:gpt-5.4-mini, openrouter:gpt-5.6 , mock")
    assert [t.provider_name for t in tg] == ["openrouter", "openrouter", "mock"]
    assert tg[0].model == "gpt-5.4-mini"
    assert tg[2].model is None  # no colon -> caller model


def test_parse_chain_empty():
    assert parse_chain("") == []
    assert parse_chain(None) == []
    assert parse_chain("  , ,") == []


# ---------------- happy path -----------------------------------------------


@pytest.mark.asyncio
async def test_first_target_succeeds():
    p = MockProvider(script=[{"type": "text", "text": "hello"}])
    fb = FallbackProvider(
        chain=[_Target("a", "m", "a:m"), _Target("b", "m", "b:m")],
        provider_factory=_factory({"a": p, "b": MockProvider()}),
    )
    evs = await _collect(fb)
    assert [e.type for e in evs][-1] == "done"
    assert any(e.type == "text" and e.text == "hello" for e in evs)
    assert STATUS.total_failovers == 0


# ---------------- balance failover -----------------------------------------


@pytest.mark.asyncio
async def test_balance_failover_to_next():
    dead = MockProvider(script=[{"type": "error", "message": "HTTP 402 insufficient credit",
                                 "meta": {"http_status": 402, "body": "insufficient credit"}}])
    # MockProvider emits error via meta.message; give it structured meta too.
    dead._script = [{"type": "error", "message": "insufficient credit"}]
    alive = MockProvider(script=[{"type": "text", "text": "backup answer"}])
    fb = FallbackProvider(
        chain=[_Target("dead", "m", "dead:m"), _Target("alive", "m", "alive:m")],
        provider_factory=_factory({"dead": dead, "alive": alive}),
    )
    evs = await _collect(fb)
    texts = [e.text for e in evs if e.type == "text"]
    assert "backup answer" in texts
    assert STATUS.balance_exhausted is True
    assert STATUS.balance_target == "dead:m"
    assert "dead:m" in STATUS.banned


@pytest.mark.asyncio
async def test_balance_flag_cleared_on_success():
    STATUS.balance_exhausted = True
    STATUS.balance_target = "old"
    good = MockProvider(script=[{"type": "text", "text": "ok"}])
    fb = FallbackProvider(chain=[_Target("g", "m", "g:m")],
                          provider_factory=_factory({"g": good}))
    await _collect(fb)
    assert STATUS.balance_exhausted is False


# ---------------- retry then failover --------------------------------------


@pytest.mark.asyncio
async def test_retry_then_success(monkeypatch):
    # Provider fails twice with 503 then succeeds. We patch sleep to be instant.
    calls = {"n": 0}

    class Flaky(MockProvider):
        async def stream(self, messages, tools, *, model, cancel=None, timeout=300.0, extra=None):
            calls["n"] += 1
            if calls["n"] <= 2:
                from llm.base import StreamEvent
                yield StreamEvent(type="error", text="HTTP 503", meta={"http_status": 503})
                yield StreamEvent(type="done")
            else:
                from llm.base import StreamEvent
                yield StreamEvent(type="text", text="recovered")
                yield StreamEvent(type="done")

    flaky = Flaky()
    fb = FallbackProvider(chain=[_Target("f", "m", "f:m")], retries=3, backoff=0.001,
                          provider_factory=_factory({"f": flaky}))
    evs = await _collect(fb)
    assert any(e.type == "text" and e.text == "recovered" for e in evs)
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_fatal_not_retried():
    from llm.base import StreamEvent

    class Bad(MockProvider):
        async def stream(self, messages, tools, *, model, cancel=None, timeout=300.0, extra=None):
            yield StreamEvent(type="error", text="HTTP 400 bad request", meta={"http_status": 400})
            yield StreamEvent(type="done")

    fb = FallbackProvider(chain=[_Target("bad", "m", "bad:m"), _Target("never", "m", "never:m")],
                          provider_factory=_factory({"bad": Bad(), "never": MockProvider()}))
    evs = await _collect(fb)
    # Surfaced error, no failover.
    assert any(e.type == "error" for e in evs)
    assert STATUS.total_failovers == 0


# ---------------- mid-stream error is not restarted ------------------------


@pytest.mark.asyncio
async def test_midstream_error_forwarded_not_restarted():
    from llm.base import StreamEvent

    class HalfThenDie(MockProvider):
        async def stream(self, messages, tools, *, model, cancel=None, timeout=300.0, extra=None):
            yield StreamEvent(type="text", text="partial ")
            yield StreamEvent(type="error", text="HTTP 500 mid", meta={"http_status": 500})
            yield StreamEvent(type="done")

    backup = MockProvider(script=[{"type": "text", "text": "SHOULD NOT APPEAR"}])
    fb = FallbackProvider(chain=[_Target("h", "m", "h:m"), _Target("b", "m", "b:m")],
                          provider_factory=_factory({"h": HalfThenDie(), "b": backup}))
    evs = await _collect(fb)
    texts = [e.text for e in evs if e.type == "text"]
    assert "partial " in texts
    assert "SHOULD NOT APPEAR" not in texts  # no failover after output started


# ---------------- all targets exhausted ------------------------------------


@pytest.mark.asyncio
async def test_all_targets_exhausted():
    from llm.base import StreamEvent

    class Dead(MockProvider):
        async def stream(self, messages, tools, *, model, cancel=None, timeout=300.0, extra=None):
            yield StreamEvent(type="error", text="HTTP 401", meta={"http_status": 401})
            yield StreamEvent(type="done")

    fb = FallbackProvider(chain=[_Target("a", "m", "a:m"), _Target("b", "m", "b:m")],
                          provider_factory=_factory({"a": Dead(), "b": Dead()}))
    evs = await _collect(fb)
    err = [e for e in evs if e.type == "error"]
    assert err and err[-1].meta.get("chain_exhausted")
    assert evs[-1].type == "done"


# ---------------- banned target skipped ------------------------------------


@pytest.mark.asyncio
async def test_banned_target_skipped():
    import time as _t
    STATUS.banned["a:m"] = {"until": _t.time() + 100, "reason": "test"}
    a = MockProvider(script=[{"type": "text", "text": "A"}])
    b = MockProvider(script=[{"type": "text", "text": "B"}])
    fb = FallbackProvider(chain=[_Target("a", "m", "a:m"), _Target("b", "m", "b:m")],
                          provider_factory=_factory({"a": a, "b": b}))
    evs = await _collect(fb)
    texts = [e.text for e in evs if e.type == "text"]
    assert texts == ["B"]  # a was banned, skipped


# ---------------- cancel during retry backoff ------------------------------


@pytest.mark.asyncio
async def test_cancel_during_backoff():
    from llm.base import StreamEvent

    class Flaky(MockProvider):
        async def stream(self, messages, tools, *, model, cancel=None, timeout=300.0, extra=None):
            yield StreamEvent(type="error", text="HTTP 503", meta={"http_status": 503})
            yield StreamEvent(type="done")

    fb = FallbackProvider(chain=[_Target("f", "m", "f:m")], retries=5, backoff=5.0,
                          provider_factory=_factory({"f": Flaky()}))
    cancel = asyncio.Event()

    async def _run():
        out = []
        async for ev in fb.stream([Message(role="user", content="hi")], [], model="m", cancel=cancel):
            out.append(ev)
            if ev.type == "throttle":
                cancel.set()  # cancel while it's about to back off
        return out

    evs = await asyncio.wait_for(_run(), timeout=3.0)
    assert any(e.type == "cancelled" for e in evs)


# ---------------- health / usage -------------------------------------------


@pytest.mark.asyncio
async def test_health_reports_chain():
    fb = FallbackProvider(chain=[_Target("mock", "mock-1", "mock:mock-1")],
                          provider_factory=_factory({"mock": MockProvider()}))
    h = await fb.health()
    assert h["name"] == "fallback"
    assert h["chain"] == ["mock:mock-1"]
    assert "fallback" in h
