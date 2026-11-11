"""频道按区目录（可选，默认关）：每城一条消息，列出各区在架资料，花名直达小程序详情。

开关 / 频道：全站默认 + 每城可单独设（channel_directory / directory_channel；频道空 = 用推送频道）。
每天 12:00（北京时间）逐城刷新；后台保存设置时也会马上同步一次。第一次发新消息，之后只编辑同一条。
关掉或换频道：旧目录消息删掉（删不掉就改成「已停用」）。失败只记日志。
Telegram 临时错误（Bad Gateway / 5xx / 网络 / 限流）：每次调用重试约 3 次；编辑遇到临时错误只重试编辑，
不改发新消息（避免重复）；只有「消息不存在」这类真错误才发新消息。整城仍失败 → 几分钟后再试这一城一次。
有上新（审核通过上架 / 续期或重新上架）或下架（管理员下架 / 封号下架 / 到期自动下架 / 删除 / 改到别的城市）→ 只刷新这一城的目录（这一城开了按区目录才刷），每城约 90 秒防抖：
这段时间内上新多条只刷一次。改设置不触发（仍等每天 12:00）。
"""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Dict, List

from sqlalchemy import select

from bot.db import session_scope
from bot.models import Lamp, LampStatus
from bot.models_extra import ChannelDirectory
from bot.services import home_service

from bot.services import execution_trace as delivery
logger = logging.getLogger(__name__)
MAX_PER_AREA = 30
RETRY_CITY_AFTER = 300   # seconds: a city whose refresh failed on a transient error is tried once more
_bg: set = set()         # pending retry tasks (kept referenced)
NEW_LISTING_DEBOUNCE = 90   # seconds: listings going live within this window → one refresh per city
_pending: Dict[str, "asyncio.Task"] = {}
_sleep = asyncio.sleep       # patched in tests


def render(city: str, lamps: List[Dict], bot_name: str) -> str:
    from bot.services.broadcast import start_url as startapp_url
    from bot.services.search_service import district_key
    groups: Dict[str, List[Dict]] = {}
    for x in lamps:
        groups.setdefault(district_key(x.get("district")) or "其他", []).append(x)
    lines = [f"📖 <b>{html.escape(city)} · 按区目录</b>（点花名直达详情）"]
    for area in sorted(groups, key=lambda k: (-len(groups[k]), k)):
        items = groups[area][:MAX_PER_AREA]
        names = " · ".join(
            f'<a href="{startapp_url(bot_name, x["lamp_id"], "s")}">{html.escape(str(x.get("title") or "")[:12])}</a>'
            for x in items if startapp_url(bot_name, x["lamp_id"], "s")
        )
        lines.append(f"\n🌺 <b>{html.escape(area)}</b>（{len(groups[area])}）\n{names}")
    text = "\n".join(lines)
    return text[:4000]


async def _ref(city: str):
    async with session_scope() as s:
        ref = (await s.execute(select(ChannelDirectory).where(ChannelDirectory.city == city))).scalar_one_or_none()
        return (ref.chat, int(ref.message_id)) if ref else ("", 0)


async def _drop_ref(city: str) -> None:
    async with session_scope() as s:
        ref = (await s.execute(select(ChannelDirectory).where(ChannelDirectory.city == city))).scalar_one_or_none()
        if ref:
            await s.delete(ref)


@delivery.traced("directory","retire",source="manual")
async def _retire(chat: str, mid: int, city: str) -> None:
    """Remove an old directory message (switch off / channel moved). Delete; else mark it stopped."""
    from bot.main import bot
    await delivery.current().identify(city=city,target="message:"+str(mid))
    try:
        await delivery.current().call(lambda:bot.delete_message(chat_id=chat,message_id=mid),"retire_delete")
        return
    except Exception:
        pass
    try:
        await delivery.current().call(lambda:bot.edit_message_text(text=f"📖 {html.escape(city)} · 按区目录（已停用）",chat_id=chat,message_id=mid),"retire_edit")
    except Exception:
        logger.warning("retire old directory for %s failed", city)


