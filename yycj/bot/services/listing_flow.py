"""改稿覆盖、代上架、到期提醒、频道推送。"""
from __future__ import annotations

from html import escape as _h

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from bot.db import session_scope
from bot.models import Lamp, LampStatus, Post, PostStatus, UserRole
from bot.services import listing_ops, search_service, users
from bot.services.media_urls import enrich_media

logger = logging.getLogger(__name__)


async def list_my_lamps(user_id: int) -> List[Dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(
            select(Lamp).where(Lamp.user_id == user_id).order_by(Lamp.updated_at.desc()).limit(50)
        )
        rows = list(res.scalars().all())
    out = []
    for x in rows:
        media = enrich_media(list(x.media or []), list(x.photos or []))
        out.append(
            {
                "lamp_id": x.lamp_id,
                "title": x.title,
                "city": x.city,
                "district": x.district,
                "price_text": x.price_text,
                "description": x.description or "",
                "status": x.status,
                "expires_at": x.expires_at,
                "unlist_reason": x.unlist_reason,
                "updated_at": x.updated_at,
                "media": media,
                "tags": list(x.tags or []),
                "approx_label": x.approx_label,
            }
        )
    return out


async def apply_edit(lamp_id: str, data: Dict[str, Any], *, owner_id: int, admin: bool = False) -> Dict[str, Any]:
    async with session_scope() as s:
        res = await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))
        lamp = res.scalar_one_or_none()
        if not lamp:
            raise ValueError("资料不存在")
        if not admin and int(lamp.user_id) != int(owner_id):
            raise ValueError("只能改自己的资料")
        old_city, old_live = lamp.city or "", lamp.status == LampStatus.ACTIVE.value
        lamp.city = (data.get("city") or lamp.city)[:32]
        lamp.title = (data.get("title") or lamp.title)[:128]
        lamp.tags = list(data.get("tags") or lamp.tags or [])[:5]
        lamp.price_text = (data.get("price_text") or lamp.price_text or "")[:32] or None
        from bot.services.search_service import clean_price
        lamp.price = clean_price(data.get("price", lamp.price), lamp.price_text)
        lamp.description = (data.get("description") or "")[:2000]
        lamp.district = (data.get("district") or None)
        lamp.approx_label = (data.get("approx_label") or None)
        media = list(data.get("media") or [])
        photos = list(data.get("photos") or [])
        if media or photos:
            lamp.media = media or [{"type": "image", "url": p} for p in photos]
            lamp.photos = [m.get("url") for m in (lamp.media or []) if (m.get("type") or "image") == "image"] or photos
        if lamp.status == LampStatus.HIDDEN.value and lamp.unlist_reason == "expired":
            pass
        elif lamp.status != LampStatus.HIDDEN.value:
            lamp.status = LampStatus.ACTIVE.value
        lamp.updated_at = datetime.utcnow()
        await s.flush()
        out = {"lamp_id": lamp.lamp_id, "title": lamp.title, "status": lamp.status, "expires_at": lamp.expires_at}
        new_city, new_live = lamp.city or "", lamp.status == LampStatus.ACTIVE.value
    if old_live and new_live and old_city != new_city:   # 改城市：旧城少一条、新城多一条
        try:
            from bot.services import channel_directory
            await channel_directory.on_cities_changed([old_city, new_city])
        except Exception:
            logger.exception("directory auto-refresh hook failed")
    return out


async def apply_admin_edit(lamp_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return await apply_edit(lamp_id, data, owner_id=0, admin=True)


async def proxy_publish(
    *,
    target_user_id: int,
    city: str,
    title: str,
    price: Optional[int] = None,
    price_text: Optional[str] = None,
    tags: Optional[List[str]] = None,
    description: str = "",
    media: Optional[List[Dict[str, str]]] = None,
    photos: Optional[List[str]] = None,
    district: Optional[str] = None,
    approx_label: Optional[str] = None,
    publisher_role: str = UserRole.TEACHER.value,
) -> Dict[str, Any]:
    await users.ensure_user(target_user_id)
    media_list = list(media or [])
    photo_list = list(photos or [])
    if not media_list and not photo_list:
        raise ValueError("代上架必须带图或视频")
    lamp = await search_service.create_lamp_from_post(
        user_id=int(target_user_id),
        city=(city or "未知")[:32],
        title=(title or "未命名")[:64],
        tags=list(tags or [])[:5],
        price=price,
        price_text=(price_text or (str(price) if price else "面议"))[:32],
        description=(description or "")[:2000],
        photos=photo_list,
        authenticity_score=80,
        district=district,
        approx_label=approx_label,
        media=media_list,
        publisher_role=publisher_role if publisher_role in ("teacher", "merchant") else "teacher",
    )
    await search_service.approve_lamp(lamp["lamp_id"])
    try:
        await listing_ops.set_listing_expiry(lamp["lamp_id"])
    except Exception:
        logger.exception("proxy expiry failed")
    return lamp


async def remind_expiring(days: int = 3) -> int:
    now = datetime.utcnow()
    until = now + timedelta(days=max(1, days))
    n = 0
    async with session_scope() as s:
        res = await s.execute(
            select(Lamp).where(
                Lamp.status == LampStatus.ACTIVE.value,
                Lamp.expires_at.is_not(None),
                Lamp.expires_at > now,
                Lamp.expires_at <= until,
            )
        )
        lamps = list(res.scalars().all())
    from bot.main import bot

    for lamp in lamps:
        reason = lamp.unlist_reason or ""
        if reason.startswith("reminded:"):
            continue
        left = max(1, int((lamp.expires_at - now).total_seconds() // 86400))
        try:
            await bot.send_message(
                lamp.user_id,
                f"你的资料「{_h(str(lamp.title or ''))}」还有约 {left} 天到期。\n请联系管理员续期，否则会从首页下架。",
            )
            async with session_scope() as s:
                row = (await s.execute(select(Lamp).where(Lamp.lamp_id == lamp.lamp_id))).scalar_one_or_none()
                if row and not (row.unlist_reason or "").startswith("reminded:"):
                    row.unlist_reason = f"reminded:{now.date().isoformat()}"
            n += 1
        except Exception:
            logger.exception("expiry remind failed %s", lamp.lamp_id)
    return n


async def broadcast_listing(lamp: Dict[str, Any], extras: Optional[Dict[str, Any]] = None):
    from bot.services.broadcast import broadcast_listing as _send
    return await _send(lamp, extras)
