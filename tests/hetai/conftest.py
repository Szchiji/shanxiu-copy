import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["HETAI_PYTEST"] = "1"
os.environ["HETAI_MODE"] = "demo"
os.environ["HETAI_HOST"] = "127.0.0.1"
os.environ["HETAI_RUN_SCHEDULER"] = "false"
os.environ["HETAI_SEED_DEMO"] = "true"
os.environ["HETAI_WEBHOOK_SECRET"] = "demo-local-only"
os.environ.pop("HETAI_BOT_TOKEN", None)


@pytest.fixture
def demo_client(tmp_path, monkeypatch):
    db = tmp_path / "hetai-test.sqlite"
    monkeypatch.setenv("HETAI_MODE", "demo")
    monkeypatch.setenv("HETAI_HOST", "127.0.0.1")
    monkeypatch.setenv("HETAI_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("HETAI_WEBHOOK_SECRET", "demo-local-only")
    monkeypatch.setenv("HETAI_SEED_DEMO", "true")
    monkeypatch.setenv("HETAI_RUN_SCHEDULER", "false")
    monkeypatch.setenv("HETAI_PYTEST", "1")
    from hetai.config import reset_settings_cache
    from hetai.bot.runtime import reset_runtime

    reset_settings_cache()
    reset_runtime()
    from hetai.app import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        yield client
    reset_settings_cache()
    reset_runtime()
