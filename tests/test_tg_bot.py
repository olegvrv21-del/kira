"""Tests for ops/tg_bot.py helpers (chunking + image detection).

These are pure-function tests; we don't exercise the long-poll loop
because it talks to Telegram.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(ROOT, "ops")


@pytest.fixture(scope="module")
def tg_bot():
    # Required envs so module-level os.environ["..."] doesn't blow up.
    os.environ.setdefault("KIRA_TG_BOT_TOKEN", "x")
    os.environ.setdefault("KIRA_TG_ALLOWED_USERS", "1")
    if OPS not in sys.path:
        sys.path.insert(0, OPS)
    import tg_bot as mod

    return mod


# ---------------------------------------------------------------------------
# split_into_chunks
# ---------------------------------------------------------------------------


class TestSplitIntoChunks:
    def test_short_passes_through(self, tg_bot):
        assert tg_bot.split_into_chunks("hi") == ["hi"]

    def test_empty(self, tg_bot):
        # split_into_chunks("") returns [""] (len 0 <= limit)
        assert tg_bot.split_into_chunks("") == [""]

    def test_hard_cut_no_separators(self, tg_bot):
        blob = "x" * 10000
        cs = tg_bot.split_into_chunks(blob, limit=3900)
        assert all(len(c) <= 3900 for c in cs)
        assert "".join(cs) == blob
        assert len(cs) >= 3

    def test_prefers_paragraph_break(self, tg_bot):
        para = "A" * 800 + "\n\n" + "B" * 800 + "\n\n" + "C" * 800
        cs = tg_bot.split_into_chunks(para, limit=1000)
        # Each chunk should end at a paragraph boundary (no fragments of A in C, etc.)
        assert len(cs) >= 2
        # First chunk should contain only A's (entire para)
        assert "B" not in cs[0]

    def test_prefers_newline_when_no_paragraph(self, tg_bot):
        s = ("line one is here\n" * 100)
        cs = tg_bot.split_into_chunks(s, limit=200)
        for c in cs:
            assert len(c) <= 200
        # Joined back (with the lstrip happening inside) gives equivalent text
        # — we test that each chunk ends on a line boundary rather than mid-word.
        for c in cs[:-1]:
            assert c.rstrip().endswith("here")

    def test_code_fence_balance(self, tg_bot):
        src = "intro\n\n```python\n" + ("print(x)\n" * 500) + "```\n\noutro"
        cs = tg_bot.split_into_chunks(src, limit=1500)
        for i, c in enumerate(cs):
            count = c.count("```")
            assert count % 2 == 0, f"unbalanced fences in chunk {i}: {count}"

    def test_no_fences_no_modification(self, tg_bot):
        s = "hello world. " * 500
        cs = tg_bot.split_into_chunks(s, limit=500)
        # No synthetic ``` markers added.
        for c in cs:
            assert "```" not in c


# ---------------------------------------------------------------------------
# detect_image_format
# ---------------------------------------------------------------------------


class TestDetectImageFormat:
    def test_png(self, tg_bot):
        assert tg_bot.detect_image_format(b"\x89PNG\r\n\x1a\n----") == "png"

    def test_jpeg(self, tg_bot):
        assert tg_bot.detect_image_format(b"\xff\xd8\xff\xe0----") == "jpeg"

    def test_gif87(self, tg_bot):
        assert tg_bot.detect_image_format(b"GIF87a\x00\x00") == "gif"

    def test_gif89(self, tg_bot):
        assert tg_bot.detect_image_format(b"GIF89a\x00\x00") == "gif"

    def test_webp(self, tg_bot):
        assert tg_bot.detect_image_format(b"RIFF\x00\x00\x00\x00WEBPextra") == "webp"

    def test_unknown_defaults_png(self, tg_bot):
        assert tg_bot.detect_image_format(b"who knows") == "png"


# ---------------------------------------------------------------------------
# parse_sse_line
# ---------------------------------------------------------------------------


class TestParseSseLine:
    def test_valid(self, tg_bot):
        assert tg_bot.parse_sse_line('data: {"type":"text","delta":"hi"}') == {
            "type": "text",
            "delta": "hi",
        }

    def test_non_data_line(self, tg_bot):
        assert tg_bot.parse_sse_line("event: foo") is None
        assert tg_bot.parse_sse_line("") is None

    def test_malformed_json(self, tg_bot):
        assert tg_bot.parse_sse_line("data: {not json}") is None

    def test_empty_payload(self, tg_bot):
        assert tg_bot.parse_sse_line("data: ") is None


# ---------------------------------------------------------------------------
# extract_voice_text gating
# ---------------------------------------------------------------------------


class TestVoiceGating:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_voice_field(self, tg_bot):
        assert await tg_bot.extract_voice_text(object(), {"text": "hi"}) is None

    @pytest.mark.asyncio
    async def test_returns_none_when_backend_disabled(self, tg_bot, monkeypatch):
        monkeypatch.setattr(tg_bot, "WHISPER_BACKEND", "")
        msg = {"voice": {"file_id": "x", "duration": 5}}
        assert await tg_bot.extract_voice_text(object(), msg) is None

    @pytest.mark.asyncio
    async def test_rejects_too_long(self, tg_bot, monkeypatch):
        monkeypatch.setattr(tg_bot, "WHISPER_BACKEND", "faster-whisper")
        monkeypatch.setattr(tg_bot, "MAX_AUDIO_SEC", 60)
        msg = {"voice": {"file_id": "x", "duration": 300}}
        res = await tg_bot.extract_voice_text(object(), msg)
        assert res is not None
        assert "too long" in res