async def refresh_city(city: str) -> bool:
    """Post / edit this city's directory in its 按区目录 channel. Switch off → the old message is
    removed; channel changed → old one removed, new one posted. Only this city's listings are listed."""
    return await _refresh_city(city) == "ok"


@delivery.traced("directory","refresh",source="manual")
async def _refresh_city(city: str) -> str:
    """ok / skip (off, nothing to show) / fail (Telegram error, logged)."""
    from bot.services.home_feed import feature_chat, ops_for_city
    from bot.services.tg_retry import is_transient, tg_call
    site = ops_for_city(await home_service.get_or_create_settings(), city)
    await delivery.current().identify(city=city,revision=site.get("config_revision"),target="channel")
    ref_chat, ref_mid = await _ref(city)
    chat = feature_chat(site, "channel_directory") if site.get("channel_directory") else ""
    if ref_mid and ref_chat != chat:
        await _retire(ref_chat, ref_mid, city)
        await _drop_ref(city)
        ref_chat, ref_mid = "", 0
    if not chat:
        delivery.current().mark("skipped" if not site.get("channel_directory") else "not_configured","disabled" if not site.get("channel_directory") else "not_configured")
        return "skip"
    from bot.services import bot_info
    bot_name = (await bot_info.get_bot_identity()).get("username") or ""
    async with session_scope() as s:
        rows = (await s.execute(
            select(Lamp.lamp_id, Lamp.title, Lamp.district)
            .where(Lamp.status == LampStatus.ACTIVE.value, Lamp.city == city)
            .order_by(Lamp.created_at.desc()).limit(400)
        )).all()
    if rows:
        text = render(city, [{"lamp_id": a, "title": b, "district": c} for a, b, c in rows], bot_name)
    elif ref_mid:
        text = f"📖 <b>{html.escape(city)} · 按区目录</b>\n\n暂时没有在架资料"
    else:
        return "skip"
    from bot.main import bot
    try:
        if ref_mid:
            try:
                await tg_call(f"directory edit ({city})", lambda: delivery.current().call(lambda:bot.edit_message_text(
                    text=text,chat_id=chat,message_id=ref_mid,disable_web_page_preview=True),"update"))
                return "ok"
            except Exception as exc:
                if "not modified" in str(exc).lower():
                    return "ok"
                if is_transient(exc) or type(exc).__name__ == "TelegramRetryAfter":
                    # Telegram is having trouble, the message most likely still exists: don't post a duplicate
                    logger.warning("directory edit for %s failed after retries (%s); not posting a new one",
                                   city, type(exc).__name__)
                    return "fail"
                logger.warning("directory edit failed (%s), posting new", type(exc).__name__)
        msg = await tg_call(f"directory post ({city})", lambda: delivery.current().call(lambda:bot.send_message(chat,text,disable_web_page_preview=True),"create",send=True))
        async with session_scope() as s:
            ref = (await s.execute(select(ChannelDirectory).where(ChannelDirectory.city == city))).scalar_one_or_none()
            if ref:
                ref.chat, ref.message_id = chat, int(msg.message_id)
            else:
                s.add(ChannelDirectory(city=city, chat=chat, message_id=int(msg.message_id)))
        return "ok"
    except Exception as exc:
        if delivery.current().ack:delivery.current().mark("unknown","delivery_unknown")
        logger.warning("channel directory for %s failed (%s)",city,type(exc).__name__)
        return "fail"


@delivery.origin("scheduled_retry")
async def _retry_later(cities: List[str], delay: float = RETRY_CITY_AFTER, sleep=asyncio.sleep) -> int:
    """One more try for cities whose refresh failed (e.g. Bad Gateway at noon). Logged either way."""
    await sleep(delay)
    n = 0
    for city in cities:
        try:
            st = await _refresh_city(city)
        except Exception:
            logger.exception("channel directory retry for %s failed", city)
            st = "fail"
        if st == "ok":
            n += 1
            logger.info("channel directory for %s refreshed on retry", city)
        elif st == "fail":
            logger.error("channel directory for %s still failing after retry; next scheduled refresh will try again", city)
    return n


