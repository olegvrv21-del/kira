"""Regression tests for CodeQL security alerts fixed in commit (path traversal, ReDoS)."""

import time

import pytest
from fastapi.testclient import TestClient

import agent_store
import app as app_mod


@pytest.fixture
def client():
    return TestClient(app_mod.app)


class TestPathTraversal:
    def test_agent_file_rejects_traversal(self, client):
        # Classic ../ escape attempt.
        r = client.get("/agent/file/abc123/../../../etc/passwd")
        assert r.status_code in (400, 404)

    def test_agent_file_rejects_bad_sid(self, client):
        r = client.get("/agent/file/..%2F..%2Fetc/passwd")
        assert r.status_code == 400

    def test_agent_file_rejects_absolute_sid(self, client):
        r = client.get("/agent/file//etc/passwd")
        # FastAPI may 404 the route or our validator may reject; either is fine.
        assert r.status_code in (400, 404)

    def test_agent_upload_rejects_bad_sid(self, client):
        r = client.post("/agent/upload/..%2Ffoo", files={"files": ("x.txt", b"hi")})
        assert r.status_code in (400, 404, 422)

    def test_safe_sid_validator(self):
        from agent_runtime import _SID_RE, _safe_sid

        # Good ids stay.
        assert _safe_sid("abcdef1234") == "abcdef1234"
        assert _SID_RE.match("a-b_c-1")
        # Bad ones are replaced with a fresh uuid.
        for bad in ("", "../etc", "a/b", "a b", "a\\b", "a" * 100, "\u202e"):
            sid = _safe_sid(bad)
            assert _SID_RE.match(sid), f"safe_sid leaked through for {bad!r}: {sid!r}"


class TestReDoSExtractUserText:
    def test_normal_extraction(self):
        s = "--- USER MESSAGE BEGIN ---\nhello world\n--- USER MESSAGE END ---"
        assert agent_store.extract_user_text(s) == "hello world"

    def test_no_match(self):
        assert agent_store.extract_user_text("just text") is None
        assert agent_store.extract_user_text(None) is None  # type: ignore[arg-type]

    def test_no_end_marker(self):
        # Old regex would search exhaustively; new code returns None fast.
        s = "--- USER MESSAGE BEGIN ---\n" + "a" * 50000
        t0 = time.perf_counter()
        assert agent_store.extract_user_text(s) is None
        dt = time.perf_counter() - t0
        # Should be trivial — linear scan, no backtracking. Generous bound.
        assert dt < 0.5, f"extract_user_text too slow: {dt:.3f}s"

    def test_redos_payload_doesnt_hang(self):
        # The CodeQL-flagged payload pattern.
        s = "--- USER MESSAGE BEGIN ---" + "a" * 200000
        t0 = time.perf_counter()
        agent_store.extract_user_text(s)
        dt = time.perf_counter() - t0
        assert dt < 0.5, f"ReDoS payload too slow: {dt:.3f}s"
