"""Staging isolation gate: offline only. No real getMe, Telegram, or production token reads."""

from __future__ import annotations

import asyncio
import os
from typing import Dict

import pytest

from bot.services import staging_guard as guard
from bot.services.staging_guard import StagingIsolationError

TEST_BOT_ID = "900000001"
TEST_TOKEN = "900000001:TESTTOKENxxxxxxxxxxxxxxxxxxxxxxxxxxx"
SECRET_URL = "postgresql+asyncpg://staging_user:super-secret-db-pass@db.example.test:5432/yycj_staging"

PROD_HOST = "web-production-46ed80.up.railway.app"
PROD_ENV_ID = "95d049a1-3156-4a57-8287-b3ddad73b7c2"
PROD_SERVICE_ID = "37c189b7-2717-4a7b-aee8-153d71f3ff37"


def _staging(**overrides) -> Dict[str, str]:
    env = {
        "ENV": "staging",
        "STAGING_ISOLATION": "1",
        "STAGING_CONFIRMED_ISOLATED": "1",
        "STAGING_DB_IS_ISOLATED": "1",
        "STAGING_REDIS_UNUSED": "1",
        "STAGING_DATA_IS_SAMPLE": "1",
        "STAGING_EXPECTED_BOT_ID": TEST_BOT_ID,
        "BOT_TOKEN": TEST_TOKEN,
        "WEBHOOK_HOST": "https://yycj-staging.example.test",
        "WEBAPP_URL": "https://yycj-staging.example.test/app",
        "DATABASE_URL": SECRET_URL,
        "REDIS_URL": "",
    }
    env.update(overrides)
    return env


def test_production_and_local_defaults_do_not_require_new_flags():
    guard.assert_startup_isolation({
        "ENV": "production",
        "BOT_TOKEN": TEST_TOKEN,
        "WEBHOOK_HOST": "https://{0}".format(PROD_HOST),
        "WEBAPP_URL": "https://{0}/app".format(PROD_HOST),
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_ENVIRONMENT_ID": PROD_ENV_ID,
        "RAILWAY_SERVICE_ID": PROD_SERVICE_ID,
    })
    guard.assert_startup_isolation({
        "ENV": "development",
        "BOT_TOKEN": TEST_TOKEN,
    })
    guard.assert_startup_isolation({})
    policy = guard.job_policy({"ENV": "production"})
    assert policy == {
        "staging": False,
        "db_jobs": True,
        "telegram_jobs": True,
        "bot_hooks": True,
        "demo_purge": True,
    }


def test_known_production_runtime_never_enters_staging_mode():
    """A mistaken ENV=staging / STAGING_* on production must keep the production path."""
    env = {
        "ENV": "staging",
        "STAGING_ISOLATION": "1",
        "STAGING_CONFIRMED_ISOLATED": "1",
        "BOT_TOKEN": TEST_TOKEN,
        "WEBHOOK_HOST": "https://{0}".format(PROD_HOST),
        "WEBAPP_URL": "https://{0}/app".format(PROD_HOST),
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_ENVIRONMENT_ID": PROD_ENV_ID,
        "RAILWAY_SERVICE_ID": PROD_SERVICE_ID,
    }
    assert guard.on_known_production_runtime(env) is True
    assert guard.staging_requested(env) is False
    guard.assert_startup_isolation(env)
    assert guard.job_policy(env) == {
        "staging": False,
        "db_jobs": True,
        "telegram_jobs": True,
        "bot_hooks": True,
        "demo_purge": True,
    }
    # Each production identifier alone is enough.
    for key, value in (
        ("RAILWAY_ENVIRONMENT_NAME", "production"),
        ("RAILWAY_ENVIRONMENT_ID", PROD_ENV_ID),
        ("RAILWAY_SERVICE_ID", PROD_SERVICE_ID),
    ):
        one = {"ENV": "staging", "STAGING_ISOLATION": "1", key: value}
        assert guard.staging_requested(one) is False


def test_env_staging_alone_is_not_isolation():
    with pytest.raises(StagingIsolationError) as exc:
        guard.assert_startup_isolation({"ENV": "staging", "BOT_TOKEN": TEST_TOKEN})
    msg = str(exc.value)
    assert "ENV=staging 不能证明完全隔离" in msg
    assert "STAGING_ISOLATION=1" in msg
    assert TEST_TOKEN not in msg


