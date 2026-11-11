"""常用位置 + 地图选点用的区中心（公开地图数据）。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from bot.api.deps import get_current_user_id
from bot.api.routes_core import router
from bot.services import places


class PlaceBody(BaseModel):
    lat: float
    lng: float


@router.get("/me/place")
async def api_me_place(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    # only the owner gets their own (already rounded) point back, to centre the map when editing
    return {"ok": True, "place": await places.describe(user_id)}


@router.post("/me/place")
async def api_me_place_set(body: PlaceBody, user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    if not await places.set_point(user_id, body.lat, body.lng):
        raise HTTPException(status_code=400, detail="位置不对，请在地图上重新选点")
    _bust_feed_caches()
    return {"ok": True, "place": await places.describe(user_id)}


@router.delete("/me/place")
async def api_me_place_clear(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    await places.clear(user_id)
    _bust_feed_caches()
    return {"ok": True, "place": await places.describe(user_id)}


@router.get("/geo/centers")
async def api_geo_centers(city: Optional[str] = None, user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    """Public district centres so the map opens on the right 区. No listing data here."""
    from bot.services.feed_filters import CENTERS
    table = CENTERS.get(str(city or "")) or {}
    pts = list(table.values())
    center = [round(sum(p[0] for p in pts) / len(pts), 3), round(sum(p[1] for p in pts) / len(pts), 3)] if pts else None
    return {"ok": True, "city": city or "", "center": center, "districts": {k: list(v) for k, v in table.items()}}


def _bust_feed_caches() -> None:
    try:
        from bot.services import feed_filters as ff
        ff._FACETS.clear()
    except Exception:
        pass
