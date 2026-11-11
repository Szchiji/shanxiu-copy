"""已上架资料：下架、重新上架、续期、到期处理。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select

from bot.db import session_scope
from bot.models import HomepagePin, Lamp, LampStatus
from bot.services import home_service

logger = logging.getLogger(__name__)

from bot.services.listing_lock import serialized

TEACHER_UNLIST = "teacher"


async def _listing_days(city: Optional[str] = None) -> int:
    from bot.services.home_feed import ops_for_city
    settings = ops_for_city(await home_service.get_or_create_settings(), city)
    return int(settings.get("listing_days") or 30)


async def set_listing_expiry(lamp_id: str, *, days: Optional[int] = None) -> Dict[str, Any]:
    if days is None:
        async with session_scope() as s:
            city = (await s.execute(select(Lamp.city).where(Lamp.lamp_id == lamp_id))).scalar_one_or_none()
        days = await _listing_days(city)
    days = int(days)
    until = datetime.utcnow() + timedelta(days=max(1, days))
    async with session_scope() as s:
        res = await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))
        lamp = res.scalar_one_or_none()
        if not lamp:
            raise ValueError("资料不存在")
        lamp.expires_at = until
        lamp.unlist_reason = None
        relisted = lamp.status == LampStatus.HIDDEN.value
        if relisted:
            lamp.status = LampStatus.ACTIVE.value
        lamp.updated_at = datetime.utcnow()
        await s.flush()
        out = {"lamp_id": lamp.lamp_id, "expires_at": lamp.expires_at, "status": lamp.status}
        city = lamp.city or ""
    if relisted:   # 续期后重新上架 = 上新
        await _directory_on_live(lamp_id, city)
    return out


async def _directory_cities(cities) -> None:
    try:
        from bot.services import channel_directory
        await channel_directory.on_cities_changed(cities)
    except Exception:
        logger.exception("directory auto-refresh hook failed")


async def _directory_on_live(lamp_id: str, city: str = "") -> None:
    try:
        from bot.services import channel_directory
        await channel_directory.on_listing_live(lamp_id, city)
    except Exception:
        logger.exception("directory auto-refresh hook failed")


@serialized
async def unlist_lamp(lamp_id: str, *, reason: str = "admin") -> Dict[str, Any]:
    async with session_scope() as s:
        res = await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))
        lamp = res.scalar_one_or_none()
        if not lamp:
            raise ValueError("资料不存在")
        was_live = lamp.status == LampStatus.ACTIVE.value
        city = lamp.city or ""
        lamp.status = LampStatus.HIDDEN.value
        from bot.services.relist_delivery import invalidate
        await invalidate(s,lamp_id)
        lamp.unlist_reason = (reason or "admin")[:64]
        lamp.feed_pinned = False
        lamp.updated_at = datetime.utcnow()
        pin_res = await s.execute(select(HomepagePin).where(HomepagePin.lamp_id == lamp_id))
        for pin in pin_res.scalars().all():
            s.delete(pin)
        await s.flush()
        out = {"lamp_id": lamp.lamp_id, "status": lamp.status, "reason": lamp.unlist_reason}
    try:
        from bot.services.broadcast import mark_unlist
        await mark_unlist(lamp_id, reason=reason)
    except Exception:
        pass
    if was_live:   # 下架（管理员 / 封号）→ 刷新该城目录
        await _directory_on_live(lamp_id, city)
    return out




async def delete_lamp(lamp_id: str) -> Dict[str, Any]:
    """Admin hard-delete: permanently remove the listing and its direct children.
    Soft 下架 (unlist_lamp) is unchanged. Also removes sessions for this lamp and credit_history rows whose related_id is this lamp / those sessions / those reports (not the user's whole history)."""
    from sqlalchemy import delete as sql_delete
    from bot.models import CreditHistory, HomepagePin, Report, Review, Session, SessionMessage
    from bot.models_extra import Coupon, CouponClaim, Favorite, LampView, ReviewNotice

    async with session_scope() as s:
        res = await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))
        lamp = res.scalar_one_or_none()
        if not lamp:
            raise ValueError("资料不存在")
        city = lamp.city or ""
        title = lamp.title or ""
        was_live = lamp.status == LampStatus.ACTIVE.value
        coupons = list((await s.execute(select(Coupon).where(Coupon.lamp_id == lamp_id))).scalars())
        coupon_ids = [c.coupon_id for c in coupons]
        review_ids = [
            r.review_id
            for r in (await s.execute(select(Review).where(Review.lamp_id == lamp_id))).scalars()
        ]
        report_ids = [
            r.report_id
            for r in (await s.execute(select(Report).where(Report.lamp_id == lamp_id))).scalars()
        ]
        session_ids = [
            r.session_id
            for r in (await s.execute(select(Session).where(Session.lamp_id == lamp_id))).scalars()
        ]

    # Channel / coupon posts before the row disappears (bc_* + get_lamp still work).
    try:
        from bot.services.broadcast import mark_unlist
        await mark_unlist(lamp_id, reason="deleted")
    except Exception:
        logger.exception("mark deleted on channel failed")
    if coupon_ids:
        try:
            from bot.services import coupon_push
            await coupon_push.close(coupon_ids[0], "deleted")
        except Exception:
            logger.exception("coupon post close on delete failed")

    async with session_scope() as s:
        res = await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))
        lamp = res.scalar_one_or_none()
        if not lamp:
            raise ValueError("资料不存在")
        credit_keys = [lamp_id, *session_ids, *report_ids]
        await s.execute(sql_delete(CreditHistory).where(CreditHistory.related_id.in_(credit_keys)))
        if session_ids:
            await s.execute(sql_delete(SessionMessage).where(SessionMessage.session_id.in_(session_ids)))
            await s.execute(sql_delete(Session).where(Session.session_id.in_(session_ids)))
        if coupon_ids:
            await s.execute(sql_delete(CouponClaim).where(CouponClaim.coupon_id.in_(coupon_ids)))
            await s.execute(sql_delete(Coupon).where(Coupon.coupon_id.in_(coupon_ids)))
        if review_ids:
            await s.execute(sql_delete(ReviewNotice).where(ReviewNotice.review_id.in_(review_ids)))
            await s.execute(sql_delete(Review).where(Review.lamp_id == lamp_id))
        await s.execute(sql_delete(HomepagePin).where(HomepagePin.lamp_id == lamp_id))
        await s.execute(sql_delete(Favorite).where(Favorite.lamp_id == lamp_id))
        await s.execute(sql_delete(LampView).where(LampView.lamp_id == lamp_id))
        await s.execute(sql_delete(Report).where(Report.lamp_id == lamp_id))
        from bot.models import ListingEditVersion, ListingEditOperation, ListingRelistOperation
        await s.execute(sql_delete(ListingEditVersion).where(ListingEditVersion.lamp_id == lamp_id))
        await s.execute(sql_delete(ListingEditOperation).where(ListingEditOperation.lamp_id == lamp_id))
        await s.execute(sql_delete(ListingRelistOperation).where(ListingRelistOperation.lamp_id == lamp_id))
        await s.execute(sql_delete(Lamp).where(Lamp.lamp_id == lamp_id))

    await _directory_on_live(lamp_id, city)  # 硬删 → 刷新该城目录（城市在删前记下）
    return {
        "lamp_id": lamp_id,
        "city": city,
        "title": title,
        "was_live": was_live,
        "deleted": True,
        "sessions_removed": len(session_ids),
        "reports_removed": len(report_ids),
    }



