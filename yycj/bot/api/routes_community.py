"""City hub and explainable 30-day review ranking; no writes or Telegram sends."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends
from sqlalchemy import select, func, distinct, or_

from bot.api.deps import get_current_user_id
from bot.api.routes_core_base import router
from bot.db import session_scope
from bot.models import Lamp, LampStatus, Review, ReviewStatus
from bot.services import home_service
from bot.services.home_feed import ops_for_city
from bot.services.community import public_links, channel_url


async def city_site(city):
    base = await home_service.get_or_create_settings()
    enabled = base.get("enabled_cities") or []
    use = city if city and city in enabled else (enabled[0] if enabled else "")
    return use, ops_for_city(base, use)


@router.get("/community")
async def api_community(
    city: Optional[str] = None, user_id: int = Depends(get_current_user_id)
):
    use, site = await city_site(city)
    links = public_links(site)
    # Use the LAST REFRESHED reference; settings changes still wait until 12:00.
    from bot.services.channel_directory import _ref

    chat, mid = await _ref(use)
    if mid:
        url = channel_url(chat, mid)
        if url:
            links.insert(1, {"key": "directory", "label": "按区目录", "url": url})
    return {"ok": True, "city": use, "links": links}


@router.get("/community/ranking")
async def api_city_ranking(
    city: Optional[str] = None, user_id: int = Depends(get_current_user_id)
):
    use, _ = await city_site(city)
    # One reviewer contributes once per listing. Ties share a rank; id is a stable display tiebreak.
    counts = (
        select(Review.lamp_id, func.count(distinct(Review.guest_id)).label("n"))
        .where(
            Review.status == ReviewStatus.APPROVED.value,
            Review.reviewed_at.is_not(None),
            or_(Review.review_note.is_(None), Review.review_note != "免审直接发布"),
            Review.created_at >= datetime.utcnow() - timedelta(days=30),
        )
        .group_by(Review.lamp_id)
        .subquery()
    )
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Lamp.lamp_id, Lamp.title, Lamp.district, counts.c.n)
                .join(counts, counts.c.lamp_id == Lamp.lamp_id)
                .where(Lamp.status == LampStatus.ACTIVE.value, Lamp.city == use)
                .order_by(counts.c.n.desc(), Lamp.lamp_id)
                .limit(30)
            )
        ).all()
    items, rank, prev = [], 0, None
    for i, (lid, title, area, n) in enumerate(rows):
        if n != prev:
            rank = i + 1
        items.append(
            {
                "lamp_id": lid,
                "title": title,
                "district": area,
                "review_count": n,
                "rank": rank,
            }
        )
        prev = n
    return {
        "ok": True,
        "city": use,
        "items": items,
        "rule": "近30天审核通过评价；每位用户每份资料计一次，并列同名次。审核通过不代表体验担保；少量样本仅供参考。",
    }
