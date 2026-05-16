"""Critic tests — exercise the parser plus the llm/-routed review loop.

After the agent_critic → llm/ migration, network mocking goes through
`llm.register("amazon-q", lambda: MockProvider(...))` instead of patching
`q_client.stream_q`. The dedicated test_critic_via_mock_provider acts as a
regression guard: if anyone re-imports q_client into agent_critic the
KIRA_LLM_PROVIDER=mock route in test_review_diff_uses_provider_layer fails.
"""

import pytest

import agent_critic
from llm import MockProvider, register


def _register_mock(text: str) -> MockProvider:
    """Make KIRA_LLM_PROVIDER='amazon-q' actually return our MockProvider.

    review_diff() routes the 'amazon-q' name through QProvider directly, so
    we override the 'mock' factory and flip the env in the test instead.
    """
    p = MockProvider(script=[{"type": "text", "text": text}])
    register("mock", lambda: p)
    return p


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


def test_parse_block_empty_reason_does_not_crash():
    """Regression: whitespace-only REASON must not raise IndexError.

    Previously `parse_verdict` did `(...).splitlines()[0]` unconditionally;
    for a REASON line that is space/tab-only, `strip()` yields `""` whose
    `splitlines()` is `[]`, and `[0]` crashed the caller (agent_runtime's
    auto-critic path right before git_commit). The verdict must still be
    parsed as BLOCK and reason must be the empty string.
    """
    cases = [
        "VERDICT: BLOCK\nREASON: ",            # single trailing space
        "VERDICT: BLOCK\nREASON:   ",          # multiple trailing spaces
        "VERDICT: BLOCK\nREASON:\t",           # tab only
    ]
    for txt in cases:
        v = agent_critic.parse_verdict(txt)
        assert v["verdict"] == "BLOCK", txt
        assert v["reason"] == "", txt


@pytest.mark.asyncio
async def test_review_diff_empty_short_circuits():
    v = await agent_critic.review_diff("key", "", intent="x")
    assert v["verdict"] == "OK"
    assert v["reason"] == "empty diff"


@pytest.mark.asyncio
async def test_review_diff_mocked_block(monkeypatch):
    _register_mock("VERDICT: BLOCK\nREASON: looks bad\nISSUES:\n- nope")
    monkeypatch.setenv("KIRA_LLM_PROVIDER", "mock")
    v = await agent_critic.review_diff("key", "diff --git a/x b/x\n+secret\n", intent="add stuff")
    assert v["verdict"] == "BLOCK"
    assert v["reason"] == "looks bad"
    assert "nope" in v["issues"]


@pytest.mark.asyncio
async def test_review_diff_mocked_ok(monkeypatch):
    _register_mock("VERDICT: OK")
    monkeypatch.setenv("KIRA_LLM_PROVIDER", "mock")
    v = await agent_critic.review_diff("key", "diff --git a/x b/x\n+ok\n")
    assert v["verdict"] == "OK"


@pytest.mark.asyncio
async def test_review_diff_truncates(monkeypatch):
    """Long diffs must be truncated before being shipped to the provider.

    We inspect the canonical Message[] the provider receives and assert the
    truncation marker appears in the user turn.
    """
    p = _register_mock("VERDICT: OK")
    monkeypatch.setenv("KIRA_LLM_PROVIDER", "mock")
    huge = "+" + "a" * 200_000
    await agent_critic.review_diff("key", huge)
    assert p.calls, "provider was never called"
    user_msg = next(m for m in p.calls[-1]["messages"] if m.role == "user")
    assert "diff truncated" in user_msg.content


@pytest.mark.asyncio
async def test_review_diff_handles_exception(monkeypatch):
    """Provider exceptions become an advisory 'OK' verdict — the critic must
    never crash the commit path on transient backend errors."""
    from llm import register as _register

    class BoomProvider:
        name = "mock"
        supported_models = ["x"]

        async def stream(self, messages, tools, *, model, cancel=None, timeout=300, extra=None):
            raise RuntimeError("q down")
            yield  # pragma: no cover

        async def health(self):
            return {"name": self.name, "status": "ok"}

    _register("mock", lambda: BoomProvider())
    monkeypatch.setenv("KIRA_LLM_PROVIDER", "mock")
    v = await agent_critic.review_diff("key", "diff")
    assert v["verdict"] == "OK"
    assert any("critic-error" in i for i in v["issues"])


@pytest.mark.asyncio
async def test_review_diff_routes_through_provider_layer(monkeypatch):
    """Regression guard for the q_client → llm/ migration.

    If anyone re-imports q_client into agent_critic and bypasses the
    abstraction, this test fails because the MockProvider never gets
    called and we'd see an empty-output 'OK' instead of the scripted BLOCK.
    """
    p = _register_mock("VERDICT: BLOCK\nREASON: routed correctly")
    monkeypatch.setenv("KIRA_LLM_PROVIDER", "mock")
    v = await agent_critic.review_diff("key", "diff --git a/x b/x\n+y\n")
    assert v["verdict"] == "BLOCK"
    assert v["reason"] == "routed correctly"
    # And the mock recorded exactly one call with our system+user messages.
    assert len(p.calls) == 1
    roles = [m.role for m in p.calls[0]["messages"]]
    assert roles == ["system", "user"]


@pytest.mark.asyncio
async def test_critic_provider_override_decorrelates(monkeypatch):
    """KIRA_CRITIC_PROVIDER must beat KIRA_LLM_PROVIDER so the reviewer can
    run on a different model family than the author.

    Setup: main LLM is the *real* `amazon-q` (which would normally route
    through QProvider). We point the critic at `mock`. If the override
    works, the MockProvider is called and returns our scripted BLOCK; if
    the override is ignored, QProvider runs (and would fail or hit network).
    """
    p_mock = _register_mock("VERDICT: BLOCK\nREASON: caught by sibling provider")
    monkeypatch.setenv("KIRA_LLM_PROVIDER", "amazon-q")
    monkeypatch.setenv("KIRA_CRITIC_PROVIDER", "mock")
    agent_critic.reload_flags()
    v = await agent_critic.review_diff("key", "diff --git a/x b/x\n+y\n", intent="x")
    assert v["verdict"] == "BLOCK"
    assert v["reason"] == "caught by sibling provider"
    assert p_mock.calls, "override-routed provider was never called"


@pytest.mark.asyncio
async def test_critic_override_unset_falls_back_to_llm_provider(monkeypatch):
    """Without KIRA_CRITIC_PROVIDER set, the critic must follow KIRA_LLM_PROVIDER."""
    p_mock = _register_mock("VERDICT: OK")
    monkeypatch.setenv("KIRA_LLM_PROVIDER", "mock")
    monkeypatch.delenv("KIRA_CRITIC_PROVIDER", raising=False)
    agent_critic.reload_flags()
    v = await agent_critic.review_diff("key", "diff --git a/x b/x\n+y\n")
    assert v["verdict"] == "OK"
    assert p_mock.calls