async def relist_lamp(lamp_id: str, *, actor_id: int, operation_id: str, expected_updated_at, admin: bool = True) -> Dict[str, Any]:
    from bot.services.relist_delivery import restore
    return await restore(lamp_id,actor_id,operation_id,expected_updated_at,admin=admin)


async def expire_due_lamps() -> int:
    now = datetime.utcnow()
    n = 0
    async with session_scope() as s:
        res = await s.execute(
            select(Lamp).where(
                Lamp.expires_at.is_not(None),
                Lamp.expires_at <= now,
                or_(
                    Lamp.status == LampStatus.ACTIVE.value,
                    (Lamp.status == LampStatus.HIDDEN.value) & (Lamp.unlist_reason == TEACHER_UNLIST),
                ),
            )
        )
        lamps = list(res.scalars().all())
        live_ids = [x.lamp_id for x in lamps if x.status == LampStatus.ACTIVE.value]
        gone_cities = [x.city or "" for x in lamps if x.status == LampStatus.ACTIVE.value]
        for lamp in lamps:
            was_live = lamp.status == LampStatus.ACTIVE.value
            lamp.status = LampStatus.HIDDEN.value
            lamp.unlist_reason = "expired"
            if was_live:
                lamp.feed_pinned = False
            lamp.updated_at = now
            n += 1
        if live_ids:
            pin_res = await s.execute(select(HomepagePin).where(HomepagePin.lamp_id.in_(live_ids)))
            for pin in pin_res.scalars().all():
                s.delete(pin)
        await s.flush()
    for lid in live_ids:
        try:
            from bot.services.broadcast import mark_unlist
            await mark_unlist(lid, reason="expired")
        except Exception:
            pass
    await _directory_cities(gone_cities)   # 到期自动下架 → 刷新这些城的目录（每城一次）
    try:
        from bot.services import listing_flow
        r = await listing_flow.remind_expiring(3)
        if r:
            logger.info("expiry remind sent %s", r)
    except Exception:
        logger.exception("expiry remind failed")
    try:
        purged = await purge_old_hidden(30)
        if purged:
            logger.info("Purged %s hidden listings older than 30 days", purged)
    except Exception:
        logger.exception("purge hidden listings failed")
    return n


