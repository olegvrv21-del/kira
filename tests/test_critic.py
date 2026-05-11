import asyncio
import pytest

import agent_critic


def test_parse_ok():
    v = agent_critic.parse_verdict("VERDICT: OK\nISSUES:\n- nitpick one")
    assert v["verdict"] == "OK"
    assert v["issues"] == ["nitpick one"]
    assert v["reason"] == ""


def test_parse_block():
    txt = "VERDICT: BLOCK\nREASON: secret leak\nISSUES:\n- key in env\n- TODO removed"
    v = agent_critic.parse_verdict(txt)
    assert v["verdict"] == "BLOCK"
    assert v["reason"] == "secret leak"
    assert v["issues"] == ["key in env", "TODO removed"]


def test_parse_missing_defaults_to_ok():
    v = agent_critic.parse_verdict("the diff looks fine to me")
    assert v["verdict"] == "OK"


def test_parse_lowercase_block():
    v = agent_critic.parse_verdict("verdict: block\nreason: bad")
    assert v["verdict"] == "BLOCK"


@pytest.mark.asyncio
async def test_review_diff_empty_short_circuits():
    v = await agent_critic.review_diff("key", "", intent="x")
    assert v["verdict"] == "OK"
    assert v["reason"] == "empty diff"


@pytest.mark.asyncio
async def test_review_diff_mocked_block(monkeypatch):
    async def fake_stream(api_key, body, **kw):
        yield ("assistantResponseEvent",
               {"content": "VERDICT: BLOCK\nREASON: looks bad\nISSUES:\n- nope"})
    monkeypatch.setattr(agent_critic.q_client, "stream_q", fake_stream)
    v = await agent_critic.review_diff("key", "diff --git a/x b/x\n+secret\n", intent="add stuff")
    assert v["verdict"] == "BLOCK"
    assert v["reason"] == "looks bad"
    assert "nope" in v["issues"]


@pytest.mark.asyncio
async def test_review_diff_mocked_ok(monkeypatch):
    async def fake_stream(api_key, body, **kw):
        yield ("assistantResponseEvent", {"content": "VERDICT: OK"})
    monkeypatch.setattr(agent_critic.q_client, "stream_q", fake_stream)
    v = await agent_critic.review_diff("key", "diff --git a/x b/x\n+ok\n")
    assert v["verdict"] == "OK"


@pytest.mark.asyncio
async def test_review_diff_truncates(monkeypatch):
    seen = {}
    async def fake_stream(api_key, body, **kw):
        seen["user"] = body["conversationState"]["currentMessage"]["userInputMessage"]["content"]
        yield ("assistantResponseEvent", {"content": "VERDICT: OK"})
    monkeypatch.setattr(agent_critic.q_client, "stream_q", fake_stream)
    huge = "+" + "a" * 200_000
    await agent_critic.review_diff("key", huge)
    assert "diff truncated" in seen["user"]


@pytest.mark.asyncio
async def test_review_diff_handles_exception(monkeypatch):
    async def fake_stream(api_key, body, **kw):
        raise RuntimeError("q down")
        yield None  # pragma: no cover
    monkeypatch.setattr(agent_critic.q_client, "stream_q", fake_stream)
    v = await agent_critic.review_diff("key", "diff")
    assert v["verdict"] == "OK"
    assert any("critic-error" in i for i in v["issues"])
