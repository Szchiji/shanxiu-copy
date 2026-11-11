"""常用位置 (common location).

Both guests and teachers pick a point on a map. We only ever store it snapped to a ~500 m grid
(0.005°), and nobody else ever sees it: other users only get a rounded 「约Xkm」.

For a teacher the same point is the approximate location of their listings: saving it updates
every listing they own right away (no re-review), and a pending submission too.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select

from bot.db import session_scope
from bot.models import Lamp, Post, PostStatus
from bot.models_extra import UserPlace

GRID = 0.005  # ≈ 550 m north-south, ≈ 500 m east-west in south China


def snap(lat: Any, lng: Any) -> Optional[Tuple[float, float]]:
    try:
        la, ln = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (-85 <= la <= 85 and -180 <= ln <= 180) or (la == 0 and ln == 0):
        return None
    return (round(round(la / GRID) * GRID, 3), round(round(ln / GRID) * GRID, 3))


async def get_point(user_id: int) -> Optional[Tuple[float, float]]:
    async with session_scope() as s:
        row = (await s.execute(select(UserPlace).where(UserPlace.user_id == user_id))).scalar_one_or_none()
        return (row.lat, row.lng) if row else None


async def listing_point(user_id: int) -> Optional[Tuple[float, float]]:
    """A teacher who pinned before 常用位置 existed: their newest listing's point."""
    async with session_scope() as s:
        row = (await s.execute(
            select(Lamp.approx_lat, Lamp.approx_lng).where(Lamp.user_id == user_id, Lamp.approx_lat.is_not(None))
            .order_by(Lamp.id.desc()).limit(1)
        )).first()
    return (row[0], row[1]) if row and row[1] is not None else None


async def describe(user_id: int) -> Dict[str, Any]:
    pt = await get_point(user_id)
    src = "saved" if pt else ""
    if not pt:
        pt = await listing_point(user_id)
        src = "listing" if pt else ""
    async with session_scope() as s:
        rows = (await s.execute(select(Lamp.city, Lamp.district).where(Lamp.user_id == user_id).order_by(Lamp.id.desc()))).all()
    # city / 区 of the newest listing: where the map opens for a teacher who has not pinned yet
    return {"set": bool(pt), "lat": pt[0] if pt else None, "lng": pt[1] if pt else None,
            "source": src, "listings": len(rows), "grid_m": 500,
            "city": rows[0][0] if rows else None, "district": rows[0][1] if rows else None}


async def _sync_listings(s, user_id: int, pt: Optional[Tuple[float, float]]) -> int:
    lamps = (await s.execute(select(Lamp).where(Lamp.user_id == user_id))).scalars().all()
    for lamp in lamps:
        lamp.approx_lat, lamp.approx_lng = (pt if pt else (None, None))
        if pt:
            lamp.approx_label = None  # the old free-text 大致位置 is replaced by the pin
    posts = (await s.execute(select(Post).where(Post.user_id == user_id, Post.status == PostStatus.PENDING.value))).scalars().all()
    for p in posts:
        d = dict(p.lamp_data or {})
        d["approx_lat"], d["approx_lng"] = (pt if pt else (None, None))
        if pt:
            d["approx_label"] = None
        p.lamp_data = d
    return len(lamps)


async def set_point(user_id: int, lat: Any, lng: Any, *, sync: bool = True) -> Optional[Tuple[float, float]]:
    pt = snap(lat, lng)
    if not pt:
        return None
    async with session_scope() as s:
        row = (await s.execute(select(UserPlace).where(UserPlace.user_id == user_id))).scalar_one_or_none()
        if row:
            row.lat, row.lng = pt
        else:
            s.add(UserPlace(user_id=user_id, lat=pt[0], lng=pt[1]))
        if sync:
            await _sync_listings(s, user_id, pt)
    return pt


async def clear(user_id: int) -> None:
    async with session_scope() as s:
        row = (await s.execute(select(UserPlace).where(UserPlace.user_id == user_id))).scalar_one_or_none()
        if row:
            await s.delete(row)
        await _sync_listings(s, user_id, None)
