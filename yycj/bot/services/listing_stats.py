"""浏览 / 收藏 / 匿名联系 counts (teacher 我的上架) and the admin per-city daily dashboard.

浏览 is the only new data (table lamp_views: one counter per listing per Beijing day; every detail
open counts, the owner's own opens don't).
收藏 comes from favorites, 匿名联系 from sessions (one session = one 匿名联系 request), 评价 from reviews,
新上架 from lamps.created_at; nothing existing is altered.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bot.db import session_scope
from bot.models import Lamp, Review, Session
from bot.models_extra import Favorite, LampView

logger = logging.getLogger(__name__)


BJ = timedelta(hours=8)


def bj_today() -> date:
    return (datetime.utcnow() + BJ).date()


async def record_view(lamp_id: str, viewer_id: Optional[int], owner_id: Optional[int]) -> None:
    """Count a detail open (+1 on today's Beijing-day counter). The owner's own opens never count."""
    if not lamp_id or not viewer_id or (owner_id is not None and int(viewer_id) == int(owner_id)):
        return
    try:
        async with session_scope() as s:
            stmt = pg_insert(LampView).values(lamp_id=str(lamp_id), day=bj_today(), views=1)
            await s.execute(stmt.on_conflict_do_update(constraint="uq_lamp_views_day", set_={"views": LampView.views + 1}))
    except Exception:
        logger.exception("record view failed %s", lamp_id)


_PENDING: set = set()


def record_view_soon(lamp_id: str, viewer_id: Optional[int], owner_id: Optional[int]) -> None:
    """Fire-and-forget so counting never slows the detail response."""
    import asyncio
    try:
        t = asyncio.get_running_loop().create_task(record_view(lamp_id, viewer_id, owner_id))
        _PENDING.add(t)
        t.add_done_callback(_PENDING.discard)
    except RuntimeError:
        pass


def _since(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days)


async def stats_for_lamps(lamp_ids: Iterable[str], days: int = 7) -> Dict[str, Dict[str, int]]:
    ids = [str(x) for x in lamp_ids if x]
    out: Dict[str, Dict[str, int]] = {
        i: {"views": 0, "views_7d": 0, "favs": 0, "favs_7d": 0, "contacts": 0, "contacts_7d": 0} for i in ids
    }
    if not ids:
        return out
    since = _since(days)
    since_day = bj_today() - timedelta(days=days - 1)
    async with session_scope() as s:
        async def grouped(col_id, stmt_total, stmt_recent, key):
            for lid, n in (await s.execute(stmt_total)).all():
                out[str(lid)][key] = int(n or 0)
            for lid, n in (await s.execute(stmt_recent)).all():
                out[str(lid)][key + "_7d"] = int(n or 0)

        await grouped(
            LampView.lamp_id,
            select(LampView.lamp_id, func.sum(LampView.views)).where(LampView.lamp_id.in_(ids)).group_by(LampView.lamp_id),
            select(LampView.lamp_id, func.sum(LampView.views)).where(LampView.lamp_id.in_(ids), LampView.day >= since_day).group_by(LampView.lamp_id),
            "views",
        )
        await grouped(
            Favorite.lamp_id,
            select(Favorite.lamp_id, func.count()).where(Favorite.lamp_id.in_(ids)).group_by(Favorite.lamp_id),
            select(Favorite.lamp_id, func.count()).where(Favorite.lamp_id.in_(ids), Favorite.created_at >= since).group_by(Favorite.lamp_id),
            "favs",
        )
        await grouped(
            Session.lamp_id,
            select(Session.lamp_id, func.count()).where(Session.lamp_id.in_(ids)).group_by(Session.lamp_id),
            select(Session.lamp_id, func.count()).where(Session.lamp_id.in_(ids), Session.created_at >= since).group_by(Session.lamp_id),
            "contacts",
        )
    return out


async def daily_dashboard(days: int = 14, cities: Optional[List[str]] = None) -> Dict[str, Any]:
    """Per city, per day (UTC+8 calendar day): 新上架 / 浏览 / 匿名联系 / 评价."""
    days = max(1, min(int(days or 14), 90))
    tz = timedelta(hours=8)
    start = (datetime.utcnow() + tz).date() - timedelta(days=days - 1)
    start_utc = datetime.combine(start, datetime.min.time()) - tz
    rows: Dict[tuple, Dict[str, int]] = {}

    def bump(day: Any, city: Optional[str], key: str, n: int) -> None:
        c = city or "未设置"
        if cities is not None and c not in cities:
            return
        d = day.isoformat() if hasattr(day, "isoformat") else str(day)[:10]
        r = rows.setdefault((d, c), {"listings": 0, "views": 0, "contacts": 0, "reviews": 0})
        r[key] += int(n or 0)

    local_day = lambda col: func.date(col + literal_column("interval '8 hours'"))  # noqa: E731  (UTC+8 calendar day)
    async with session_scope() as s:
        q = select(local_day(Lamp.created_at), Lamp.city, func.count()).where(Lamp.created_at >= start_utc).group_by(local_day(Lamp.created_at), Lamp.city)
        for d, c, n in (await s.execute(q)).all():
            bump(d, c, "listings", n)
        # lamp_views.day is already the Beijing calendar day
        q = (select(LampView.day, Lamp.city, func.sum(LampView.views)).join(Lamp, Lamp.lamp_id == LampView.lamp_id)
             .where(LampView.day >= start).group_by(LampView.day, Lamp.city))
        for d, c, n in (await s.execute(q)).all():
            bump(d, c, "views", n)
        q = (select(local_day(Session.created_at), Lamp.city, func.count()).join(Lamp, Lamp.lamp_id == Session.lamp_id)
             .where(Session.created_at >= start_utc).group_by(local_day(Session.created_at), Lamp.city))
        for d, c, n in (await s.execute(q)).all():
            bump(d, c, "contacts", n)
        q = (select(local_day(Review.created_at), Lamp.city, func.count()).join(Lamp, Lamp.lamp_id == Review.lamp_id)
             .where(Review.created_at >= start_utc).group_by(local_day(Review.created_at), Lamp.city))
        for d, c, n in (await s.execute(q)).all():
            bump(d, c, "reviews", n)
    day_list = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    city_list = sorted({c for (_, c) in rows})
    totals = {c: {"listings": 0, "views": 0, "contacts": 0, "reviews": 0} for c in city_list}
    for (d, c), r in rows.items():
        for k, v in r.items():
            totals[c][k] += v
    return {
        "days": day_list,
        "cities": city_list,
        "rows": [dict(day=d, city=c, **r) for (d, c), r in sorted(rows.items())],
        "totals": totals,
    }


async def expiring_listings(days: int = 7, cities: Optional[List[str]] = None, limit: int = 200) -> List[Dict[str, Any]]:
    from bot.services.extras_store import load_extras
    now = datetime.utcnow()
    async with session_scope() as s:
        cond = [Lamp.status == "active", Lamp.expires_at.is_not(None), Lamp.expires_at > now, Lamp.expires_at <= now + timedelta(days=days)]
        if cities is not None:
            cond.append(Lamp.city.in_(cities))
        res = await s.execute(select(Lamp).where(and_(*cond)).order_by(Lamp.expires_at.asc()).limit(limit))
        items = [
            {"lamp_id": x.lamp_id, "title": x.title, "city": x.city, "district": x.district, "user_id": x.user_id,
             "expires_at": x.expires_at}
            for x in res.scalars().all()
        ]
    for it in items:
        left = it["expires_at"] - now
        it["hours_left"] = int(left.total_seconds() // 3600)
        ex = await load_extras(it["lamp_id"]) or {}
        it["nudged_3d"] = bool(ex.get("_expiry_nudge"))
        it["nudged_1d"] = bool(ex.get("_expiry_nudge_1d"))
        it["expires_at"] = it["expires_at"].isoformat() + "Z"
    return items
