"""Staging 启动隔离校验。

仅在明确请求 staging / test 时生效；生产与本地默认路径不增加必填项。
ENV=staging 本身不能证明已隔离。本模块只做离线配置校验，不访问 Telegram、
不读取生产 token，不把 secret 写进异常或日志。无法自动证明的项必须由人声明，
声明不等于事实。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlparse

# Recorded from a prior read-only audit of the current production Railway
# deployment. These are public identifiers, not secrets. Update them if
# production is rebuilt. They do not prove a new host is safe.
KNOWN_PRODUCTION_HOSTS = frozenset({
    "web-production-46ed80.up.railway.app",
})
KNOWN_PRODUCTION_RAILWAY_ENV_IDS = frozenset({
    "95d049a1-3156-4a57-8287-b3ddad73b7c2",
})
KNOWN_PRODUCTION_RAILWAY_SERVICE_IDS = frozenset({
    "37c189b7-2717-4a7b-aee8-153d71f3ff37",
})

STAGING_ENV_NAMES = frozenset({"staging", "test", "testing"})
PRODUCTION_RAILWAY_ENV_NAMES = frozenset({"production", "prod"})

CHANNEL_SETTING_KEYS = (
    "broadcast_channel",
    "directory_channel",
    "review_channel",
    "coupon_channel",
    "media_channel_id",
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_TOKEN_PREFIX = re.compile(r"^\d{6,16}$")


class StagingIsolationError(RuntimeError):
    """Staging was requested but isolation is not confirmed."""


def is_truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def on_known_production_runtime(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when this process is clearly the known production Railway env or web service."""
    env = environ if environ is not None else os.environ
    railway_name = (env.get("RAILWAY_ENVIRONMENT_NAME") or "").strip().lower()
    railway_env_id = (env.get("RAILWAY_ENVIRONMENT_ID") or "").strip().lower()
    railway_service_id = (env.get("RAILWAY_SERVICE_ID") or "").strip().lower()
    if railway_name in PRODUCTION_RAILWAY_ENV_NAMES:
        return True
    if railway_env_id and railway_env_id in blocked_railway_env_ids(env):
        return True
    if railway_service_id and railway_service_id in blocked_railway_service_ids(env):
        return True
    return False