def test_railway_staging_name_also_requires_explicit_flag():
    with pytest.raises(StagingIsolationError) as exc:
        guard.assert_startup_isolation({
            "ENV": "production",
            "RAILWAY_ENVIRONMENT_NAME": "staging",
            "BOT_TOKEN": TEST_TOKEN,
        })
    assert "STAGING_ISOLATION=1" in str(exc.value)


def test_confirmed_staging_config_passes_offline():
    guard.assert_startup_isolation(_staging())
    policy = guard.job_policy(_staging())
    assert policy["staging"] is True
    assert policy["db_jobs"] is False
    assert policy["telegram_jobs"] is False
    assert policy["bot_hooks"] is False
    assert policy["demo_purge"] is False


def test_opt_in_flags_enable_jobs_only_when_set():
    enabled = _staging(
        STAGING_ENABLE_DB_JOBS="1",
        STAGING_ENABLE_TELEGRAM_JOBS="1",
        STAGING_ENABLE_BOT_HOOKS="1",
        STAGING_ENABLE_DEMO_PURGE="1",
    )
    policy = guard.job_policy(enabled)
    assert policy["db_jobs"] and policy["telegram_jobs"] and policy["bot_hooks"] and policy["demo_purge"]


def test_token_must_match_expected_test_bot_id():
    with pytest.raises(StagingIsolationError) as exc:
        guard.assert_startup_isolation(_staging(STAGING_EXPECTED_BOT_ID="123456789"))
    msg = str(exc.value)
    assert "STAGING_EXPECTED_BOT_ID 不一致" in msg
    assert TEST_TOKEN not in msg
    assert "TESTTOKEN" not in msg


def test_bot_id_from_token_is_prefix_only():
    assert guard.bot_id_from_token(TEST_TOKEN) == 900000001
    assert guard.bot_id_from_token("not-a-token") is None
    assert guard.bot_id_from_token("") is None


def test_known_production_host_is_blocked_on_real_staging():
    cases = [
        _staging(WEBHOOK_HOST="https://{0}".format(PROD_HOST)),
        _staging(WEBAPP_URL="https://{0}/app".format(PROD_HOST)),
        _staging(
            STAGING_BLOCKED_HOSTS="other-prod.example.test",
            WEBHOOK_HOST="https://other-prod.example.test",
        ),
    ]
    for env in cases:
        assert guard.on_known_production_runtime(env) is False
        with pytest.raises(StagingIsolationError):
            guard.assert_startup_isolation(env)


def test_http_and_localhost_targets_rejected():
    with pytest.raises(StagingIsolationError) as exc:
        guard.assert_startup_isolation(_staging(WEBHOOK_HOST="http://yycj-staging.example.test"))
    assert "https://" in str(exc.value)
    with pytest.raises(StagingIsolationError) as exc:
        guard.assert_startup_isolation(_staging(WEBHOOK_HOST="https://localhost:8080"))
    assert "localhost" in str(exc.value)


def test_redis_declarations_are_consistent():
    with pytest.raises(StagingIsolationError):
        guard.assert_startup_isolation(_staging(STAGING_REDIS_UNUSED="", STAGING_REDIS_IS_ISOLATED=""))
    with pytest.raises(StagingIsolationError):
        guard.assert_startup_isolation(_staging(STAGING_REDIS_UNUSED="1", REDIS_URL="redis://example.test:6379"))
    with pytest.raises(StagingIsolationError):
        guard.assert_startup_isolation(_staging(STAGING_REDIS_UNUSED="", STAGING_REDIS_IS_ISOLATED="1", REDIS_URL=""))
    guard.assert_startup_isolation(_staging(
        STAGING_REDIS_UNUSED="",
        STAGING_REDIS_IS_ISOLATED="1",
        REDIS_URL="redis://staging-redis.example.test:6379",
    ))


def test_errors_never_include_secrets():
    with pytest.raises(StagingIsolationError) as exc:
        guard.assert_startup_isolation(_staging(
            STAGING_ISOLATION="",
            BOT_TOKEN=TEST_TOKEN,
            DATABASE_URL=SECRET_URL,
        ))
    msg = str(exc.value)
    assert TEST_TOKEN not in msg
    assert "super-secret-db-pass" not in msg
    assert "staging_user" not in msg
    assert SECRET_URL not in msg


