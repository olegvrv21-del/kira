"""Additional coverage for app.py helpers + endpoints.

Covers:
  - _extract_text_and_images (text/image_url paths, malformed data URL)
  - _convert_messages_to_q (history pairing, image dropping)
  - _parse_eventstream + _es_event_type (frame parser)
  - _sse / _ensure_system / _safe_workspace
  - /chat unknown-model branch
  - /agent/coverage, /agent/coverage/file, /agent/auth_status
  - /agent/memory, /agent/memory/search, /agent/keys, /agent/metrics, /agent/hooks
  - /admin/restart auth
  - /agent/upload, /agent/file, /agent/reset, /agent/sessions/{sid}
  - /usage error branch
"""

import os
import struct
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

import app as app_mod


# ---------- _extract_text_and_images ----------


def test_extract_str():
    assert app_mod._extract_text_and_images("hi") == ("hi", [])


def test_extract_non_list_returns_empty():
    assert app_mod._extract_text_and_images(42) == ("", [])


def test_extract_text_parts():
    out = app_mod._extract_text_and_images(
        [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    )
    assert out == ("a\nb", [])


def test_extract_image_data_url():
    d = (
        "data:image/png;base64,aGVsbG8="
    )
    text, imgs = app_mod._extract_text_and_images([
        {"type": "text", "text": "caption"},
        {"type": "image_url", "image_url": {"url": d}},
    ])
    assert text == "caption"
    assert imgs == [{"format": "png", "source": {"bytes": "aGVsbG8="}}]


def test_extract_image_jpg_normalises_to_jpeg():
    d = "data:image/jpg;base64,YQ=="
    _, imgs = app_mod._extract_text_and_images([{"type": "image_url", "image_url": {"url": d}}])
    assert imgs[0]["format"] == "jpeg"


def test_extract_image_unknown_format_defaults_png():
    d = "data:image/heic;base64,YQ=="
    _, imgs = app_mod._extract_text_and_images([{"type": "image_url", "image_url": {"url": d}}])
    assert imgs[0]["format"] == "png"


def test_extract_image_non_data_url_ignored():
    _, imgs = app_mod._extract_text_and_images([{"type": "image_url", "image_url": {"url": "https://x.com/a.png"}}])
    assert imgs == []


def test_extract_image_malformed_url():
    _, imgs = app_mod._extract_text_and_images([{"type": "image_url", "image_url": {"url": "data:malformed"}}])
    assert imgs == []


# ---------- _convert_messages_to_q ----------


def test_convert_messages_pairs_history_and_keeps_only_last_image():
    msgs = [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": [
            {"type": "text", "text": "q1"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}},
        ]},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": [
            {"type": "text", "text": "q2"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,Yg=="}},
        ]},
    ]
    current, history, images = app_mod._convert_messages_to_q(msgs)
    # System text prepended to last user turn.
    assert "sys prompt" in current["userInputMessage"]["content"]
    assert "q2" in current["userInputMessage"]["content"]
    # Only one history pair from (q1, a1)
    assert len(history) == 2
    assert history[0]["userInputMessage"]["content"] == "q1"
    assert history[1]["assistantResponseMessage"]["content"] == "a1"
    # Only the last user turn keeps its image.
    assert len(images) == 1 and images[0]["source"]["bytes"] == "Yg=="


def test_convert_messages_empty_returns_blank_current():
    current, history, images = app_mod._convert_messages_to_q([])
    assert current["userInputMessage"]["content"] == ""
    assert history == [] and images == []


# ---------- _sse / _ensure_system ----------


def test_sse_dict_yields_data_prefix():
    b = app_mod._sse({"x": 1})
    assert b.startswith(b"data: ") and b.endswith(b"\n\n")


def test_sse_str_passthrough():
    b = app_mod._sse("hello")
    assert b == b"data: hello\n\n"


def test_ensure_system_prepends_when_missing():
    out = app_mod._ensure_system([{"role": "user", "content": "hi"}])
    assert out[0]["role"] == "system"
    assert out[1]["content"] == "hi"


