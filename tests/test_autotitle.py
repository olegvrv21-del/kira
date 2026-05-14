"""Tests for auto-title generation (agent_titler)."""
import asyncio


import agent_titler


# ----- _build_exchange / extract helpers -----

def _user_msg(text: str) -> dict:
    return {"userInputMessage": {"content": f"--- USER MESSAGE BEGIN ---\n{text}\n--- USER MESSAGE END ---"}}


def _assistant_msg(text: str) -> dict:
    return {"assistantResponseMessage": {"content": text}}


def test_build_exchange_basic():
    hist = [_user_msg("hello there"), _assistant_msg("hi back")]
    out = agent_titler._build_exchange(hist)
    assert "USER: hello there" in out
    assert "ASSISTANT: hi back" in out


def test_build_exchange_no_user_returns_none():
    assert agent_titler._build_exchange([]) is None
    assert agent_titler._build_exchange([_assistant_msg("orphan")]) is None


def test_build_exchange_skips_tool_results():
    tool_turn = {"userInputMessage": {
        "content": "...",
        "userInputMessageContext": {"toolResults": [{"x": 1}]},
    }}
    hist = [tool_turn, _user_msg("real prompt"), _assistant_msg("real reply")]
    out = agent_titler._build_exchange(hist)
    assert "real prompt" in out
    assert "real reply" in out


def test_build_exchange_user_only_no_assistant():
    hist = [_user_msg("solo question")]
    out = agent_titler._build_exchange(hist)
    assert out.startswith("USER: solo question")
    assert "ASSISTANT" not in out


def test_build_exchange_truncates_long_text():
    long_q = "x" * 5000
    hist = [_user_msg(long_q), _assistant_msg("y" * 5000)]
    out = agent_titler._build_exchange(hist)
    assert len(out) <= agent_titler._MAX_EXCHANGE_CHARS + 100


def test_extract_assistant_text_handles_list_content():
    am = {"content": [{"text": "part one"}, {"text": "part two"}, "trailing"]}
    out = agent_titler._extract_assistant_text(am)
    assert "part one" in out and "part two" in out and "trailing" in out


# ----- _clean -----

def test_clean_strips_quotes_and_punct():
    assert agent_titler._clean('"Привет мир."') == "Привет мир"
    assert agent_titler._clean("«Hello World!»") == "Hello World"


def test_clean_strips_title_prefix():
    assert agent_titler._clean("Title: Bug Fix Session") == "Bug Fix Session"
    assert agent_titler._clean("Заголовок: Починили баг") == "Починили баг"
    assert agent_titler._clean("Название - Тест") == "Тест"


def test_clean_keeps_first_line():
    assert agent_titler._clean("Line one\nLine two") == "Line one"


def test_clean_caps_length():
    long = "a" * 200
    out = agent_titler._clean(long)
    assert len(out) <= agent_titler._MAX_TITLE_LEN


def test_clean_collapses_whitespace():
    assert agent_titler._clean("  too   many   spaces  ") == "too many spaces"


# ----- should_retitle -----

def test_should_retitle_empty(monkeypatch):
    monkeypatch.delenv("KIRA_AUTOTITLE", raising=False)
    hist = [_user_msg("какой-то вопрос")]
    assert agent_titler.should_retitle(None, hist) is True
    assert agent_titler.should_retitle("", hist) is True


def test_should_retitle_when_matches_derive(monkeypatch):
    monkeypatch.delenv("KIRA_AUTOTITLE", raising=False)
    hist = [_user_msg("какой-то вопрос")]
    import agent_store
    derived = agent_store.derive_title(hist)
    assert agent_titler.should_retitle(derived, hist) is True


def test_should_not_retitle_when_user_renamed(monkeypatch):
    monkeypatch.delenv("KIRA_AUTOTITLE", raising=False)
    hist = [_user_msg("какой-то вопрос")]
    assert agent_titler.should_retitle("My Custom Title", hist) is False


def test_should_not_retitle_when_disabled(monkeypatch):
    monkeypatch.setenv("KIRA_AUTOTITLE", "0")
    hist = [_user_msg("какой-то вопрос")]
    assert agent_titler.should_retitle(None, hist) is False


def test_should_not_retitle_when_no_history(monkeypatch):
    monkeypatch.delenv("KIRA_AUTOTITLE", raising=False)
    assert agent_titler.should_retitle(None, []) is False


# ----- propose_title (async) -----


async def _fake_llm(api_key, prompt, model, system=None, max_tokens=None):
    return "Починили баг в guardrails"


async def _fake_llm_error(api_key, prompt, model, system=None, max_tokens=None):
    return "[llm_one_shot error] Boom"


async def _fake_llm_empty(api_key, prompt, model, system=None, max_tokens=None):
    return ""


async def _fake_llm_raises(api_key, prompt, model, system=None, max_tokens=None):
    raise RuntimeError("net down")


async def _fake_llm_with_prefix(api_key, prompt, model, system=None, max_tokens=None):
    return 'Title: "Подсчёт юнит-тестов"'


def test_propose_title_happy_path(monkeypatch):
    monkeypatch.delenv("KIRA_AUTOTITLE", raising=False)
    hist = [_user_msg("привет"), _assistant_msg("здравствуй")]
    out = asyncio.run(agent_titler.propose_title(hist, _fake_llm))
    assert out == "Починили баг в guardrails"


def test_propose_title_returns_none_on_error(monkeypatch):
    monkeypatch.delenv("KIRA_AUTOTITLE", raising=False)
    hist = [_user_msg("привет"), _assistant_msg("здравствуй")]
    assert asyncio.run(agent_titler.propose_title(hist, _fake_llm_error)) is None
    assert asyncio.run(agent_titler.propose_title(hist, _fake_llm_empty)) is None
    assert asyncio.run(agent_titler.propose_title(hist, _fake_llm_raises)) is None


def test_propose_title_strips_prefix_and_quotes(monkeypatch):
    monkeypatch.delenv("KIRA_AUTOTITLE", raising=False)
    hist = [_user_msg("q"), _assistant_msg("a")]
    out = asyncio.run(agent_titler.propose_title(hist, _fake_llm_with_prefix))
    assert out == "Подсчёт юнит-тестов"


def test_propose_title_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("KIRA_AUTOTITLE", "0")
    hist = [_user_msg("q"), _assistant_msg("a")]
    out = asyncio.run(agent_titler.propose_title(hist, _fake_llm))
    assert out is None


def test_propose_title_no_history_returns_none(monkeypatch):
    monkeypatch.delenv("KIRA_AUTOTITLE", raising=False)
    out = asyncio.run(agent_titler.propose_title([], _fake_llm))
    assert out is None


def test_propose_title_too_short_returns_none(monkeypatch):
    async def _short(api_key, prompt, model, system=None, max_tokens=None):
        return "Hi"
    monkeypatch.delenv("KIRA_AUTOTITLE", raising=False)
    hist = [_user_msg("q"), _assistant_msg("a")]
    out = asyncio.run(agent_titler.propose_title(hist, _short))
    assert out is None
