"""Tests for agent_summarizer."""
import asyncio

import agent_summarizer


def _M(role, content="", tool_calls=None, tool_call_id=None, name=None):
    from llm.base import Message
    return Message(role=role, content=content, tool_calls=tool_calls or [],
                   tool_call_id=tool_call_id, name=name)


def _TC(tid, name="foo", args=None):
    from llm.base import ToolCall
    return ToolCall(id=tid, name=name, arguments=args or {})


# ----- char estimation -----

def test_estimate_chars_simple():
    msgs = [_M("system", "abc"), _M("user", "hello"), _M("assistant", "hi")]
    assert agent_summarizer.estimate_chars(msgs) == 10


def test_estimate_chars_counts_tool_calls():
    tc = _TC("t1", name="execute_bash", args={"cmd": "ls -la"})
    msgs = [_M("assistant", "", tool_calls=[tc])]
    n = agent_summarizer.estimate_chars(msgs)
    assert n >= len("execute_bash")
    assert n >= len('{"cmd": "ls -la"}')


def test_estimate_chars_list_content():
    msgs = [_M("user", [{"text": "hello"}, {"text": " world"}])]
    assert agent_summarizer.estimate_chars(msgs) == len("hello world")


# ----- should_summarize -----

def test_should_summarize_below_threshold(monkeypatch):
    monkeypatch.delenv("KIRA_SUMMARIZE", raising=False)
    msgs = [_M("system", "s"), _M("user", "x" * 100), _M("assistant", "y"), _M("user", "z")]
    assert agent_summarizer.should_summarize(msgs, threshold=1000) is False


def test_should_summarize_above_threshold(monkeypatch):
    monkeypatch.delenv("KIRA_SUMMARIZE", raising=False)
    msgs = [_M("system", "s"), _M("user", "x" * 500), _M("assistant", "y" * 500),
            _M("user", "z" * 500), _M("assistant", "w" * 500)]
    assert agent_summarizer.should_summarize(msgs, threshold=1000) is True


def test_should_summarize_disabled(monkeypatch):
    monkeypatch.setenv("KIRA_SUMMARIZE", "0")
    msgs = [_M("system", "s")] + [_M("user", "x" * 10000)] * 5
    assert agent_summarizer.should_summarize(msgs) is False


def test_should_summarize_too_short(monkeypatch):
    monkeypatch.delenv("KIRA_SUMMARIZE", raising=False)
    msgs = [_M("system", "s"), _M("user", "x" * 100000)]
    assert agent_summarizer.should_summarize(msgs, threshold=1000) is False


# ----- pick_summarize_range -----

def test_pick_range_basic_no_tools():
    msgs = [_M("system", "s")] + [_M("user", f"u{i}") for i in range(10)]
    rng = agent_summarizer.pick_summarize_range(msgs, keep=3)
    assert rng == (1, 8)


def test_pick_range_too_few_messages():
    msgs = [_M("system", "s"), _M("user", "a"), _M("assistant", "b")]
    assert agent_summarizer.pick_summarize_range(msgs, keep=3) is None


def test_pick_range_no_system_prompt():
    msgs = [_M("user", "x"), _M("assistant", "y"), _M("user", "z"), _M("assistant", "w")]
    assert agent_summarizer.pick_summarize_range(msgs, keep=2) is None


def test_pick_range_keeps_tool_pair_together():
    # Layout: system, u, a, u, a(tool_use t1), tool(t1), u, a, u, a, u
    # keep=4 → naive hi = 11-4 = 7, which sits ON the tool result for t1.
    # The window [1:7] then contains assistant.tool_calls=[t1] but the
    # tool result is at index 5 — both inside, that's fine. But let's set
    # up the trickier case where the tool result sits AT hi.
    msgs = [
        _M("system", "s"),       # 0
        _M("user", "u1"),         # 1
        _M("assistant", "a1"),    # 2
        _M("user", "u2"),         # 3
        _M("assistant", "", tool_calls=[_TC("t1")]),  # 4
        _M("tool", "result", tool_call_id="t1"),       # 5
        _M("user", ""),           # 6
        _M("assistant", "a3"),    # 7  <- naive hi if keep=3, but [1:7] still OK
        _M("user", "u3"),         # 8
        _M("assistant", "a4"),    # 9
    ]
    rng = agent_summarizer.pick_summarize_range(msgs, keep=3)
    # Either (1, 7) — naive — or further. The key invariant: range must NOT
    # contain only one side of a tool pair.
    assert rng is not None
    lo, hi = rng
    opened_inside = set()
    for m in msgs[lo:hi]:
        for tc in (m.tool_calls or []):
            opened_inside.add(tc.id)
    for m in msgs[lo:hi]:
        if m.role == "tool" and m.tool_call_id:
            opened_inside.discard(m.tool_call_id)
    assert not opened_inside, "tool_use without matching tool_result inside range"


