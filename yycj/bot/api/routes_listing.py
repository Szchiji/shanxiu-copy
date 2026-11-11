"""我的上架、改稿、管理员代上架。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, field_validator, Field

from bot.api.deps import get_admin_user_id, get_current_user_id
from bot.api.routes_core import MediaItem, _normalize_media, _ser_dt, router
from bot.models import LampStatus, UserRole
from bot.services import listing_flow, listing_notify, search_service, users
from bot.services.admin_ops import notify_user_best_effort
from bot.services.broadcast import update_broadcast
from bot.services.extras_store import save_extras, save_user_fields

logger = logging.getLogger(__name__)


class RelistBody(BaseModel):
    operation_id: str = ""
    expected_updated_at: str = ""


class EditBody(BaseModel):
    operation_id: Optional[str] = None
    city: str
    title: str
    price: Optional[int] = None
    price_text: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    description: str = ""
    photos: List[str] = Field(default_factory=list)
    media: List[MediaItem] = Field(default_factory=list)
    district: Optional[str] = None
    approx_label: Optional[str] = None
    extras: Optional[Dict[str, Any]] = None
    hours: Optional[Any] = None

    @field_validator("hours", mode="after")
    @classmethod
    def _norm_hours(cls, v):
        from bot.services.open_shift import normalize_hours
        return normalize_hours(v) if v is not None else None

    @field_validator("extras", mode="after")
    @classmethod
    def _clean_extras(cls, v):
        from bot.services.extras_clean import clean_user_extras
        return clean_user_extras(v) if v is not None else None


class ProxyBody(EditBody):
    target_user_id: int
    publisher_role: str = "teacher"


@router.get("/me/listings")
async def api_me_listings(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    from bot.services.open_shift import REMIND_LEAD_MIN, normalize_hours, shop_status
    items = await listing_flow.list_my_lamps(user_id)
    for it in items:
        extras = it.get("extras") if isinstance(it.get("extras"), dict) else {}
        try:
            it["shop"] = shop_status(extras, it.get("status"), it.get("expires_at"))
        except Exception:
            it["shop"] = {}
        it["hours"] = normalize_hours(extras.get("_hours") or extras.get("hours"))
        it["extras"] = {k: v for k, v in extras.items() if not str(k).startswith("_")}
        it["expires_at"] = _ser_dt(it.get("expires_at"))
    try:
        from bot.services.listing_stats import stats_for_lamps
        stats = await stats_for_lamps([it.get("lamp_id") for it in items])
        for it in items:
            it["stats"] = stats.get(str(it.get("lamp_id")), {})
    except Exception:
        logger.exception("listing stats failed")
    show = {"views": True, "favs": True, "contacts": True}
    try:
        from bot.services import home_service
        st = await home_service.get_or_create_settings()
        show = {
            "views": bool(st.get("teacher_show_views", True)),
            "favs": bool(st.get("teacher_show_favs", True)),
            "contacts": bool(st.get("teacher_show_contacts", True)),
        }
    except Exception:
        logger.exception("teacher stats flags failed")
    from bot.services.listing_edits import latest
    states=await latest([x["lamp_id"] for x in items])
    from bot.services.relist_delivery import latest as relist_latest
    relists=await relist_latest([x["lamp_id"] for x in items])
    for x in items:
        x["edit_state"]=states.get(x["lamp_id"]);x["relist_state"]=relists.get(x["lamp_id"])
        x["updated_at"]=_ser_dt(x.get("updated_at"))
    return {"ok": True, "items": items, "remind_lead_min": REMIND_LEAD_MIN, "stats_show": show}


@router.post("/me/listings/{lamp_id}/edit")
async def api_me_edit(
    lamp_id: str,
    body: EditBody,
    user_id: int = Depends(get_current_user_id),
) -> Dict[str, Any]:
    await users.ensure_user(user_id)
    media = _normalize_media(body.media, body.photos)
    mine = await listing_flow.list_my_lamps(user_id)
    current = next((x for x in mine if x.get("lamp_id") == lamp_id), None)
    if not current:
        raise HTTPException(status_code=404, detail="找不到你的这条资料")
    payload = {
        "city": body.city.strip()[:32],
        "title": body.title.strip()[:64],
        "tags": [t.strip()[:24] for t in body.tags if t.strip()][:5],
        "price": body.price,
        "price_text": (body.price_text or "")[:32],
        "description": (body.description or "")[:2000],
        "photos": [m.get("url") or m.get("file_id") for m in media if (m.get("type") or "image") == "image"],
        "media": media,
        "district": (body.district or "").strip()[:64] or None,
        "approx_label": (body.approx_label or "").strip()[:128] or None,
        "extras": dict(body.extras or {}),
        "hours": body.hours,
    }
    from bot.services import listing_edits
    try:
        result = await listing_edits.submit(lamp_id,user_id,payload,body.operation_id)
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    return {"ok":True,"operation":result,"instant":result["text_saved"],"post_id":result.get("post_id")}



@router.post("/admin/listings/proxy")
async def api_admin_proxy(
    body: ProxyBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    from bot.services import city_scope
    await city_scope.ensure_city(admin_id, body.city)
    media = _normalize_media(body.media, body.photos)
    try:
        lamp = await listing_flow.proxy_publish(
            target_user_id=body.target_user_id,
            city=body.city,
            title=body.title,
            price=body.price,
            price_text=body.price_text,
            tags=body.tags,
            description=body.description,
            media=media,
            photos=[m["url"] for m in media if m["type"] == "image"],
            district=body.district,
            approx_label=body.approx_label,
            publisher_role=body.publisher_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        await save_user_fields((lamp or {}).get("lamp_id"), {"extras": body.extras, "hours": body.hours})
        await listing_flow.broadcast_listing(lamp, body.extras)
    except Exception:
        pass
    try:
        await notify_user_best_effort(
            body.target_user_id,
            f"管理员已为你上架「{lamp.get('title')}」，客人点联系会打到你的机器人私聊。",
        )
    except Exception:
        pass
    return {"ok": True, "lamp": lamp, "by": admin_id}


@router.post("/admin/listings/{lamp_id}/proxy-edit")
async def api_admin_proxy_edit(
    lamp_id: str,
    body: EditBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    from bot.services import city_scope
    await city_scope.guard(admin_id, "lamp", lamp_id)
    if body.city:
        await city_scope.ensure_city(admin_id, body.city)
    media = _normalize_media(body.media, body.photos)
    payload = {
        "city": body.city,
        "title": body.title,
        "price": body.price,
        "price_text": body.price_text,
        "tags": body.tags,
        "description": body.description,
        "district": body.district,
        "approx_label": body.approx_label,
        "extras": dict(body.extras or {}),
        "hours": body.hours,
    }
    if media:
        payload["media"] = media
        payload["photos"] = [m["url"] for m in media if m["type"] == "image"]
    from bot.services import listing_edits
    try:
        result = await listing_edits.submit(lamp_id,admin_id,payload,body.operation_id,admin=True)
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    return {"ok":True,"operation":result,"by":admin_id}



@router.post("/me/listings/{lamp_id}/unlist")
async def api_me_unlist(lamp_id: str, user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    from bot.services import listing_ops
    try:
        out = await listing_ops.teacher_unlist(lamp_id, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **out}


@router.post("/me/listings/{lamp_id}/relist")
async def api_me_relist(lamp_id: str, body: RelistBody = RelistBody(), user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    from bot.services import listing_ops
    try:
        out = await listing_ops.teacher_relist(lamp_id,user_id,operation_id=body.operation_id,expected_updated_at=body.expected_updated_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "operation":out, "message":out.get("message")}


@router.get("/me/todos")
async def api_me_todos(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    """「我的」顶部待办计数。客人：可用优惠券、收藏。老师：3 天内到期的上架、待核销优惠。"""
    from datetime import datetime, timedelta
    from sqlalchemy import func, select
    from bot.models_extra import Favorite
    from bot.services import coupons, users

    u = await users.ensure_user(user_id)
    role = u.get("role") or "guest"
    if role in ("teacher", "merchant"):
        items = await listing_flow.list_my_lamps(user_id)
        now = datetime.utcnow()
        until = now + timedelta(days=3)
        expiring = 0
        for it in items:
            if it.get("status") != "active":
                continue
            exp = it.get("expires_at")
            if not exp:
                continue
            if getattr(exp, "tzinfo", None):
                exp = exp.replace(tzinfo=None)
            if now < exp <= until:
                expiring += 1
        pending = 0
        try:
            mine = await coupons.mine(user_id)
            pending = int((mine.get("current") or {}).get("pending") or 0)
            pending += sum(int(b.get("pending") or 0) for b in (mine.get("history") or []))
        except Exception:
            logger.exception("teacher coupon todos failed")
        return {"ok": True, "role": "teacher", "expiring": expiring, "pending": pending}
    favs = 0
    usable = 0
    try:
        from bot.db import session_scope
        async with session_scope() as s:
            favs = int((await s.execute(select(func.count()).select_from(Favorite).where(Favorite.user_id == user_id))).scalar() or 0)
    except Exception:
        logger.exception("fav todos failed")
    try:
        usable = int((await coupons.my_claims(user_id)).get("usable") or 0)
    except Exception:
        logger.exception("coupon claim todos failed")
    return {"ok": True, "role": "guest", "coupons": usable, "favs": favs}


class EditRetryBody(BaseModel):
    step: str


@router.get("/me/listings/{lamp_id}/edit-state")
async def api_me_edit_state(lamp_id: str, user_id: int = Depends(get_current_user_id)):
    from bot.services import listing_edits
    lamp=await search_service.get_lamp(lamp_id)
    if not lamp or int(lamp.get("user_id") or 0)!=user_id:raise HTTPException(status_code=404,detail="找不到你的资料")
    return {"operation":(await listing_edits.latest([lamp_id])).get(lamp_id)}


@router.get("/admin/listings/{lamp_id}/edit-state")
async def api_admin_edit_state(lamp_id: str, admin_id: int = Depends(get_admin_user_id)):
    from bot.services import listing_edits,city_scope
    await city_scope.guard(admin_id,"lamp",lamp_id)
    return {"operation":(await listing_edits.latest([lamp_id])).get(lamp_id)}


async def _retry_edit(key,body,actor,admin):
    from bot.services import listing_edits,city_scope
    from bot.models import ListingEditOperation
    from bot.db import session_scope
    async with session_scope() as s:
        op=await s.get(ListingEditOperation,key)
        if not op:raise HTTPException(status_code=404,detail="修改记录不存在")
        lamp_id=op.lamp_id
    if admin:await city_scope.guard(actor,"lamp",lamp_id)
    else:await users.ensure_user(actor)
    try:result=await listing_edits.retry(key,body.step,actor,admin=admin)
    except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc
    return {"operation":result}


@router.post("/me/edit-operations/{operation_id}/retry")
async def api_me_edit_retry(operation_id: str,body: EditRetryBody,user_id: int=Depends(get_current_user_id)):
    return await _retry_edit(operation_id,body,user_id,False)


@router.post("/admin/edit-operations/{operation_id}/retry")
async def api_admin_edit_retry(operation_id: str,body: EditRetryBody,admin_id: int=Depends(get_admin_user_id)):
    return await _retry_edit(operation_id,body,admin_id,True)


@router.post('/me/relist-operations/{key}/retry')
async def api_me_relist_retry(key: str, body: EditRetryBody, user_id: int = Depends(get_current_user_id)):
    from bot.services.relist_delivery import retry
    try:return {'operation':await retry(key,body.step,user_id)}
    except ValueError as exc:raise HTTPException(409,detail=str(exc)) from exc


@router.post('/admin/relist-operations/{key}/retry')
async def api_admin_relist_retry(key: str, body: EditRetryBody, admin_id: int = Depends(get_admin_user_id)):
    from bot.services.relist_delivery import retry
    try:return {'operation':await retry(key,body.step,admin_id,admin=True)}
    except ValueError as exc:raise HTTPException(409,detail=str(exc)) from exc


class RestoreBody(BaseModel):
    operation_id: Optional[str] = None
    expected_updated_at: str = ""
    confirm: bool = False
    include_media: bool = False


async def _history_access(lamp_id, actor, admin):
    from bot.services import listing_history, city_scope, search_service
    if admin:
        await city_scope.guard(actor, "lamp", lamp_id)
        return
    lamp = await search_service.get_lamp(lamp_id)
    if not lamp or int(lamp.get("user_id") or 0) != int(actor):
        raise HTTPException(status_code=404, detail="找不到你的资料")


@router.get("/me/listings/{lamp_id}/edit-history")
async def api_me_edit_history(lamp_id: str, user_id: int = Depends(get_current_user_id)):
    from bot.services import listing_history
    await _history_access(lamp_id, user_id, False)
    try:
        return await listing_history.history(lamp_id, user_id, admin=False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/admin/listings/{lamp_id}/edit-history")
async def api_admin_edit_history(lamp_id: str, admin_id: int = Depends(get_admin_user_id)):
    from bot.services import listing_history
    await _history_access(lamp_id, admin_id, True)
    try:
        return await listing_history.history(lamp_id, admin_id, admin=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/me/listings/{lamp_id}/edit-history/{version_id}")
async def api_me_edit_history_preview(lamp_id: str, version_id: str, user_id: int = Depends(get_current_user_id)):
    from bot.services import listing_history
    await _history_access(lamp_id, user_id, False)
    try:
        return await listing_history.preview(lamp_id, version_id, user_id, admin=False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/admin/listings/{lamp_id}/edit-history/{version_id}")
async def api_admin_edit_history_preview(lamp_id: str, version_id: str, admin_id: int = Depends(get_admin_user_id)):
    from bot.services import listing_history
    await _history_access(lamp_id, admin_id, True)
    try:
        return await listing_history.preview(lamp_id, version_id, admin_id, admin=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _restore(lamp_id, version_id, body, actor, admin):
    from bot.services import listing_history, users
    await _history_access(lamp_id, actor, admin)
    if not admin:
        await users.ensure_user(actor)
    try:
        result = await listing_history.restore(
            lamp_id, version_id, actor, body.operation_id,
            admin=admin, expected_updated_at=body.expected_updated_at,
            confirm=body.confirm, include_media=body.include_media,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "operation": result}


@router.post("/me/listings/{lamp_id}/edit-history/{version_id}/restore")
async def api_me_restore(lamp_id: str, version_id: str, body: RestoreBody, user_id: int = Depends(get_current_user_id)):
    return await _restore(lamp_id, version_id, body, user_id, False)


@router.post("/admin/listings/{lamp_id}/edit-history/{version_id}/restore")
async def api_admin_restore(lamp_id: str, version_id: str, body: RestoreBody, admin_id: int = Depends(get_admin_user_id)):
    return await _restore(lamp_id, version_id, body, admin_id, True)
