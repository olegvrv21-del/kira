"""Unit tests for agent_runtime pure helpers (no LLM, no docker).

Targets the long tail of low-coverage helpers: _sse, _q_headers,
_event_type, _parse_frames, _env_state, _user_msg, _safe_sid,
request_cancel and the AWS event-stream framing parser.
"""

import asyncio
import json
import struct


import agent_runtime as ar


# ---------- trivial helpers ----------


def test_sse_encodes_json_with_newlines():
    b = ar._sse({"a": 1, "text": "привет"})
    assert b.startswith(b"data: ")
    assert b.endswith(b"\n\n")
    payload = b[6:-2].decode("utf-8")
    assert json.loads(payload) == {"a": 1, "text": "привет"}


def test_q_headers_carries_bearer_token():
    h = ar._q_headers("ksk_abc123")
    assert h["Authorization"] == "Bearer ksk_abc123"
    assert h["tokentype"] == "API_KEY"
    assert "GenerateAssistantResponse" in h["X-Amz-Target"]
    assert h["Accept"] == "application/vnd.amazon.eventstream"


def test_env_state_shape():
    s = ar._env_state("/workspace")
    assert s["currentWorkingDirectory"] == "/workspace"
    assert s["operatingSystem"] in ("linux", "darwin", "windows")


# ---------- _safe_sid ----------


def test_safe_sid_passes_good_ids():
    for good in ("abc", "a-b", "ABC_xyz", "0123456789abcdef", "x" * 64):
        assert ar._safe_sid(good) == good


def test_safe_sid_rejects_bad_ids():
    for bad in (None, "", "a/b", "..", "a b", "a\nb", "a\x00b", "x" * 65, "рус"):
        sid = ar._safe_sid(bad)
        assert ar._SID_RE.match(sid), f"_safe_sid leaked for {bad!r}"
        assert sid != bad


# ---------- request_cancel / cancellation events ----------


def test_request_cancel_unknown_sid_returns_false():
    assert ar.request_cancel("no_such_session_zzz") is False


def test_request_cancel_signals_registered_event():
    async def go():
        ev = ar._register_cancel("unit-test-sid-1")
        try:
            assert not ev.is_set()
            assert ar.request_cancel("unit-test-sid-1") is True
            assert ev.is_set()
        finally:
            ar._unregister_cancel("unit-test-sid-1")

    asyncio.run(go())


def test_unregister_cancel_idempotent():
    ar._unregister_cancel("never-existed-xyz")  # must not raise


# ---------- _user_msg ----------


def test_user_msg_wraps_text():
    m = ar._user_msg("hello", "claude-x", "/cwd")
    inp = m["userInputMessage"]
    assert "--- USER MESSAGE BEGIN ---\nhello--- USER MESSAGE END ---" in inp["content"]
    assert inp["modelId"] == "claude-x"
    assert inp["origin"] == "KIRO_CLI"
    assert inp["userInputMessageContext"]["envState"]["currentWorkingDirectory"] == "/cwd"
    assert "tools" in inp["userInputMessageContext"]


def test_user_msg_empty_text():
    m = ar._user_msg("", "claude-x", "/cwd")
    assert m["userInputMessage"]["content"] == ""


def test_user_msg_includes_tool_results_when_given():
    tr = [{"toolUseId": "t1", "content": [{"text": "ok"}], "status": "success"}]
    m = ar._user_msg("continue", "m", "/c", tool_results=tr)
    assert m["userInputMessage"]["userInputMessageContext"]["toolResults"] == tr


def test_user_msg_custom_tool_specs():
    m = ar._user_msg("x", "m", "/c", tool_specs=[])
    assert m["userInputMessage"]["userInputMessageContext"]["tools"] == []


def test_user_msg_with_images():
    imgs = [{"format": "png", "source": {"bytes": "AAAA"}}]
    m = ar._user_msg("see", "m", "/c", images=imgs)
    assert m["userInputMessage"]["images"] == imgs


# ---------- _event_type + _parse_frames ----------