def test_unprovable_items_are_honest():
    items = guard.unprovable_isolation_items()
    blob = "\n".join(items)
    assert "Postgres" in blob or "数据库" in blob
    assert "Redis" in blob
    assert "getMe" in blob
    assert "不等于" in blob
    assert items == guard.UNPROVABLE_ISOLATION_ITEMS


def test_channel_defaults_empty_unless_explicitly_allowed():
    ops = {"broadcast_channel": "@something", "coupon_channel": ""}
    problems = guard.channel_isolation_problems(ops, _staging())
    assert problems and "broadcast_channel" in problems[0]
    assert "@something" not in problems[0]
    assert guard.channel_isolation_problems(ops, _staging(STAGING_ALLOW_TEST_CHANNELS="1")) == []
    assert guard.channel_isolation_problems({"broadcast_channel": ""}, _staging()) == []
    assert guard.configured_channel_fields({
        "city_overrides": {"深圳": {"directory_channel": "@x"}},
        "required_chats": ["@group"],
    }) == ["required_chats", "city_overrides.*.directory_channel"]


def test_runtime_isolation_accepts_injected_ops():
    asyncio.run(guard.assert_runtime_isolation(_staging(), ops={"broadcast_channel": ""}))
    with pytest.raises(StagingIsolationError):
        asyncio.run(guard.assert_runtime_isolation(_staging(), ops={"review_channel": "-1001"}))


def test_startup_aborts_before_side_effects_when_unconfirmed(monkeypatch):
    import bot.main as main

    hits = []

    async def mark(name="connect"):
        hits.append(name)

    monkeypatch.setenv("ENV", "staging")
    for key in (
        "STAGING_ISOLATION",
        "STAGING_CONFIRMED_ISOLATED",
        "STAGING_DB_IS_ISOLATED",
        "STAGING_DATA_IS_SAMPLE",
        "STAGING_REDIS_UNUSED",
        "STAGING_EXPECTED_BOT_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(main, "connect_db", lambda: mark("connect_db"))
    monkeypatch.setattr(main.anti_brush, "allow", lambda *a, **k: mark("redis"))
    monkeypatch.setattr(main, "_schedule_jobs", lambda: hits.append("schedule"))
    monkeypatch.setattr(main, "_setup_webhook", lambda: mark("webhook"))

    ready_before = main._ready
    error_before = main._startup_error
    main._ready = True  # stale ready must not survive a failed isolation gate
    try:
        asyncio.run(main._startup())
        assert hits == []
        assert main._ready is False
        assert main._startup_error
        assert TEST_TOKEN not in (main._startup_error or "")
        assert "ENV=staging 不能证明完全隔离" in (main._startup_error or "")
    finally:
        main._ready = ready_before
        main._startup_error = error_before
        # do not leave ENV=staging for later tests that import job helpers
        monkeypatch.delenv("ENV", raising=False)


def test_startup_maintenance_skips_telegram_jobs_in_staging(monkeypatch):
    from bot.services import bot_info

    class FakeSched:
        running = True

        def __init__(self):
            self.ids = []

        def add_job(self, fn, *a, id=None, **k):
            self.ids.append(id)

    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("STAGING_ISOLATION", "1")
    for key in ("STAGING_ENABLE_TELEGRAM_JOBS", "STAGING_ENABLE_DB_JOBS"):
        monkeypatch.delenv(key, raising=False)
    sch = FakeSched()
    asyncio.run(bot_info.startup_maintenance(sch))
    assert sch.ids == []
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("STAGING_ISOLATION", raising=False)


def test_existing_production_maintenance_jobs_unchanged(monkeypatch):
    from bot.services import bot_info

    class FakeSched:
        running = True

        def __init__(self):
            self.ids = []

        def add_job(self, fn, *a, id=None, **k):
            self.ids.append(id)

    async def noop(*a, **k):
        return None

    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("STAGING_ISOLATION", raising=False)
    monkeypatch.setattr(bot_info, "job_expiry_nudge", noop)
    sch = FakeSched()
    asyncio.run(bot_info.startup_maintenance(sch))
    assert {"open_shift", "shift_timeout", "expiry_nudge"} <= set(sch.ids)