def test_ensure_system_noop_when_present():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "u"}]
    out = app_mod._ensure_system(msgs)
    assert out is msgs


# ---------- _parse_eventstream / _es_event_type ----------


def _build_es_frame(event_type: str, payload: bytes) -> bytes:
    name = b":event-type"
    val = event_type.encode()
    headers = bytes([len(name)]) + name + b"\x07" + struct.pack(">H", len(val)) + val
    headers_len = len(headers)
    total = 12 + headers_len + len(payload) + 4
    return struct.pack(">II", total, headers_len) + b"\x00\x00\x00\x00" + headers + payload + b"\x00\x00\x00\x00"


def test_parse_eventstream_returns_frames():
    f = _build_es_frame("assistantResponseEvent", b'{"content":"x"}')
    buf = bytearray(f)
    out = app_mod._parse_eventstream(buf)
    assert len(out) == 1
    hdrs, payload = out[0]
    assert app_mod._es_event_type(hdrs) == "assistantResponseEvent"
    assert payload == b'{"content":"x"}'
    assert len(buf) == 0


def test_parse_eventstream_invalid_total_clears():
    bad = struct.pack(">II", 99_999_999, 0) + b"\x00" * 8  # > 16MB
    buf = bytearray(bad)
    out = app_mod._parse_eventstream(buf)
    assert out == [] and len(buf) == 0


def test_parse_eventstream_partial_keeps_buffer():
    f = _build_es_frame("e", b'{}')
    buf = bytearray(f[:10])
    out = app_mod._parse_eventstream(buf)
    assert out == [] and len(buf) == 10


# ---------- _safe_workspace ----------


def test_safe_workspace_rejects_bad_sid():
    with pytest.raises(HTTPException) as e:
        app_mod._safe_workspace("../etc")
    assert e.value.status_code == 400


def test_safe_workspace_rejects_empty():
    with pytest.raises(HTTPException):
        app_mod._safe_workspace("")


def test_safe_workspace_accepts_good():
    out = app_mod._safe_workspace("sid-good_1")
    assert out.endswith("/workspaces/sid-good_1")


# ---------- /chat unknown model ----------


