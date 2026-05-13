"""Regression: no-argument tool calls were dropped because Q never sends stop=True."""

from llm.q_provider import _ToolAccumulator


def test_flush_no_arg_tool_when_stop_missing():
    acc = _ToolAccumulator()
    # First frame: name only
    assert acc.feed({"name": "self_status", "toolUseId": "t1"}) is None
    # Second frame: empty input, no stop
    assert acc.feed({"input": "", "name": "self_status", "toolUseId": "t1"}) is None
    # End-of-stream flush should emit the call
    out = acc.flush_remaining()
    assert len(out) == 1
    assert out[0].id == "t1"
    assert out[0].name == "self_status"
    assert out[0].arguments == {}


def test_stop_frame_still_works_and_flush_is_idempotent():
    acc = _ToolAccumulator()
    acc.feed({"name": "fs_read", "toolUseId": "t2"})
    acc.feed({"input": '{"path":"/x"}', "toolUseId": "t2"})
    tc = acc.feed({"stop": True, "toolUseId": "t2"})
    assert tc is not None
    assert tc.name == "fs_read"
    assert tc.arguments == {"path": "/x"}
    # already-closed slot must not re-emit
    assert acc.flush_remaining() == []


def test_flush_ignores_slots_with_no_name():
    acc = _ToolAccumulator()
    # bare toolUseId with nothing else (shouldn't happen but be defensive)
    acc.feed({"toolUseId": "t3", "input": "..."})
    assert acc.flush_remaining() == []
