"""Test config: isolate SQLite, disable sandbox, fake Kiro key."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Point agent_store at a temp DB BEFORE importing app.
_tmp_db = Path(tempfile.mkdtemp(prefix="kira-test-")) / "agent_sessions.db"
os.environ.setdefault("KIRO_API_KEY", "ksk_test_fake")
os.environ["KIRA_SANDBOX"] = "0"
os.environ["KIRA_NO_AUTOSTART"] = "1"

import agent_store  # noqa: E402

agent_store.DB_PATH = _tmp_db
agent_store.init()


@pytest.fixture
def store():
    return agent_store


@pytest.fixture
def app_client():
    from fastapi.testclient import TestClient

    import app as app_mod

    return TestClient(app_mod.app)


# ---------- llm/ provider helpers ----------
#
# These fixtures swap the live provider for an in-memory MockProvider so tests
# don't go anywhere near q_client / Bedrock. The goal is a provider-agnostic
# way to script the agent loop without touching vendor wire format.
#
# Use `mock_llm` to script the response(s) — pass a list of stream events,
# a list-of-lists (one inner list per turn), or a callable taking the
# messages list and returning the script.
#
# Use `assert_no_q_client` to poison q_client.stream_q for the duration of
# the test, so any accidental fall-through to the legacy Q path fails loudly.


@pytest.fixture
def mock_llm(monkeypatch):
    """Factory: install a MockProvider for the duration of the test.

        def test_x(mock_llm):
            p = mock_llm([{"type": "text", "text": "hi"}])
            ...

    Per-turn scripts:

            p = mock_llm([
                [{"type": "tool_call", "id": "t1", "name": "fs_read", "args": {}}],
                [{"type": "text", "text": "done"}],
            ])

    Dynamic script (sees the messages list, decides on the fly):

            p = mock_llm(lambda msgs: [...])
    """
    from llm import MockProvider, register

    installed: dict = {}

    def _install(script, *, models=None):
        if isinstance(script, list) and script and isinstance(script[0], list):
            turn = {"n": 0}

            def _multi(_msgs, _turns=script, _turn=turn):
                i = min(_turn["n"], len(_turns) - 1)
                _turn["n"] += 1
                return _turns[i]

            real_script = _multi
        else:
            real_script = script
        provider = MockProvider(script=real_script, models=models or ["mock-1"])
        register("mock", lambda _p=provider: _p)
        monkeypatch.setenv("KIRA_LLM_PROVIDER", "mock")
        installed["provider"] = provider
        return provider

    yield _install


@pytest.fixture
def assert_no_q_client(monkeypatch):
    """Poison q_client.stream_q so any code path that reaches it fails loudly.

    Use together with `mock_llm` to prove a code path is fully migrated to
    the llm/ abstraction. Regression guard for Phase 3a/b/c/d.
    """
    import q_client

    async def _poison(*_a, **_kw):
        raise AssertionError(
            "q_client.stream_q was called — code path is NOT going through "
            "the llm/ provider abstraction. This indicates a Phase 3 regression."
        )
        yield  # pragma: no cover

    monkeypatch.setattr(q_client, "stream_q", _poison)
    yield
