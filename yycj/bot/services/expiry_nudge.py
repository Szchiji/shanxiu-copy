"""上架到期提醒：每天 10:00（北京时间）跑一次；按北京日历天数，剩 3 天、剩 1 天各提醒老师一次。

去重：每个到期日一个标记（extras._expiry_nudge = 3 天档，_expiry_nudge_1d = 1 天档），
续期后到期日变了才会再提醒。先发过 1 天档就不再补 3 天档。"""
from __future__ import annotations

from html import escape as _h

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from sqlalchemy import select

from bot.db import session_scope
from bot.models import Lamp, LampStatus
from bot.services.extras_store import load_extras, save_extras

logger = logging.getLogger(__name__)


BJ = timedelta(hours=8)


def _days_left(exp: datetime, now: datetime) -> int:
    """Calendar days between today and the expiry date, both in Beijing time (exp/now are naive UTC)."""
    return ((exp + BJ).date() - (now + BJ).date()).days


def _stage(days_left: int, days: int = 3, last_day: int = 1) -> str | None:
    if days_left <= last_day:
        return "1d"
    if days_left <= days:
        return "3d"
    return None


async def nudge_expiring(days: int = 3, last_day: int = 1) -> int:
    now = datetime.utcnow()
    until = now + timedelta(days=days + 1)
    sent = 0
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Lamp).where(
                    Lamp.status == LampStatus.ACTIVE.value,
                    Lamp.expires_at.is_not(None),
                    Lamp.expires_at <= until,
                    Lamp.expires_at > now,
                )
            )
        ).scalars().all()
        items = [r.__dict__.copy() for r in rows]
    if not items:
        return 0
    try:
        from bot.main import bot
    except Exception:
        return 0
    admin_url = ""
    try:
        from bot.services import home_service
        from bot.services.bot_info import normalize_contact
        admin_url = normalize_contact(str((await home_service.get_or_create_settings()).get("admin_contact") or ""))
    except Exception:
        admin_url = ""
    markup = None
    if admin_url.startswith(("https://", "tg://")):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="联系管理员续期", url=admin_url)]])
    for lamp in items:
        lid = str(lamp.get("lamp_id") or "")
        uid = lamp.get("user_id")
        exp = lamp.get("expires_at")
        if not exp or not uid:
            continue
        left = _days_left(exp, now)
        stage = _stage(left, days, last_day)
        if not stage:
            continue
        extras: Dict[str, Any] = await load_extras(lid)
        mark = f"nudge-{str(exp)[:10]}"
        key = "_expiry_nudge_1d" if stage == "1d" else "_expiry_nudge"
        if extras.get(key) == mark:
            continue
        title = _h(str(lamp.get("title") or ""))
        when = (exp + BJ).strftime("%m-%d %H:%M")
        if stage == "1d":
            day = "今天" if left <= 0 else "明天"
            text = f"「{title}」{day}到期（{when}），到期后会自动下架。联系管理员续期即可继续展示。"
        else:
            text = f"「{title}」还有 {left} 天到期（{when}）。联系管理员续期后可以继续展示。"
        # mark first, then send: a crash between the two can at worst skip a reminder, never double-send
        extras[key] = mark
        if stage == "1d":
            extras["_expiry_nudge"] = mark
        await save_extras(lid, extras)
        try:
            try:
                await bot.send_message(int(uid), text, reply_markup=markup)
            except Exception as exc:
                # a bad button (e.g. tg://user link Telegram refuses) must not cost the reminder itself
                if markup is None or "button" not in str(exc).lower():
                    raise
                await bot.send_message(int(uid), text)
            sent += 1
        except Exception:
            logger.exception("expiry nudge failed %s", lid)
    return sent
