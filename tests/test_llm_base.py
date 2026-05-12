"""Tests for llm/base.py — the dependency-free part of the provider layer."""

from __future__ import annotations

import pytest

from llm.base import (
    LLMProvider,
    Message,
    StreamEvent,
    ToolCall,
    ToolSpec,
    Usage,
    message_from_dict,
    toolspecs_from_openai_json,
)


class TestMessage:
    def test_defaults(self):
        m = Message(role="user", content="hi")
        assert m.role == "user"
        assert m.content == "hi"
        assert m.tool_calls == []
        assert m.tool_call_id is None

    def test_with_tool_calls(self):
        tc = ToolCall(id="t1", name="fs_read", arguments={"path": "/x"})
        m = Message(role="assistant", content="", tool_calls=[tc])
        assert m.tool_calls[0].name == "fs_read"


class TestMessageFromDict:
    def test_plain(self):
        m = message_from_dict({"role": "user", "content": "hi"})
        assert m.role == "user"
        assert m.content == "hi"
        assert m.tool_calls == []

    def test_with_tool_calls_dict_args(self):
        m = message_from_dict(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t1", "name": "grep", "arguments": {"q": "x"}}],
            }
        )
        assert len(m.tool_calls) == 1
        assert m.tool_calls[0].id == "t1"
        assert m.tool_calls[0].arguments == {"q": "x"}

    def test_with_tool_calls_string_args(self):
        m = message_from_dict(
            {
                "role": "assistant",
                "tool_calls": [{"id": "t1", "name": "grep", "arguments": '{"q": "x"}'}],
            }
        )
        assert m.tool_calls[0].arguments == {"q": "x"}

    def test_with_unparseable_string_args(self):
        m = message_from_dict(
            {"role": "assistant", "tool_calls": [{"id": "t1", "name": "x", "arguments": "not json"}]}
        )
        assert m.tool_calls[0].arguments == {"_raw": "not json"}

    def test_tool_use_id_alias(self):
        m = message_from_dict(
            {"role": "assistant", "tool_calls": [{"tool_use_id": "alt", "name": "x", "input": {"a": 1}}]}
        )
        assert m.tool_calls[0].id == "alt"
        assert m.tool_calls[0].arguments == {"a": 1}

    def test_tool_result_role(self):
        m = message_from_dict(
            {"role": "tool", "content": "output", "tool_call_id": "t1", "name": "fs_read"}
        )
        assert m.role == "tool"
        assert m.tool_call_id == "t1"
        assert m.name == "fs_read"


class TestToolspecsParsing:
    def test_openai_function_shape(self):
        specs = toolspecs_from_openai_json(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "fs_read",
                        "description": "Read a file",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ]
        )
        assert len(specs) == 1
        assert specs[0].name == "fs_read"
        assert "properties" in specs[0].parameters

    def test_bare_shape(self):
        specs = toolspecs_from_openai_json(
            [{"name": "grep", "description": "x", "parameters": {"type": "object"}}]
        )
        assert specs[0].name == "grep"

    def test_tool_spec_wrapper(self):
        specs = toolspecs_from_openai_json(
            [
                {
                    "toolSpec": {
                        "name": "git",
                        "description": "run git",
                        "inputSchema": {"json": {"type": "object"}},
                    }
                }
            ]
        )
        assert specs[0].name == "git"
        assert specs[0].parameters == {"type": "object"}

    def test_skips_malformed(self):
        specs = toolspecs_from_openai_json([{"junk": 1}, {"name": "ok"}])
        assert [s.name for s in specs] == ["ok"]


class TestStreamEvent:
    def test_text(self):
        ev = StreamEvent(type="text", text="hi")
        assert ev.type == "text"
        assert ev.text == "hi"

    def test_done(self):
        assert StreamEvent(type="done").type == "done"

    def test_usage(self):
        ev = StreamEvent(type="usage", usage=Usage(input_tokens=10, output_tokens=5))
        assert ev.usage.input_tokens == 10


def test_protocol_runtime_check():
    """LLMProvider is @runtime_checkable — confirm with an inline impl."""

    class _Stub:
        name = "x"
        supported_models = []

        async def stream(self, *a, **kw):
            yield StreamEvent(type="done")

        async def health(self):
            return {}

    assert isinstance(_Stub(), LLMProvider)
