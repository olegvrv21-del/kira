"""Phase 3c.2: bidirectional history conversion + run_agent body building.

The runtime now keeps a canonical Message[] in lockstep with the Q-dict
history (for DB back-compat). These tests verify the conversion is
loss-free for the dict shapes we actually see in production.
"""

import pytest

from llm.base import Message, ToolCall
from llm.q_provider import (
    USER_MSG_BEGIN,
    USER_MSG_END,
    messages_to_q_history,
    q_history_to_messages,
    wrap_user_text,
)


def _make_history():
    """A representative Q-dict history covering: system prompt, wrapped user
    turn, assistant w/ tool_uses, user w/ tool_results, plain assistant."""
    return [
        # System prompt as the very first user turn (no markers)
        {
            "userInputMessage": {
                "content": "You are Kira.",
                "userInputMessageContext": {"envState": {"operatingSystem": "linux"}},
                "origin": "KIRO_CLI",
            }
        },
        # Wrapped user turn
        {
            "userInputMessage": {
                "content": wrap_user_text("hello"),
                "userInputMessageContext": {},
                "origin": "KIRO_CLI",
            }
        },
        # Assistant with a tool call
        {
            "assistantResponseMessage": {
                "messageId": "msg_a1",
                "content": "Sure, let me run that.",
                "toolUses": [
                    {"toolUseId": "tu_1", "name": "execute_bash", "input": {"command": "ls"}}
                ],
            }
        },
        # User turn carrying tool_results
        {
            "userInputMessage": {
                "content": "",
                "userInputMessageContext": {
                    "toolResults": [
                        {
                            "toolUseId": "tu_1",
                            "content": [{"text": "file1\nfile2"}],
                            "status": "success",
                        }
                    ]
                },
                "origin": "KIRO_CLI",
            }
        },
        # Plain assistant response, no tools
        {
            "assistantResponseMessage": {
                "messageId": "msg_a2",
                "content": "Done.",
                "toolUses": [],
            }
        },
    ]


def test_q_history_to_messages_basic_shape():
    msgs = q_history_to_messages(_make_history())
    roles = [m.role for m in msgs]
    # system, user, assistant, tool, user(empty), assistant
    assert roles == ["system", "user", "assistant", "tool", "user", "assistant"]
    assert msgs[0].content == "You are Kira."
    assert msgs[1].content == "hello"  # markers stripped
    assert msgs[2].name == "msg_a1"
    assert len(msgs[2].tool_calls) == 1
    assert msgs[2].tool_calls[0].id == "tu_1"
    assert msgs[2].tool_calls[0].arguments == {"command": "ls"}
    assert msgs[3].tool_call_id == "tu_1"
    assert msgs[3].content == "file1\nfile2"
    assert msgs[5].content == "Done."


def test_round_trip_messages_to_q_history():
    original = _make_history()
    msgs = q_history_to_messages(original)
    # Round-trip back. The user turn that originally had wrapped text gets
    # re-wrapped via wrap_text=True.
    back = messages_to_q_history(msgs, wrap_text=True)
    # System turn content must survive verbatim
    assert back[0]["userInputMessage"]["content"] == "You are Kira."
    # Wrapped user turn must contain the markers again
    assert USER_MSG_BEGIN in back[1]["userInputMessage"]["content"]
    assert USER_MSG_END in back[1]["userInputMessage"]["content"]
    # Assistant tool_use preserved
    arm = back[2]["assistantResponseMessage"]
    assert arm["toolUses"][0]["toolUseId"] == "tu_1"
    assert arm["toolUses"][0]["input"] == {"command": "ls"}
    # Tool result attached to next user turn
    tr = back[3]["userInputMessage"]["userInputMessageContext"]["toolResults"]
    assert tr[0]["toolUseId"] == "tu_1"
    assert tr[0]["content"][0]["text"] == "file1\nfile2"
    # Final assistant response preserved
    assert back[4]["assistantResponseMessage"]["content"] == "Done."


def test_empty_history_returns_empty():
    assert q_history_to_messages([]) == []
    assert messages_to_q_history([]) == []


def test_messages_to_q_history_trailing_tool_results():
    """Tool results without a following user turn get attached to a synthetic empty user."""
    msgs = [
        Message(role="assistant", content="run it", tool_calls=[ToolCall(id="t1", name="x", arguments={})]),
        Message(role="tool", content="ok", tool_call_id="t1", name="success"),
    ]
    out = messages_to_q_history(msgs)
    assert out[-1]["userInputMessage"]["content"] == ""
    assert out[-1]["userInputMessage"]["userInputMessageContext"]["toolResults"][0]["toolUseId"] == "t1"


def test_unwrap_handles_missing_markers():
    """Old / synthetic content without markers still produces a Message."""
    h = [
        {
            "userInputMessage": {
                "content": "plain text no markers",
                "userInputMessageContext": {},
            }
        }
    ]
    msgs = q_history_to_messages(h)
    # First turn -> system role (per the runtime's convention)
    assert msgs[0].role == "system"
    assert msgs[0].content == "plain text no markers"


def test_assistant_round_trip_preserves_message_id():
    msgs = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello", name="msg_42"),
    ]
    back = messages_to_q_history(msgs, wrap_text=False)
    again = q_history_to_messages(back)
    assert again[-1].name == "msg_42"


def test_wrap_user_text_format():
    s = wrap_user_text("hello world")
    assert "--- CONTEXT ENTRY BEGIN ---" in s
    assert s.endswith("hello world--- USER MESSAGE END ---")
    assert wrap_user_text("") == ""