def test_pick_range_advances_hi_when_tool_pair_split():
    """Naive hi would split a tool pair; the range picker must walk hi forward."""
    msgs = [
        _M("system", "s"),       # 0
        _M("user", "u1"),         # 1
        _M("assistant", "", tool_calls=[_TC("t1")]),  # 2  <- tool_use here
        _M("user", "u2"),         # 3  (irrelevant filler between)
        _M("tool", "result", tool_call_id="t1"),       # 4  <- pair closed
        _M("user", "u3"),         # 5
        _M("assistant", "a"),     # 6
    ]
    # keep=4 → naive hi = 3, which leaves t1 open inside [1:3].
    # picker must extend hi until 5.
    rng = agent_summarizer.pick_summarize_range(msgs, keep=4)
    assert rng is not None
    lo, hi = rng
    assert lo == 1
    assert hi >= 5  # tool pair closed inside


def test_pick_range_returns_none_if_no_room_left():
    """If keep eats everything past the system prompt, no range."""
    msgs = [_M("system", "s"), _M("user", "u"), _M("assistant", "a")]
    assert agent_summarizer.pick_summarize_range(msgs, keep=5) is None


def test_pick_range_skips_tool_result_at_hi():
    """If hi points at a tool message whose pair was just summarized away."""
    msgs = [
        _M("system", "s"),
        _M("assistant", "", tool_calls=[_TC("t1")]),  # 1
        _M("tool", "r1", tool_call_id="t1"),           # 2
        _M("user", "u"),                               # 3
        _M("assistant", "a"),                          # 4
        _M("user", "u2"),                              # 5
    ]
    rng = agent_summarizer.pick_summarize_range(msgs, keep=3)
    # Either valid range that keeps invariant, or None.
    if rng is not None:
        lo, hi = rng
        opened = set()
        for m in msgs[lo:hi]:
            for tc in (m.tool_calls or []):
                opened.add(tc.id)
            if m.role == "tool" and m.tool_call_id:
                opened.discard(m.tool_call_id)
        assert not opened


# ----- summarize() integration -----

async def _fake_llm(api_key, prompt, model, system=None, max_tokens=None):
    return "summary text " * 10  # 110 chars > 30


async def _fake_llm_error(api_key, prompt, model, system=None, max_tokens=None):
    return "[llm_one_shot error] Boom"


async def _fake_llm_empty(api_key, prompt, model, system=None, max_tokens=None):
    return "(empty response)"


async def _fake_llm_short(api_key, prompt, model, system=None, max_tokens=None):
    return "ok"


def _big_msgs(n=10, payload=600):
    msgs = [_M("system", "sys prompt")]
    for i in range(n):
        msgs.append(_M("user" if i % 2 == 0 else "assistant", f"msg{i}: " + "x" * payload))
    return msgs


def test_summarize_happy_path(monkeypatch):
    monkeypatch.delenv("KIRA_SUMMARIZE", raising=False)
    monkeypatch.setenv("KIRA_SUMMARIZE_THRESHOLD", "1000")
    monkeypatch.setenv("KIRA_SUMMARIZE_KEEP", "3")
    msgs = _big_msgs(n=10, payload=200)
    before = len(msgs)
    did = asyncio.run(agent_summarizer.summarize(msgs, _fake_llm))
    assert did is True
    assert len(msgs) < before
    # First msg still system; new system summary message present at index 1.
    assert msgs[0].role == "system"
    assert "Earlier conversation summary" in msgs[1].content
    # Tail kept (last KEEP messages).
    assert msgs[-1].content.startswith("msg9")