def staging_requested(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    # Never activate staging mode on the known production runtime. A mistaken
    # ENV=staging or STAGING_* on production must not refuse to start.
    if on_known_production_runtime(env):
        return False
    if is_truthy(env.get("STAGING_ISOLATION")):
        return True
    app_env = (env.get("ENV") or "").strip().lower()
    railway_name = (env.get("RAILWAY_ENVIRONMENT_NAME") or "").strip().lower()
    return app_env in STAGING_ENV_NAMES or railway_name in STAGING_ENV_NAMES


def bot_id_from_token(token: str) -> Optional[int]:
    """Parse the public bot id prefix. Never returns or logs the secret suffix."""
    raw = (token or "").strip()
    if ":" not in raw:
        return None
    prefix, secret = raw.split(":", 1)
    if not secret or not _TOKEN_PREFIX.match(prefix):
        return None
    return int(prefix)


def hostname_of(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _csv(value: Optional[str]) -> List[str]:
    return [part.strip().lower() for part in (value or "").split(",") if part.strip()]


def blocked_hosts(environ: Mapping[str, str]) -> frozenset[str]:
    extra = {hostname_of(h) or h for h in _csv(environ.get("STAGING_BLOCKED_HOSTS"))}
    return frozenset(h for h in (KNOWN_PRODUCTION_HOSTS | extra) if h)


def blocked_railway_env_ids(environ: Mapping[str, str]) -> frozenset[str]:
    extra = set(_csv(environ.get("STAGING_BLOCKED_RAILWAY_ENV_IDS")))
    return frozenset(KNOWN_PRODUCTION_RAILWAY_ENV_IDS | extra)


def blocked_railway_service_ids(environ: Mapping[str, str]) -> frozenset[str]:
    extra = set(_csv(environ.get("STAGING_BLOCKED_RAILWAY_SERVICE_IDS")))
    return frozenset(KNOWN_PRODUCTION_RAILWAY_SERVICE_IDS | extra)


def unprovable_isolation_items() -> List[str]:
    """Honest list of isolation facts this gate cannot prove automatically."""
    return [
        "DATABASE_URL 是否指向独立 Postgres（而非生产库或生产数据的副本）",
        "REDIS_URL 是否指向独立 Redis（而非生产实例）",
        "库内数据是否仅为示例资料，而非生产转储",
        "库内频道/群目标在连接数据库之前无法检查",
        "专用 token 虽可离线核对 bot id 前缀，但无法证明该 token 未被误用于其他环境",
        "STAGING_EXPECTED_BOT_USERNAME 无法离线证明（本门禁不调用 getMe）",
        "未列入阻止名单的生产域名、私有 DNS 或未来重建后的 Railway 标识",
        "人工声明（STAGING_*_IS_ISOLATED 等）不等于已核实的事实",
    ]


def db_jobs_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    if not staging_requested(env):
        return True
    return is_truthy(env.get("STAGING_ENABLE_DB_JOBS"))


def telegram_jobs_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    if not staging_requested(env):
        return True
    return is_truthy(env.get("STAGING_ENABLE_TELEGRAM_JOBS"))


def bot_hooks_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """setWebhook / menu / commands / deleteWebhook / getMe at startup."""
    env = environ if environ is not None else os.environ
    if not staging_requested(env):
        return True
    return is_truthy(env.get("STAGING_ENABLE_BOT_HOOKS"))


def demo_purge_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    if not staging_requested(env):
        return True
    return is_truthy(env.get("STAGING_ENABLE_DEMO_PURGE"))


def job_policy(environ: Optional[Mapping[str, str]] = None) -> Dict[str, bool]:
    env = environ if environ is not None else os.environ
    return {
        "staging": staging_requested(env),
        "db_jobs": db_jobs_enabled(env),
        "telegram_jobs": telegram_jobs_enabled(env),
        "bot_hooks": bot_hooks_enabled(env),
        "demo_purge": demo_purge_enabled(env),
    }


def configured_channel_fields(ops: Optional[Mapping[str, Any]]) -> List[str]:
    """Return setting keys that currently point at a channel or chat. Values are not included."""
    found: List[str] = []
    data = ops or {}
    for key in CHANNEL_SETTING_KEYS:
        if str(data.get(key) or "").strip():
            found.append(key)
    required = data.get("required_chats") or []
    if isinstance(required, list) and any(str(item or "").strip() for item in required):
        found.append("required_chats")
    overrides = data.get("city_overrides") or {}
    if isinstance(overrides, dict):
        for city, vals in overrides.items():
            if not isinstance(vals, dict):
                continue
            for key in CHANNEL_SETTING_KEYS:
                if str(vals.get(key) or "").strip():
                    found.append("city_overrides.*.{0}".format(key))
    # Stable unique order, no city names (may be operational context, not needed).
    unique: List[str] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def _require_https_public_url(raw: str, label: str, problems: List[str]) -> str:
    value = (raw or "").strip()
    if not value:
        problems.append("{0} 未设置；staging 需要指向独立测试域名的 HTTPS 地址".format(label))
        return ""
    if "://" not in value or not value.lower().startswith("https://"):
        problems.append("{0} 必须是 https:// 开头的公开地址".format(label))
        return ""
    host = hostname_of(value)
    if not host:
        problems.append("{0} 无法解析主机名".format(label))
        return ""
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost"):
        problems.append("{0} 不能是 localhost；测试环境需要独立的 HTTPS 主机".format(label))
        return host
    return host


def _startup_problems(environ: Mapping[str, str]) -> List[str]:
    problems: List[str] = []
    if not is_truthy(environ.get("STAGING_ISOLATION")):
        problems.append(
            "仅 ENV=staging 或 Railway 环境名不能证明已隔离；必须显式设置 STAGING_ISOLATION=1"
        )
    if not is_truthy(environ.get("STAGING_CONFIRMED_ISOLATED")):
        problems.append("缺少 STAGING_CONFIRMED_ISOLATED=1（操作者确认这是独立测试环境）")
    if not is_truthy(environ.get("STAGING_DB_IS_ISOLATED")):
        problems.append("缺少 STAGING_DB_IS_ISOLATED=1（无法自动证明数据库独立，必须人工确认）")
    if not is_truthy(environ.get("STAGING_DATA_IS_SAMPLE")):
        problems.append("缺少 STAGING_DATA_IS_SAMPLE=1（无法自动证明库内不是生产数据）")

    redis_url = (environ.get("REDIS_URL") or "").strip()
    redis_isolated = is_truthy(environ.get("STAGING_REDIS_IS_ISOLATED"))
    redis_unused = is_truthy(environ.get("STAGING_REDIS_UNUSED"))
    if redis_isolated and redis_unused:
        problems.append("STAGING_REDIS_IS_ISOLATED 与 STAGING_REDIS_UNUSED 不能同时为真")
    elif redis_unused:
        if redis_url:
            problems.append("已声明 STAGING_REDIS_UNUSED=1 时不得再配置 REDIS_URL")
    elif redis_isolated:
        if not redis_url:
            problems.append("已声明 STAGING_REDIS_IS_ISOLATED=1 但 REDIS_URL 为空")
    else:
        problems.append(
            "缺少 Redis 隔离声明：独立实例请设 STAGING_REDIS_IS_ISOLATED=1，"
            "或明确不使用 Redis 并设 STAGING_REDIS_UNUSED=1 且留空 REDIS_URL"
        )

    expected_raw = (environ.get("STAGING_EXPECTED_BOT_ID") or "").strip()
    if not expected_raw:
        problems.append("缺少 STAGING_EXPECTED_BOT_ID（专用测试机器人的数字 id）")
        expected_id = None
    elif not expected_raw.isdigit():
        problems.append("STAGING_EXPECTED_BOT_ID 必须是数字 bot id，不要填写 token 或用户名")
        expected_id = None
    else:
        expected_id = int(expected_raw)

    token_id = bot_id_from_token(environ.get("BOT_TOKEN") or "")
    if token_id is None:
        problems.append("BOT_TOKEN 缺失或格式无效；无法离线解析测试 bot id")
    elif expected_id is not None and token_id != expected_id:
        problems.append("BOT_TOKEN 的 bot id 与 STAGING_EXPECTED_BOT_ID 不一致")

    blocked = blocked_hosts(environ)
    webhook_host = _require_https_public_url(environ.get("WEBHOOK_HOST") or "", "WEBHOOK_HOST", problems)
    webapp_host = _require_https_public_url(environ.get("WEBAPP_URL") or "", "WEBAPP_URL", problems)
    for label, host in (("WEBHOOK_HOST", webhook_host), ("WEBAPP_URL", webapp_host)):
        if host and host in blocked:
            problems.append("{0} 指向已知生产域名，已拒绝".format(label))

    railway_name = (environ.get("RAILWAY_ENVIRONMENT_NAME") or "").strip().lower()
    railway_env_id = (environ.get("RAILWAY_ENVIRONMENT_ID") or "").strip().lower()
    railway_service_id = (environ.get("RAILWAY_SERVICE_ID") or "").strip().lower()
    if railway_name in PRODUCTION_RAILWAY_ENV_NAMES:
        problems.append("当前 Railway 环境名是 production，不能作为 staging 启动")
    if railway_env_id and railway_env_id in blocked_railway_env_ids(environ):
        problems.append("当前 Railway 环境 ID 匹配已知生产环境，已拒绝")
    if railway_service_id and railway_service_id in blocked_railway_service_ids(environ):
        problems.append("当前 Railway 服务 ID 匹配已知生产 Web 服务，已拒绝")
    return problems


def _format_error(problems: Sequence[str]) -> str:
    lines = [
        "Staging 启动已拒绝：隔离配置未确认。ENV=staging 不能证明完全隔离。",
        *["- {0}".format(item) for item in problems],
        "无法自动证明的隔离项见 docs/staging-isolation.md；人工声明不是核实。",
    ]
    return "\n".join(lines)


def assert_startup_isolation(environ: Optional[Mapping[str, str]] = None) -> None:
    """Call before DB migration, Redis use, scheduler, backfill, or Telegram writes."""
    env = environ if environ is not None else os.environ
    if not staging_requested(env):
        return
    problems = _startup_problems(env)
    if problems:
        raise StagingIsolationError(_format_error(problems))


def channel_isolation_problems(
    ops: Optional[Mapping[str, Any]],
    environ: Optional[Mapping[str, str]] = None,
) -> List[str]:
    env = environ if environ is not None else os.environ
    if not staging_requested(env):
        return []
    fields = configured_channel_fields(ops)
    if not fields:
        return []
    if is_truthy(env.get("STAGING_ALLOW_TEST_CHANNELS")):
        return []
    return [
        "库内已配置频道或必进群目标（{0}）；staging 默认要求为空。"
        "仅在确认这些是测试频道后才可设置 STAGING_ALLOW_TEST_CHANNELS=1".format(", ".join(fields))
    ]


async def _read_ops_readonly() -> Dict[str, Any]:
    from sqlalchemy import select

    from bot.db import session_scope
    from bot.models import SiteSettings
    from bot.services import home_feed

    async with session_scope() as session:
        result = await session.execute(select(SiteSettings).where(SiteSettings.key == "default"))
        row = result.scalar_one_or_none()
        if not row:
            return {}
        return home_feed.merge_ops(getattr(row, "ops_config", None) or {})


async def assert_runtime_isolation(
    environ: Optional[Mapping[str, str]] = None,
    *,
    ops: Optional[Mapping[str, Any]] = None,
) -> None:
    """Post-connect check: default-empty channels. Does not claim the DB itself is isolated."""
    env = environ if environ is not None else os.environ
    if not staging_requested(env):
        return
    if ops is None:
        ops = await _read_ops_readonly()
    problems = channel_isolation_problems(ops, env)
    if problems:
        raise StagingIsolationError(_format_error(problems))


UNPROVABLE_ISOLATION_ITEMS = unprovable_isolation_items()
