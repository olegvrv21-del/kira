"""Cover /chat streaming via stream_q + stream_omni, and /agent gen() loop.

We mock httpx.AsyncClient so we never hit the network. The async ctx
manager + cx.stream() return a fake response whose aiter_bytes()/
aiter_lines() emit scripted byte sequences.
"""

import asyncio
import json
import struct
import zlib

import pytest
from fastapi.testclient import TestClient

import app as app_mod


# ---------- helpers ----------


def _make_eventstream_frame(event_type: str, payload: bytes) -> bytes:
    """Build a minimal AWS vnd.amazon.eventstream frame.

    Format: [total:4][headers_len:4][prelude_crc:4][headers...][payload][msg_crc:4]
    Header entry: [name_len:1][name][type:1][value_len:2][value]  (type 7 = string)
    """
    name = b":event-type"
    val = event_type.encode()
    header = (
        bytes([len(name)]) + name + b"\x07" + struct.pack(">H", len(val)) + val
    )
    headers_len = len(header)
    total_len = 12 + headers_len + len(payload) + 4
    prelude = struct.pack(">II", total_len, headers_len)
    prelude_crc = struct.pack(">I", zlib.crc32(prelude))
    msg_no_crc = prelude + prelude_crc + header + payload
    msg_crc = struct.pack(">I", zlib.crc32(msg_no_crc))
    return msg_no_crc + msg_crc


class _FakeResp:
    def __init__(self, status_code=200, byte_chunks=None, lines=None, body=b""):
        self.status_code = status_code
        self._chunks = byte_chunks or []
        self._lines = lines or []
        self._body = body

    async def aread(self):
        return self._body

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, *a, **kw):
        self._resp = _FakeClient._next

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, *a, **kw):
        return _FakeStreamCtx(self._resp)

    async def post(self, *a, **kw):  # for /usage path if reused
        return self._resp


def _patch_httpx(monkeypatch, resp):
    _FakeClient._next = resp
    monkeypatch.setattr(app_mod.httpx, "AsyncClient", _FakeClient)


async def _drain(agen):
    out = []
    async for chunk in agen:
        out.append(chunk)
    return out


# ---------- stream_q ----------


def test_stream_q_success_yields_text_chunks(monkeypatch):
    frame = _make_eventstream_frame(
        "assistantResponseEvent", json.dumps({"content": "hello world"}).encode()
    )
    resp = _FakeResp(status_code=200, byte_chunks=[frame])
    _patch_httpx(monkeypatch, resp)
    chunks = asyncio.run(_drain(app_mod.stream_q("claude-haiku-4.5", [{"role": "user", "content": "hi"}])))
    blob = b"".join(chunks)
    assert b"hello world" in blob
    assert b"[DONE]" in blob


def test_stream_q_filters_non_text_event_types(monkeypatch):
    """toolUseEvent / messageMetadataEvent / followupPromptEvent / empty type should be ignored."""
    frames = b"".join(
        _make_eventstream_frame(et, b"{}")
        for et in ("toolUseEvent", "messageMetadataEvent", "", "followupPromptEvent", "codeReferenceEvent", "initial-response")
    )
    resp = _FakeResp(status_code=200, byte_chunks=[frames])
    _patch_httpx(monkeypatch, resp)
    chunks = asyncio.run(_drain(app_mod.stream_q("claude-haiku-4.5", [{"role": "user", "content": "hi"}])))
    blob = b"".join(chunks)
    # No text deltas should appear, but [DONE] should
    assert b"[DONE]" in blob
    # The only 'data:' frames are the empty delta + DONE
    assert b"hello" not in blob


def test_stream_q_bad_payload_skipped(monkeypatch):
    # assistantResponseEvent with non-JSON payload — should be caught silently
    frame = _make_eventstream_frame("assistantResponseEvent", b"not-json")
    resp = _FakeResp(status_code=200, byte_chunks=[frame])
    _patch_httpx(monkeypatch, resp)
    chunks = asyncio.run(_drain(app_mod.stream_q("claude-haiku-4.5", [{"role": "user", "content": "hi"}])))
    blob = b"".join(chunks)
    assert b"[DONE]" in blob


def test_stream_q_upstream_4xx(monkeypatch):
    resp = _FakeResp(status_code=403, body=b"forbidden: bad token")
    _patch_httpx(monkeypatch, resp)
    chunks = asyncio.run(_drain(app_mod.stream_q("claude-haiku-4.5", [{"role": "user", "content": "hi"}])))
    blob = b"".join(chunks)
    assert b"403" in blob and b"forbidden" in blob
    assert b"[DONE]" not in blob  # early return


