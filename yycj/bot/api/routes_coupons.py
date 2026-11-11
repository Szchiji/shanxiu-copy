"""优惠券：老师在「我的」发，客人在详情页领取，后台按城市查看 / 停用。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from bot.api.deps import get_admin_user_id, get_current_user_id
from bot.api.routes_core import router
from bot.services import city_scope, coupons, home_service


class CouponItem(BaseModel):
    text: str = ""
    quota: int = 0


class CouponBody(BaseModel):
    """New form: ``items`` (1–5, each with 数量). ``text`` / ``quota`` = old single-coupon body (cached clients)."""
    text: str = ""
    days: int = 2
    quota: int = 0
    items: Optional[List[CouponItem]] = None


class UseBody(BaseModel):
    code: str
    used: bool = True


async def _enabled() -> bool:
    s = await home_service.get_or_create_settings()
    return bool(s.get("coupons_enabled", True))


async def _role(user_id: int) -> str:
    from sqlalchemy import select
    from bot.db import session_scope
    from bot.models import User
    async with session_scope() as s:
        return (await s.execute(select(User.role).where(User.user_id == user_id))).scalar_one_or_none() or ""


def _bad(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


@router.get("/me/coupon")
async def api_my_coupon(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    return {"ok": True, "enabled": await _enabled(), **(await coupons.mine(user_id))}


@router.post("/me/coupon")
async def api_my_coupon_create(body: CouponBody, user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    if not await _enabled():
        raise HTTPException(status_code=400, detail="优惠功能暂未开放")
    if await _role(user_id) not in ("teacher", "merchant"):
        raise HTTPException(status_code=403, detail="只有老师 / 商家可以发优惠")
    try:
        if body.items is not None:
            await coupons.create(user_id, days=body.days, items=[i.model_dump() if hasattr(i, "model_dump") else i.dict() for i in body.items])
        else:
            await coupons.create(user_id, body.text, body.days, body.quota)
    except coupons.CouponError as e:
        _bad(e)
    return {"ok": True, **(await coupons.mine(user_id))}


@router.post("/me/coupon/end")
async def api_my_coupon_end(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    await coupons.end(user_id)
    return {"ok": True, **(await coupons.mine(user_id))}


@router.post("/me/coupon/use")
async def api_my_coupon_use(body: UseBody, user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    if not await coupons.mark_used(user_id, body.code, body.used):
        raise HTTPException(status_code=404, detail="没找到这个码")
    return {"ok": True, **(await coupons.mine(user_id))}


@router.get("/me/coupon/code")
async def api_my_coupon_code(code: str = "", user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    """老师核销：按券码查（当前和以前发的优惠都能查）。"""
    hit = await coupons.lookup(user_id, code)
    if not hit:
        raise HTTPException(status_code=404, detail="没找到这个券码")
    return {"ok": True, "claim": hit}


@router.get("/me/claims")
async def api_my_claims(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    return {"ok": True, **(await coupons.my_claims(user_id))}


@router.get("/lamps/{lamp_id}/coupon")
async def api_lamp_coupon(lamp_id: str, user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    if not await _enabled():
        return {"ok": True, "coupon": None}
    return {"ok": True, "coupon": await coupons.for_lamp(lamp_id, user_id)}


@router.post("/coupons/{coupon_id}/claim")
async def api_coupon_claim(coupon_id: str, user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    try:
        c = await coupons.claim(user_id, coupon_id)
    except coupons.CouponError as e:
        _bad(e)
    return {"ok": True, "coupon": c}


@router.get("/coupons")
async def api_city_coupons(city: Optional[str] = None, user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    if not await _enabled():
        return {"ok": True, "items": []}
    return {"ok": True, "items": await coupons.city_list(city)}


@router.get("/admin/coupons")
async def api_admin_coupons(city: Optional[str] = None, status: str = "active",
                            admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    cities = await city_scope.effective(admin_id, city)
    return {"ok": True, "items": await coupons.admin_list(cities, "all" if status == "all" else "active")}


@router.post("/admin/coupons/{coupon_id}/stop")
async def api_admin_coupon_stop(coupon_id: str, admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    from sqlalchemy import select
    from bot.db import session_scope
    from bot.models_extra import Coupon
    async with session_scope() as s:
        city = (await s.execute(select(Coupon.city).where(Coupon.coupon_id == coupon_id))).scalar_one_or_none()
    if city is None:
        raise HTTPException(status_code=404, detail="优惠不存在")
    await city_scope.ensure_city(admin_id, city)
    await coupons.admin_stop(coupon_id, admin_id)
    return {"ok": True}