def test_summarize_below_threshold_noop(monkeypatch):
    monkeypatch.delenv("KIRA_SUMMARIZE", raising=False)
    msgs = _big_msgs(n=4, payload=10)
    did = asyncio.run(agent_summarizer.summarize(msgs, _fake_llm, threshold=100000))
    assert did is False


def test_summarize_disabled(monkeypatch):
    monkeypatch.setenv("KIRA_SUMMARIZE", "0")
    msgs = _big_msgs(n=10, payload=200)
    did = asyncio.run(agent_summarizer.summarize(msgs, _fake_llm, threshold=1000))
    assert did is False


def test_summarize_llm_error_noop(monkeypatch):
    monkeypatch.delenv("KIRA_SUMMARIZE", raising=False)
    msgs = _big_msgs(n=10, payload=200)
    before = list(msgs)
    did = asyncio.run(agent_summarizer.summarize(msgs, _fake_llm_error, threshold=1000, keep=3))
    assert did is False
    assert msgs == before


def test_summarize_llm_empty_noop(monkeypatch):
    monkeypatch.delenv("KIRA_SUMMARIZE", raising=False)
    msgs = _big_msgs(n=10, payload=200)
    did = asyncio.run(agent_summarizer.summarize(msgs, _fake_llm_empty, threshold=1000, keep=3))
    assert did is False


def test_summarize_llm_short_output_noop(monkeypatch):
    monkeypatch.delenv("KIRA_SUMMARIZE", raising=False)
    msgs = _big_msgs(n=10, payload=200)
    did = asyncio.run(agent_summarizer.summarize(msgs, _fake_llm_short, threshold=1000, keep=3))
    assert did is False


def test_summarize_preserves_tool_pairs(monkeypatch):
    """If there's a tool_use right before the keep-tail, it must stay paired."""
    monkeypatch.delenv("KIRA_SUMMARIZE", raising=False)
    msgs = [_M("system", "s")]
    # fluff to push us over threshold
    for i in range(8):
        msgs.append(_M("user" if i % 2 == 0 else "assistant", "x" * 300))
    # tool pair right before tail
    msgs.append(_M("assistant", "", tool_calls=[_TC("t1", "bash", {"cmd": "ls"})]))
    msgs.append(_M("tool", "result", tool_call_id="t1", name="bash"))
    # tail
    msgs.append(_M("user", "final question"))
    msgs.append(_M("assistant", "final answer"))

    did = asyncio.run(agent_summarizer.summarize(msgs, _fake_llm, threshold=1000, keep=3))
    assert did is True
    # Invariant: across the final messages list, every tool_call_id has its pair.
    opened: set[str] = set()
    for m in msgs:
        for tc in (m.tool_calls or []):
            opened.add(tc.id)
        if m.role == "tool" and m.tool_call_id:
            opened.discard(m.tool_call_id)
    assert not opened, "tool_use without matching tool_result after summarization"


def test_summarize_returns_false_when_no_valid_range(monkeypatch):
    """When tool pair runs to the end, no valid summarize range exists."""
    monkeypatch.delenv("KIRA_SUMMARIZE", raising=False)
    msgs = [_M("system", "s" * 5000)]
    msgs.append(_M("user", "u" * 5000))
    msgs.append(_M("assistant", "", tool_calls=[_TC("t1")]))
    msgs.append(_M("tool", "r", tool_call_id="t1"))
    # No tail beyond the open pair past keep boundary.
    did = asyncio.run(agent_summarizer.summarize(msgs, _fake_llm, threshold=1000, keep=10))
    assert did is False


def test_build_summary_prompt_includes_facts():
    msgs = [
        _M("system", "s"),
        _M("user", "fix bug in agent_store.py at SHA abc1234"),
        _M("assistant", "I'll check it"),
    ]
    out = agent_summarizer.build_summary_prompt(msgs, 1, 3)
    assert "agent_store.py" in out
    assert "abc1234" in out
    assert "USER:" in out
    assert "ASSISTANT:" in out
