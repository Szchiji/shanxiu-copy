"""缓存 Telegram getMe，供首页展示当前机器人用户名。"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_cache: Dict[str, Any] = {"username": "", "id": None, "ts": 0.0}
_TTL = 3600.0

try:
    from bot.services import user_admin  # noqa: F401
except Exception:
    logger.exception("user_admin wire skipped")
try:
    from bot.services import search_hooks  # noqa: F401
except Exception:
    logger.exception("search_hooks wire skipped")
try:
    from bot.services import admin_ops_wire  # noqa: F401
    from bot.services import extras_store  # noqa: F401
    from bot.services import admin_acl  # noqa: F401
except Exception:
    logger.exception("admin_ops_wire skipped")


def _install_shift_jobs(scheduler=None) -> None:
    # The app runs as `python -m bot.main`, so `from bot.main import scheduler` would import a
    # second copy of the module whose scheduler never runs. The caller passes the live one.
    try:
        if scheduler is None:
            from bot.main import scheduler
        from bot.services.open_shift import expire_unanswered, send_due
    except Exception:
        return
    if not getattr(scheduler, "running", False):
        return
    async def job_open_shift() -> None:
        try:
            n = await send_due()
            if n:
                logger.info("shift remind users=%s", n)
        except Exception:
            logger.exception("shift remind failed")
    async def job_shift_timeout() -> None:
        try:
            n = await expire_unanswered()
            if n:
                logger.info("shift timeout closed=%s", n)
        except Exception:
            logger.exception("shift timeout failed")
    # Every 5 min: each teacher gets the prompt ~1h before their own first shift of the day.
    scheduler.add_job(job_open_shift, "interval", minutes=5, id="open_shift", replace_existing=True)
    scheduler.add_job(job_shift_timeout, "interval", minutes=10, id="shift_timeout", replace_existing=True)


async def refresh_bot_identity(bot=None) -> Dict[str, Any]:
    global _cache
    try:
        if bot is None:
            from bot.main import bot as app_bot
            bot = app_bot
        from bot.services.tg_retry import tg_call
        me = await tg_call("get_me (bot identity)", bot.get_me)
        _cache = {
            "username": me.username or "",
            "id": me.id,
            "first_name": me.first_name or "",
            "ts": time.time(),
        }
        logger.info("Bot identity @%s id=%s", _cache["username"], _cache["id"])
    except Exception as exc:
        from bot.services.tg_retry import is_transient
        if type(exc).__name__ == "TelegramRetryAfter" or is_transient(exc):
            # flood control / Bad Gateway / network blip even after retries: keep the cached identity, next refresh fills it
            logger.warning("refresh_bot_identity: %s after retries, using cached identity", type(exc).__name__)
        else:
            logger.exception("refresh_bot_identity failed")
    return dict(_cache)


async def job_expiry_nudge() -> None:
    try:
        from bot.services.expiry_nudge import nudge_expiring
        await nudge_expiring(3, 1)
    except Exception:
        logger.exception("expiry nudge failed")


async def startup_maintenance(scheduler=None) -> None:
    """Startup-only work (used to piggyback on /api/home identity refresh)."""
    from bot.services import staging_guard

    telegram_jobs = staging_guard.telegram_jobs_enabled()
    db_jobs = staging_guard.db_jobs_enabled()
    try:
        from bot.services.admin_acl import warm
        await warm()
    except Exception:
        logger.exception("warm extra admins failed")
    if telegram_jobs:
        try:
            _install_shift_jobs(scheduler)
        except Exception:
            logger.exception("shift jobs skipped")
    elif staging_guard.staging_requested():
        logger.warning("Staging: shift / Telegram jobs off until STAGING_ENABLE_TELEGRAM_JOBS=1")
    try:
        if telegram_jobs:
            if scheduler is None:
                from bot.main import scheduler
            if getattr(scheduler, "running", False):
                # 每天 10:00（北京时间）发一次：剩 3 天、剩 1 天各一次。部署/重启不会补发（不在启动时跑）。
                scheduler.add_job(job_expiry_nudge, "cron", hour=10, minute=0, timezone="Asia/Shanghai",
                                  id="expiry_nudge", replace_existing=True, coalesce=True, max_instances=1,
                                  misfire_grace_time=3600)
    except Exception:
        logger.exception("expiry nudge job skipped")
    try:
        if telegram_jobs and scheduler is not None and getattr(scheduler, "running", False):
            from bot.services.channel_directory import job_refresh_all
            scheduler.add_job(job_refresh_all, "cron", hour=12, minute=0, timezone="Asia/Shanghai",
                              id="channel_directory", replace_existing=True, coalesce=True, max_instances=1,
                              misfire_grace_time=3600)
    except Exception:
        logger.exception("channel directory job skipped")
    try:
        if telegram_jobs and scheduler is not None and getattr(scheduler, "running", False):
            # 优惠推送：过期 / 结束 / 停用 / 领完 → 频道帖改成「已结束」
            from bot.services.coupon_push import job_close_due
            scheduler.add_job(job_close_due, "interval", minutes=10, id="coupon_posts",
                              replace_existing=True, coalesce=True, max_instances=1)
    except Exception:
        logger.exception("coupon post job skipped")
    try:
        if telegram_jobs and scheduler is not None and getattr(scheduler, "running", False):
            from bot.services.label_sync import job_tick
            scheduler.add_job(job_tick, "interval", seconds=15, id="label_sync",
                              replace_existing=True, coalesce=True, max_instances=1)
    except Exception:
        logger.exception("label sync job skipped")
    if telegram_jobs:
        try:
            # pending reviews from the last day that never reached the bot admins (each sent once)
            from bot.services.review_notify import backfill_pending
            await backfill_pending(24)
        except Exception:
            logger.exception("review notice backfill skipped")
    elif staging_guard.staging_requested():
        logger.warning("Staging: review backfill off until STAGING_ENABLE_TELEGRAM_JOBS=1")
    if db_jobs:
        try:
            from bot.services.search_service import backfill_prices
            await backfill_prices()
        except Exception:
            logger.exception("price backfill skipped")
    elif staging_guard.staging_requested():
        logger.warning("Staging: price backfill off until STAGING_ENABLE_DB_JOBS=1")


async def get_bot_identity(bot=None) -> Dict[str, Any]:
    if not _cache.get("username") or (time.time() - float(_cache.get("ts") or 0)) > _TTL:
        await refresh_bot_identity(bot)
    return dict(_cache)


def bot_tme_url(username: Optional[str] = None) -> str:
    name = (username or _cache.get("username") or "").lstrip("@")
    return f"https://t.me/{name}" if name else ""


def normalize_contact(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("tg://"):
        return s
    if s.startswith(("t.me/", "telegram.me/")):
        s = "https://" + s
    if s.startswith("http://") or s.startswith("https://"):
        import re
        if re.match(r"^https?://(www\.)?(t\.me|telegram\.me)/\S+$", s, re.I):
            return s
        return ""
    if s.startswith("@"):
        name = s[1:]
        return f"https://t.me/{name}" if name and name.replace("_", "").isalnum() else ""
    if s.lstrip("-").isdigit():
        return f"tg://user?id={s}"
    if s.replace("_", "").isalnum():
        return f"https://t.me/{s}"
    return ""