def _schedule_retry(cities: List[str]) -> None:
    try:
        t = asyncio.get_running_loop().create_task(_retry_later(list(cities)))
    except RuntimeError:
        return
    _bg.add(t)
    t.add_done_callback(_bg.discard)
    logger.warning("channel directory failed for %s; retrying once in %ss", ", ".join(cities), RETRY_CITY_AFTER)


async def on_listing_live(lamp_id: str = "", city: str = "") -> bool:
    """A listing just went live (approved / renewed / relisted): refresh only its city's directory, debounced.
    Best effort, never raises. Returns True when a refresh was scheduled (False: off for this city / coalesced)."""
    try:
        if not city and lamp_id:
            async with session_scope() as s:
                city = (await s.execute(select(Lamp.city).where(Lamp.lamp_id == lamp_id))).scalar_one_or_none() or ""
        if not city:
            return False
        from bot.services.home_feed import feature_chat, ops_for_city
        site = ops_for_city(await home_service.get_or_create_settings(), city)
        if not site.get("channel_directory") or not feature_chat(site, "channel_directory"):
            from bot.services import execution_log
            await execution_log.event("directory","schedule","skipped" if not site.get("channel_directory") else "not_configured","disabled" if not site.get("channel_directory") else "not_configured",city=city,listing_id=lamp_id or None,source="listing_event",revision=site.get("config_revision"),target="channel")
            return False
        t = _pending.get(city)
        if t is not None and not t.done():
            return False          # already queued: this listing is picked up by that refresh
        t = asyncio.get_running_loop().create_task(_debounced_refresh(city))
        _pending[city] = t
        t.add_done_callback(lambda done, c=city: _pending.pop(c, None) if _pending.get(c) is done else None)
        logger.info("channel directory for %s: new listing, refreshing in %ss", city, NEW_LISTING_DEBOUNCE)
        return True
    except Exception:
        logger.exception("channel directory auto-refresh scheduling failed")
        return False


async def on_listing_gone(lamp_id: str = "", city: str = "") -> bool:
    """A live listing left the directory (delisted / expired / deleted / moved city). Same debounce;
    pass `city` when the row is already gone."""
    return await on_listing_live(lamp_id, city)


async def on_cities_changed(cities) -> int:
    n = 0
    for c in dict.fromkeys(c for c in cities if c):
        n += 1 if await on_listing_live("", c) else 0
    return n


@delivery.origin("listing_event")
async def _debounced_refresh(city: str) -> str:
    await _sleep(NEW_LISTING_DEBOUNCE)
    _pending.pop(city, None)    # listings going live from now on queue another refresh
    try:
        st = await _refresh_city(city)
    except Exception:
        logger.exception("channel directory auto-refresh for %s failed", city)
        st = "fail"
    if st == "fail":
        _schedule_retry([city])
    elif st == "ok":
        logger.info("channel directory for %s refreshed (new listing)", city)
    return st


@delivery.origin("scheduled")
async def job_refresh_all() -> int:
    """Every enabled city (each with its own channel / switch), plus cleanup of directories left in
    cities that were switched off or are no longer enabled."""
    n = 0
    try:
        site = await home_service.get_or_create_settings()
        cities = list(site.get("enabled_cities") or [])
        async with session_scope() as s:
            known = [c for (c,) in (await s.execute(select(ChannelDirectory.city))).all()]
        failed = []
        for city in cities:
            st = await _refresh_city(city)
            if st == "ok":
                n += 1
            elif st == "fail":
                failed.append(city)
        if failed:
            _schedule_retry(failed)
        for city in known:
            if city not in cities:
                chat, mid = await _ref(city)
                if mid:
                    await _retire(chat, mid, city)
                await _drop_ref(city)
    except Exception:
        logger.exception("channel directory job failed")
    return n
