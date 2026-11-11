import os

import pytest

from hetai.bot.handlers import start_reply_plan
from hetai.bot.runtime import build_dispatcher, registered_router_names, reset_runtime
from hetai.config import Settings, reset_settings_cache


def test_production_fail_closed_without_secrets(monkeypatch):
    monkeypatch.setenv("HETAI_MODE", "production")
    monkeypatch.setenv("HETAI_DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    monkeypatch.delenv("HETAI_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("HETAI_ADMIN_IDS", raising=False)
    monkeypatch.delenv("HETAI_SESSION_SECRET", raising=False)
    monkeypatch.delenv("HETAI_BOT_TOKEN", raising=False)
    reset_settings_cache()
    with pytest.raises(RuntimeError):
        Settings().validate_boot()


def test_production_rejects_sqlite(monkeypatch):
    monkeypatch.setenv("HETAI_MODE", "production")
    monkeypatch.setenv("HETAI_DATABASE_URL", "sqlite+aiosqlite:///./x.db")
    monkeypatch.setenv("HETAI_WEBHOOK_SECRET", "a-very-long-secret")
    monkeypatch.setenv("HETAI_ADMIN_IDS", "1")
    monkeypatch.setenv("HETAI_SESSION_SECRET", "a-sufficiently-long-session-secret")
    monkeypatch.setenv("HETAI_BOT_TOKEN", "123:ABC")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="SQLite"):
        Settings().validate_boot()


def test_demo_rejects_non_loopback_bind(monkeypatch):
    monkeypatch.setenv("HETAI_MODE", "demo")
    monkeypatch.setenv("HETAI_HOST", "0.0.0.0")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="回环"):
        Settings().validate_boot()


def test_single_dispatcher_registers_unified_handlers():
    reset_runtime()
    dp = build_dispatcher()
    names = registered_router_names()
    assert names == ["start", "help", "menu", "chat_member"]
    assert dp is build_dispatcher()  # same instance
    assert len(dp.sub_routers) == 4


def test_start_plan_points_to_same_app(monkeypatch):
    monkeypatch.setenv("HETAI_MODE", "demo")
    monkeypatch.setenv("HETAI_HOST", "127.0.0.1")
    monkeypatch.setenv("HETAI_PORT", "8765")
    monkeypatch.delenv("HETAI_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HETAI_WEBAPP_URL", raising=False)
    reset_settings_cache()
    plan = start_reply_plan(Settings(), is_admin=True)
    assert plan["single_bot"] is True
    assert any(b.get("url", "").rstrip("/").endswith("/app") or "/app/" in b.get("url", "") for b in plan["buttons"])
    assert any("/admin" in b.get("url", "") for b in plan["buttons"])
    assert "未连接真实 Telegram Bot" in plan["text"]


def test_transport_defaults(monkeypatch):
    monkeypatch.setenv("HETAI_MODE", "demo")
    monkeypatch.setenv("HETAI_HOST", "127.0.0.1")
    monkeypatch.setenv("HETAI_BOT_TRANSPORT", "live")
    reset_settings_cache()
    assert Settings().resolved_transport == "local"
    assert Settings().outbound_live is False

    monkeypatch.setenv("HETAI_MODE", "production")
    monkeypatch.setenv("HETAI_DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    monkeypatch.setenv("HETAI_WEBHOOK_SECRET", "a-very-long-secret")
    monkeypatch.setenv("HETAI_ADMIN_IDS", "1")
    monkeypatch.setenv("HETAI_SESSION_SECRET", "a-sufficiently-long-session-secret")
    monkeypatch.setenv("HETAI_BOT_TOKEN", "123:ABC")
    monkeypatch.delenv("HETAI_BOT_TRANSPORT", raising=False)
    reset_settings_cache()
    st = Settings()
    st.validate_boot()
    assert st.resolved_transport == "dry-run"
    assert st.outbound_live is False


def test_production_live_requires_real_token(monkeypatch):
    monkeypatch.setenv("HETAI_MODE", "production")
    monkeypatch.setenv("HETAI_DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    monkeypatch.setenv("HETAI_WEBHOOK_SECRET", "a-very-long-secret")
    monkeypatch.setenv("HETAI_ADMIN_IDS", "1")
    monkeypatch.setenv("HETAI_SESSION_SECRET", "a-sufficiently-long-session-secret")
    monkeypatch.setenv("HETAI_BOT_TOKEN", "0:local-no-send")
    monkeypatch.setenv("HETAI_BOT_TRANSPORT", "live")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="live"):
        Settings().validate_boot()
