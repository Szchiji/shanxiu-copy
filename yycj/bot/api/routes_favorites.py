"""客人收藏。"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends, HTTPException
from sqlalchemy import delete, select

from bot.api.deps import get_current_user_id
from bot.api.routes_core import router
from bot.db import session_scope
from bot.models_extra import Favorite
from bot.services import search_service, users


@router.get("/me/favorites")
async def api_my_favorites(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    async with session_scope() as s:
        res = await s.execute(
            select(Favorite).where(Favorite.user_id == user_id).order_by(Favorite.created_at.desc()).limit(100)
        )
        rows = list(res.scalars().all())
    items = []
    for row in rows:
        lamp = await search_service.get_lamp(row.lamp_id)
        if lamp:
            items.append(lamp)
    return {"ok": True, "items": items, "ids": [r.lamp_id for r in rows]}


@router.post("/favorites/{lamp_id}")
async def api_toggle_favorite(
    lamp_id: str,
    user_id: int = Depends(get_current_user_id),
) -> Dict[str, Any]:
    u = await users.ensure_user(user_id)
    if (u.get("role") or "guest") != "guest":
        raise HTTPException(status_code=403, detail="仅客人可收藏")
    async with session_scope() as s:
        res = await s.execute(
            select(Favorite).where(Favorite.user_id == user_id, Favorite.lamp_id == lamp_id)
        )
        row = res.scalar_one_or_none()
        if row:
            await s.delete(row)
            on = False
        else:
            s.add(Favorite(user_id=user_id, lamp_id=lamp_id))
            on = True
    return {"ok": True, "lamp_id": lamp_id, "favorited": on}