def _make_event_stream_frame(event_type: str, payload: dict) -> bytes:
    """Build a single AWS event-stream frame the way our parser expects it.

    Frame layout:
      [total_len:4][headers_len:4][crc-prelude:4 (ignored by us)]
      [headers...][payload...][crc-frame:4 (ignored)]
    Each header: [name_len:1][name][type:1][...]; we use type=7 (str) for
    :event-type, which is the only header the parser actually reads.
    """
    name = b":event-type"
    val = event_type.encode("utf-8")
    headers = bytes([len(name)]) + name + bytes([7]) + struct.pack(">H", len(val)) + val
    body = json.dumps(payload).encode("utf-8")
    headers_len = len(headers)
    total_len = 12 + headers_len + len(body) + 4  # +4 for the message CRC
    prelude = struct.pack(">II", total_len, headers_len) + b"\x00\x00\x00\x00"  # fake prelude CRC
    crc = b"\x00\x00\x00\x00"
    return prelude + headers + body + crc


def test_event_type_reads_first_string_header():
    # build just the header block as our helper would
    name = b":event-type"
    val = b"assistantResponseEvent"
    headers = bytes([len(name)]) + name + bytes([7]) + struct.pack(">H", len(val)) + val
    assert ar._event_type(headers) == "assistantResponseEvent"


def test_event_type_unknown_type_short_circuits():
    # type=9 isn't handled — parser breaks; should return whatever it had
    headers = bytes([5]) + b"thing" + bytes([9])  # bogus type byte
    assert ar._event_type(headers) == ""


def test_parse_frames_yields_single_frame():
    frame = _make_event_stream_frame("toolUseEvent", {"foo": 1})
    buf = bytearray(frame)
    out = list(ar._parse_frames(buf))
    assert out == [("toolUseEvent", {"foo": 1})]
    assert len(buf) == 0  # consumed


def test_parse_frames_handles_chunked_input():
    # split a frame across two reads
    frame = _make_event_stream_frame("messageMetadataEvent", {"x": "y"})
    buf = bytearray(frame[:10])
    assert list(ar._parse_frames(buf)) == []  # not enough yet
    buf.extend(frame[10:])
    out = list(ar._parse_frames(buf))
    assert out == [("messageMetadataEvent", {"x": "y"})]


def test_parse_frames_two_frames_in_one_buffer():
    f1 = _make_event_stream_frame("a", {"i": 1})
    f2 = _make_event_stream_frame("b", {"i": 2})
    buf = bytearray(f1 + f2)
    out = list(ar._parse_frames(buf))
    assert [t for t, _ in out] == ["a", "b"]
    assert [p["i"] for _, p in out] == [1, 2]


def test_parse_frames_rejects_oversized_prelude():
    # Forge a total_len that exceeds 16MB cap
    bad = struct.pack(">II", 17 * 1024 * 1024, 4) + b"\x00" * 100
    buf = bytearray(bad)
    out = list(ar._parse_frames(buf))
    assert out == []
    assert len(buf) == 0  # cleared on the corruption path


def test_parse_frames_invalid_payload_yields_none():
    # Build a frame with garbage JSON payload
    name = b":event-type"
    val = b"weird"
    headers = bytes([len(name)]) + name + bytes([7]) + struct.pack(">H", len(val)) + val
    body = b"not-json"
    total_len = 12 + len(headers) + len(body) + 4
    prelude = struct.pack(">II", total_len, len(headers)) + b"\x00\x00\x00\x00"
    crc = b"\x00\x00\x00\x00"
    frame = prelude + headers + body + crc
    buf = bytearray(frame)
    out = list(ar._parse_frames(buf))
    assert out == [("weird", None)]


# ---------- _build_system_prompt ----------


def test_build_system_prompt_lists_tools():
    sp = ar._build_system_prompt()
    assert isinstance(sp, str)
    assert len(sp) > 100
    # Should mention the tools list section
    assert "tool" in sp.lower()


# ---------- _maybe_diff smoke ----------


def test_maybe_diff_no_backup_returns_none(tmp_path):
    d, lines = ar._maybe_diff("fs_write", {"path": str(tmp_path / "x.py"), "command": "create"}, backup_path=None)
    assert d is None
    assert lines == 0


def test_maybe_diff_with_backup(tmp_path):
    target = tmp_path / "x.py"
    target.write_text("new\nlines\n")
    bak = tmp_path / "x.py.bak"
    bak.write_text("old\nlines\n")
    d, n = ar._maybe_diff(
        "fs_write",
        {"path": str(target), "command": "replace"},
        backup_path=str(bak),
    )
    assert d is not None
    assert "-old" in d and "+new" in d
    assert n >= 1
