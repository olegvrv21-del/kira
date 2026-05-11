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