async def purge_old_hidden(days: int = 30) -> int:
    cutoff = datetime.utcnow() - timedelta(days=max(1, days))
    n = 0
    async with session_scope() as s:
        res = await s.execute(
            select(Lamp).where(
                Lamp.status == LampStatus.HIDDEN.value,
                Lamp.updated_at <= cutoff,
                or_(Lamp.unlist_reason.is_(None), Lamp.unlist_reason != TEACHER_UNLIST),
            )
        )
        lamps = list(res.scalars().all())
        ids = [x.lamp_id for x in lamps]
        for lamp in lamps:
            s.delete(lamp)
            n += 1
        if ids:
            pin_res = await s.execute(select(HomepagePin).where(HomepagePin.lamp_id.in_(ids)))
            for pin in pin_res.scalars().all():
                s.delete(pin)
        await s.flush()
    return n




async def teacher_unlist(lamp_id: str, user_id: int) -> Dict[str, Any]:
    """Teacher soft-unlist. Does not hard-delete. Reason is TEACHER_UNLIST so they can self-relist."""
    async with session_scope() as s:
        lamp = (await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))).scalar_one_or_none()
        if not lamp:
            raise ValueError("资料不存在")
        if int(lamp.user_id) != int(user_id):
            raise ValueError("这不是你的资料")
        if lamp.status == LampStatus.HIDDEN.value:
            if lamp.unlist_reason == TEACHER_UNLIST:
                return {"lamp_id": lamp.lamp_id, "status": lamp.status, "reason": lamp.unlist_reason}
            raise ValueError("资料已下架，不能自己再下架")
        if lamp.status != LampStatus.ACTIVE.value:
            raise ValueError("资料未上架")
    return await unlist_lamp(lamp_id, reason=TEACHER_UNLIST)


async def teacher_relist(lamp_id: str, user_id: int, *, operation_id: str, expected_updated_at) -> Dict[str, Any]:
    return await relist_lamp(lamp_id,actor_id=user_id,operation_id=operation_id,expected_updated_at=expected_updated_at,admin=False)


async def list_listed(limit: int = 100, cities: Optional[set] = None) -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    stmt = select(Lamp).where(or_(Lamp.status == LampStatus.ACTIVE.value, Lamp.status == LampStatus.HIDDEN.value))
    if cities is not None:
        from bot.services.city_scope import city_in
        stmt = stmt.where(city_in(Lamp.city, cities))
    async with session_scope() as s:
        res = await s.execute(
            stmt.order_by(Lamp.expires_at.asc().nullslast(), Lamp.updated_at.desc()).limit(max(1, min(limit, 300)))
        )
        rows = list(res.scalars().all())
    out = []
    for x in rows:
        days_left = None
        if x.expires_at:
            days_left = int((x.expires_at - now).total_seconds() // 86400)
        out.append(
            {
                "lamp_id": x.lamp_id,
                "title": x.title,
                "city": x.city,
                "status": x.status,
                "expires_at": x.expires_at,
                "days_left": days_left,
                "unlist_reason": x.unlist_reason,
                "feed_pinned": bool(x.feed_pinned),
                "user_id": x.user_id,
                "media": list(x.media or []),
                "photos": list(x.photos or []),
                "description": x.description or "",
                "tags": list(x.tags or []),
                "district": x.district,
                "price_text": x.price_text,
                "updated_at": x.updated_at,
            }
        )
    return out
