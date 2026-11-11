"""老师恢复每日提醒 / 手动开课休息。"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from bot.api.deps import get_current_user_id
from bot.api.routes_core import router
from bot.services.open_shift import apply_choice, shop_status
from bot.services.extras_store import load_extras
from bot.services import listing_flow


class ShiftBody(BaseModel):
    action: str


@router.post("/me/listings/{lamp_id}/shift")
async def api_me_shift(
    lamp_id: str,
    body: ShiftBody,
    user_id: int = Depends(get_current_user_id),
) -> Dict[str, Any]:
    action = (body.action or "").strip()
    if action not in ("open", "rest", "remind", "remind_on", "remind_off", "auto_on", "auto_off", "pause", "resume"):
        raise HTTPException(status_code=400, detail="action 仅支持 open/rest/pause/resume/remind_on/remind_off/auto_on/auto_off")
    msg = await apply_choice(lamp_id, user_id, action)
    extras = await load_extras(lamp_id)
    mine = await listing_flow.list_my_lamps(user_id)
    current = next((x for x in mine if x.get("lamp_id") == lamp_id), None) or {}
    return {
        "ok": True,
        "message": msg,
        "shop": shop_status(extras, current.get("status"), current.get("expires_at")),
    }