def test_stream_q_upstream_exception(monkeypatch):
    class BoomClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, *a, **kw):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(app_mod.httpx, "AsyncClient", BoomClient)
    chunks = asyncio.run(_drain(app_mod.stream_q("claude-haiku-4.5", [{"role": "user", "content": "hi"}])))
    blob = b"".join(chunks)
    assert b"error" in blob and b"q upstream" in blob


# ---------- stream_omni ----------


def test_stream_omni_passes_lines(monkeypatch):
    resp = _FakeResp(status_code=200, lines=["data: a", "data: b", "", "data: [DONE]"])
    _patch_httpx(monkeypatch, resp)
    chunks = asyncio.run(_drain(app_mod.stream_omni("some-model", [{"role": "user", "content": "hi"}])))
    blob = b"".join(chunks)
    # blank lines should be skipped
    assert b"data: a\n" in blob and b"data: b\n" in blob
    assert b"data: [DONE]\n" in blob


def test_stream_omni_upstream_4xx(monkeypatch):
    resp = _FakeResp(status_code=500, body=b"upstream down")
    _patch_httpx(monkeypatch, resp)
    chunks = asyncio.run(_drain(app_mod.stream_omni("some-model", [{"role": "user", "content": "hi"}])))
    blob = b"".join(chunks)
    assert b"upstream down" in blob


def test_stream_omni_exception(monkeypatch):
    class BoomClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, *a, **kw):
            raise OSError("dns fail")

    monkeypatch.setattr(app_mod.httpx, "AsyncClient", BoomClient)
    chunks = asyncio.run(_drain(app_mod.stream_omni("some-model", [{"role": "user", "content": "hi"}])))
    blob = b"".join(chunks)
    assert b"error" in blob and b"upstream" in blob


# ---------- /chat endpoint dispatches by prefix ----------