def test_chat_rejects_unknown_model(app_client):
    r = app_client.post("/chat", json={"model": "bogus", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 400
    assert "unknown model" in r.json()["error"]


# ---------- /agent/* extra endpoints ----------


def test_coverage_endpoint(app_client):
    r = app_client.get("/agent/coverage")
    assert r.status_code == 200
    d = r.json()
    assert "ok" in d


def test_coverage_file_endpoint(app_client):
    r = app_client.get("/agent/coverage/file?path=app.py")
    assert r.status_code == 200
    assert "ok" in r.json()


def test_coverage_run_gate(app_client, monkeypatch):
    # without KIRA_COVERAGE_ALLOW_RUN it should refuse
    monkeypatch.delenv("KIRA_COVERAGE_ALLOW_RUN", raising=False)
    r = app_client.post("/agent/coverage/run")
    assert r.status_code == 200
    assert r.json().get("ok") is False


def test_auth_status_endpoint(app_client):
    r = app_client.get("/agent/auth_status")
    assert r.status_code == 200
    d = r.json()
    assert "install" in d and "runtime" in d


def test_memory_endpoint(app_client):
    r = app_client.get("/agent/memory")
    assert r.status_code == 200
    assert "chunks" in r.json()


def test_memory_search_endpoint(app_client):
    r = app_client.get("/agent/memory/search?q=test&k=2")
    assert r.status_code == 200
    d = r.json()
    assert d["query"] == "test"
    assert "hits" in d


def test_keys_endpoint(app_client):
    r = app_client.get("/agent/keys")
    assert r.status_code == 200
    assert "pool_size" in r.json()


def test_keys_reload_endpoint(app_client):
    r = app_client.post("/agent/keys/reload")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_metrics_endpoint(app_client):
    r = app_client.get("/agent/metrics")
    assert r.status_code == 200
    assert "by_tool" in r.json()


def test_metrics_sid_endpoint(app_client):
    r = app_client.get("/agent/metrics/some-sid")
    assert r.status_code == 200
    assert "by_tool" in r.json()


def test_hooks_endpoint(app_client):
    r = app_client.get("/agent/hooks")
    assert r.status_code == 200
    d = r.json()
    assert "status" in d and "hooks" in d


def test_admin_restart_requires_token(app_client):
    r = app_client.post("/admin/restart")
    assert r.status_code == 403


def test_admin_restart_bad_token(app_client):
    r = app_client.post("/admin/restart?token=wrong")
    assert r.status_code == 403


def test_skills_get_404(app_client):
    r = app_client.get("/skills/nope-skill-zzz")
    assert r.status_code == 404


def test_agent_reset(app_client):
    r = app_client.post("/agent/reset", json={"prompt": "", "session_id": "never"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_agent_stop_unknown_sid(app_client):
    r = app_client.post("/agent/stop/never-existed")
    assert r.status_code == 200
    # cancellation may return False for unknown sid
    assert "ok" in r.json()


def test_agent_sessions_list(app_client):
    r = app_client.get("/agent/sessions")
    assert r.status_code == 200
    assert "sessions" in r.json()


def test_agent_upload_and_file_roundtrip(app_client, tmp_path, monkeypatch):
    # Redirect _WORKSPACES_ROOT so we don't pollute repo.
    monkeypatch.setattr(app_mod, "_WORKSPACES_ROOT", str(tmp_path.resolve()))
    # Upload a file to a fresh sid.
    files = {"files": ("hello.txt", b"hello world", "text/plain")}
    r = app_client.post("/agent/upload/sid_up_1", files=files)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and d["saved"] and d["saved"][0]["name"].startswith("hello")
    # Now read it back via /agent/file/{sid}/{path}
    r2 = app_client.get("/agent/file/sid_up_1/hello.txt")
    assert r2.status_code == 200
    assert r2.content == b"hello world"


def test_agent_file_404(app_client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "_WORKSPACES_ROOT", str(tmp_path.resolve()))
    r = app_client.get("/agent/file/sid_x/no-such.txt")
    assert r.status_code in (400, 404)


def test_agent_file_bad_sid(app_client):
    r = app_client.get("/agent/file/..%2F..%2Fetc/passwd")
    assert r.status_code in (400, 404)


def test_session_get_emits_transcript(app_client, store):
    sid = "sid_endpoint_test"
    hist = [
        {"userInputMessage": {"content": "--- USER MESSAGE BEGIN ---\nhi\n--- USER MESSAGE END ---"}},
        {"assistantResponseMessage": {"content": "hello back", "toolUses": []}},
    ]
    store.save_session(sid, hist, "claude-haiku-4.5", "t")
    r = app_client.get(f"/agent/sessions/{sid}")
    assert r.status_code == 200
    d = r.json()
    assert d["sid"] == sid
    roles = [m["role"] for m in d["transcript"]]
    assert "user" in roles and "assistant" in roles


def test_session_rename(app_client, store):
    sid = "sid_rename_test"
    store.save_session(sid, [{"userInputMessage": {"content": "x"}}], "haiku", "old")
    r = app_client.post(f"/agent/sessions/{sid}/rename", json={"title": "New Title"})
    assert r.status_code == 200 and r.json()["ok"]


def test_session_delete(app_client, store):
    sid = "sid_delete_test"
    store.save_session(sid, [{"userInputMessage": {"content": "x"}}], "haiku", "t")
    r = app_client.delete(f"/agent/sessions/{sid}")
    assert r.status_code == 200


def test_usage_missing_key(app_client, monkeypatch):
    # Provider-routed: status='no_key' surfaces as HTTP 400.
    import llm

    class _Stub:
        name = "stub"
        supported_models = []
        async def usage(self):
            return {"supported": True, "status": "no_key", "error": "no key"}

    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: _Stub())
    r = app_client.get("/usage")
    assert r.status_code == 400
