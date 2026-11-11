"""轮播置顶：一次查 pin + lamp，不逐条 get_lamp。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import or_, select

from bot.db import session_scope
from bot.models import HomepagePin, Lamp, LampStatus
from bot.services import search_service


async def list_active_pins() -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    async with session_scope() as s:
        res = await s.execute(
            select(HomepagePin)
            .where(or_(HomepagePin.expires_at.is_(None), HomepagePin.expires_at > now))
            .order_by(HomepagePin.sort_order.asc(), HomepagePin.id.asc())
        )
        pins = list(res.scalars().all())
        ids = [p.lamp_id for p in pins]
        lamps: Dict[str, Dict[str, Any]] = {}
        if ids:
            r2 = await s.execute(select(Lamp).where(Lamp.lamp_id.in_(ids)))
            for lamp in r2.scalars().all():
                lamps[lamp.lamp_id] = search_service._lamp_to_dict(lamp)
    out: List[Dict[str, Any]] = []
    for p in pins:
        lamp = lamps.get(p.lamp_id)
        if not lamp or lamp.get("status") != LampStatus.ACTIVE.value:
            continue
        out.append(
            {
                "id": p.id,
                "lamp_id": p.lamp_id,
                "sort_order": p.sort_order,
                "expires_at": p.expires_at,
                "lamp": lamp,
            }
        )
    return out
