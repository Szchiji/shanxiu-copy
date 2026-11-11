"""卡片置顶单条时效。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from bot.api.deps import get_admin_user_id
from bot.api.routes_core import _ser_dt, router
from bot.db import session_scope
from bot.models import Lamp, LampStatus
from bot.services import home_service


class FeedPinHoursBody(BaseModel):
    lamp_id: str
    pinned: bool = True
    expires_hours: Optional[int] = None


@router.post("/admin/homepage/feed-pin")
async def api_feed_pin_hours(
    body: FeedPinHoursBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    from bot.services import city_scope
    await city_scope.guard(admin_id, "lamp", body.lamp_id)
    until = None
    if body.pinned and body.expires_hours not in (None, 0):
        until = datetime.utcnow() + timedelta(hours=max(1, int(body.expires_hours)))
    try:
        data = await home_service.set_feed_pin(body.lamp_id, pinned=body.pinned)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with session_scope() as s:
        res = await s.execute(select(Lamp).where(Lamp.lamp_id == body.lamp_id))
        lamp = res.scalar_one_or_none()
        if not lamp:
            raise HTTPException(status_code=404, detail="资料不存在")
        if lamp.status != LampStatus.ACTIVE.value and body.pinned:
            raise HTTPException(status_code=400, detail="仅已上架可置顶")
        lamp.feed_pinned = bool(body.pinned)
        if hasattr(lamp, "feed_pin_expires_at"):
            lamp.feed_pin_expires_at = until if body.pinned else None
        elif until:
            lamp.approx_label = (lamp.approx_label or "")  # no-op fallback
    data["expires_at"] = _ser_dt(until)
    data["feed_pinned"] = bool(body.pinned)
    return {"ok": True, "feed_pin": data}