def test_chat_endpoint_routes_to_stream_q(monkeypatch):
    monkeypatch.setattr(app_mod, "MODEL_IDS", {"q/test-m"})
    frame = _make_eventstream_frame("assistantResponseEvent", json.dumps({"content": "x"}).encode())
    _patch_httpx(monkeypatch, _FakeResp(status_code=200, byte_chunks=[frame]))
    client = TestClient(app_mod.app)
    r = client.post("/chat", json={"model": "q/test-m", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert b"[DONE]" in r.content


def test_chat_endpoint_routes_to_stream_omni(monkeypatch):
    monkeypatch.setattr(app_mod, "MODEL_IDS", {"plain-model"})
    _patch_httpx(monkeypatch, _FakeResp(status_code=200, lines=["data: hello", "data: [DONE]"]))
    client = TestClient(app_mod.app)
    r = client.post("/chat", json={"model": "plain-model", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert b"data: hello" in r.content


# ---------- /agent endpoint gen() loop ----------


def test_agent_endpoint_streams_run_agent(monkeypatch, tmp_path):
    """Hit /agent with a stubbed agent_runtime.run_agent.

    Covers: nonlocal hist init, images conversion, stats event credit
    persistence, derive_title + save_session calls.
    """
    import agent_runtime
    import agent_store

    async def fake_run(api_key, prompt, model, session_id=None, history=None, images=None, **_kw):
        # mirror real SSE shape
        yield ("data: " + json.dumps({"type": "meta", "session_id": session_id}) + "\n\n").encode()
        yield ("data: " + json.dumps({"type": "text", "delta": "hi"}) + "\n\n").encode()
        yield ("data: " + json.dumps({"type": "stats", "credits": 0.123}) + "\n\n").encode()
        yield ("data: " + json.dumps({"type": "done"}) + "\n\n").encode()

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run)
    client = TestClient(app_mod.app)
    sid = "agentep1"
    r = client.post(
        "/agent",
        json={
            "prompt": "hello",
            "session_id": sid,
            "model": "claude-haiku-4.5",
            "images": [
                {"format": "jpeg", "data_base64": "AAAA"},
                {"format": "png"},  # missing data, should be skipped
                {"data_base64": "BB"},  # no format -> defaults to png
            ],
        },
    )
    assert r.status_code == 200
    assert b'"type": "meta"' in r.content
    assert b'"type": "done"' in r.content
    # credits should be persisted
    assert agent_store.get_session_credits(sid) >= 0.12


def test_agent_endpoint_q_prefix_strip(monkeypatch):
    """model='q/xxx' should be stripped to 'xxx' before passing to run_agent."""
    import agent_runtime
    captured = {}

    async def fake_run(api_key, prompt, model, session_id=None, history=None, images=None, **_kw):
        captured["model"] = model
        yield ("data: " + json.dumps({"type": "done"}) + "\n\n").encode()

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run)
    client = TestClient(app_mod.app)
    # Use a live model so the dead-upstream remap doesn't rewrite it; we only
    # assert the "q/" prefix is stripped.
    client.post("/agent", json={"prompt": "x", "model": "q/gpt-5.4", "session_id": "qpfx"})
    assert captured["model"] == "gpt-5.4"


def test_agent_endpoint_uses_default_model(monkeypatch):
    import agent_runtime
    captured = {}

    async def fake_run(api_key, prompt, model, session_id=None, history=None, images=None, **_kw):
        captured["model"] = model
        yield ("data: " + json.dumps({"type": "done"}) + "\n\n").encode()

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run)
    client = TestClient(app_mod.app)
    client.post("/agent", json={"prompt": "x", "session_id": "defmod"})
    assert captured["model"] == app_mod.DEFAULT_MODEL


def test_agent_endpoint_remaps_dead_models(monkeypatch):
    """Legacy Kiro/Claude models (dead upstream) are remapped to DEFAULT_MODEL."""
    import agent_runtime
    for dead in ("kr/claude-sonnet-4.5", "claude-opus-4.7", "kiro/claude-haiku-4.5"):
        captured = {}

        async def fake_run(api_key, prompt, model, session_id=None, history=None, images=None, **_kw):
            captured["model"] = model
            yield ("data: " + json.dumps({"type": "done"}) + "\n\n").encode()

        monkeypatch.setattr(agent_runtime, "run_agent", fake_run)
        client = TestClient(app_mod.app)
        client.post("/agent", json={"prompt": "x", "model": dead, "session_id": "dead"})
        assert captured["model"] == app_mod.DEFAULT_MODEL, dead


def test_agent_endpoint_auto_routes(monkeypatch):
    """model='auto' triggers the router: a route event is emitted and the
    resolved model (not 'auto') is passed to run_agent."""
    import agent_runtime
    import llm.router as _router

    captured = {}

    async def fake_run(api_key, prompt, model, session_id=None, history=None, images=None, **_kw):
        captured["model"] = model
        yield ("data: " + json.dumps({"type": "done"}) + "\n\n").encode()

    async def fake_route(prompt, *, llm_one_shot=None):
        return ("gpt-5.4-mini", "simple")

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run)
    monkeypatch.setattr(_router, "route", fake_route)
    client = TestClient(app_mod.app)
    r = client.post("/agent", json={"prompt": "привет", "model": "auto", "session_id": "auto1"})
    body = r.content.decode()
    assert '"type": "route"' in body
    assert '"model": "gpt-5.4-mini"' in body
    assert captured["model"] == "gpt-5.4-mini"


def test_agent_endpoint_claude_live_when_key_set(monkeypatch):
    """With KIRA_CLAUDE_KEY set, a bare claude model is NOT remapped."""
    import agent_runtime
    captured = {}

    async def fake_run(api_key, prompt, model, session_id=None, history=None, images=None, **_kw):
        captured["model"] = model
        yield ("data: " + json.dumps({"type": "done"}) + "\n\n").encode()

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run)
    monkeypatch.setenv("KIRA_CLAUDE_KEY", "claude-key")
    client = TestClient(app_mod.app)
    client.post("/agent", json={"prompt": "x", "model": "claude-sonnet-4-6", "session_id": "cl1"})
    assert captured["model"] == "claude-sonnet-4-6"


def test_agent_endpoint_frugal_downgrades(monkeypatch):
    """Over the expensive-tier daily cap, /agent downgrades the model and
    emits an info note."""
    import agent_runtime
    import agent_frugal
    captured = {}

    async def fake_run(api_key, prompt, model, session_id=None, history=None, images=None, **_kw):
        captured["model"] = model
        yield ("data: " + json.dumps({"type": "done"}) + "\n\n").encode()

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run)
    # Force the guard to always downgrade.
    monkeypatch.setattr(agent_frugal, "guard",
                        lambda m: ("gpt-5.4", "💸 downgraded to save balance"))
    client = TestClient(app_mod.app)
    r = client.post("/agent", json={"prompt": "x", "model": "gpt-5.6", "session_id": "frugal1"})
    body = r.content.decode()
    assert captured["model"] == "gpt-5.4"
    assert "downgraded to save balance" in body
