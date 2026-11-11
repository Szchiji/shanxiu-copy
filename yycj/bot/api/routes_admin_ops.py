"""管理：用户拉黑、遮蔽、下架、续期、Bot 同步。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete as sql_delete, select

from bot.api.deps import get_admin_user_id, get_super_user_id
from bot.api.routes_listing import RelistBody
from bot.api.routes_core import BanBody, LampOpBody, _ser_dt, _ser_user, router
from bot.config import get_settings
from bot.db import session_scope
from bot.models import HomepagePin, Lamp
from bot.services import city_scope, home_service, listing_flow, listing_ops, search_service, user_admin
from bot.services.role_lock import unlock_role

logger = logging.getLogger(__name__)


class ExtraOpsBody(BaseModel):
    popup_announcement_enabled: Optional[bool] = None
    popup_announcement_title: Optional[str] = None
    popup_announcement_text: Optional[str] = None
    bot_welcome_text: Optional[str] = None
    bot_welcome_enabled: Optional[bool] = None
    approval_notify_enabled: Optional[bool] = None
    approval_buttons: Optional[Dict[str,Any]] = None
    media_max_count: Optional[int] = None
    review_require_audit: Optional[bool] = None
    bot_welcome_buttons: Optional[List[Dict[str, Any]]] = None
    approve_attach_detail: Optional[bool] = None
    approve_attach_channel_btn: Optional[bool] = None
    community_discussion_url: Optional[str] = None
    community_backup_url: Optional[str] = None
    community_location_url: Optional[str] = None
    community_location_label: Optional[str] = None
    community_enabled: Optional[bool] = None
    listing_days: Optional[int] = None
    carousel_interval_sec: Optional[int] = None
    show_bot_link: Optional[bool] = None
    show_admin_link: Optional[bool] = None
    bot_btn_label: Optional[str] = None
    admin_btn_label: Optional[str] = None
    admin_contact: Optional[str] = None
    required_chats: Optional[List[Dict[str, Any]]] = None
    chat_cta_label: Optional[str] = None
    show_chat_cta: Optional[bool] = None
    home_feed_page_size: Optional[int] = None
    approve_promo_text: Optional[str] = None
    broadcast_channel: Optional[str] = None
    media_channel_id: Optional[str] = None
    broadcast_template: Optional[str] = None
    listing_fields: Optional[List[Dict[str, Any]]] = None
    link_label_detail: Optional[str] = None
    link_label_reviews: Optional[str] = None
    link_label_location: Optional[str] = None
    link_label_write: Optional[str] = None
    teacher_show_views: Optional[bool] = None
    teacher_show_favs: Optional[bool] = None
    teacher_show_contacts: Optional[bool] = None


class ShadowBody(BaseModel):
    user_id: int
    shadowed: bool = True
    days: Optional[int] = 7
    reason: str = ""


class PinRemoveBody(BaseModel):
    pin_id: Optional[int] = None
    lamp_id: Optional[str] = None


class UnlockRoleBody(BaseModel):
    user_id: int


class PreviewBody(BaseModel):
    lamp_id: Optional[str] = None
    template: Optional[str] = None


@router.post("/admin/settings/extra")
async def api_admin_extra_ops(
    body: ExtraOpsBody,
    admin_id: int = Depends(get_super_user_id),
) -> Dict[str, Any]:
    ops = {k: v for k, v in body.model_dump().items() if v is not None}
    from bot.services.community import safe_url
    for key in ("community_discussion_url", "community_backup_url", "community_location_url"):
        if ops.get(key) and not safe_url(ops[key]):
            raise HTTPException(status_code=400, detail="社区入口只支持 https:// 或 t.me/ 链接")
    if "admin_contact" in ops:
        from bot.services import bot_info
        raw = str(ops.get("admin_contact") or "").strip()
        norm = bot_info.normalize_contact(raw) if raw else ""
        if raw and not norm:
            raise HTTPException(status_code=400, detail="管理员联系链接格式不对，示例 https://t.me/用户名 或 @用户名")
        ops["admin_contact"] = norm
    before = await home_service.get_or_create_settings()
    settings = await home_service.update_settings(ops_config=ops or None,actor_id=admin_id)
    try:
        from bot.services import label_sync
        await label_sync.schedule_after_save(before, settings, city=None)
    except Exception:
        logger.exception("schedule label sync after global save failed")
    settings["updated_at"] = _ser_dt(settings.get("updated_at"))
    return {"ok": True, "settings": settings}


@router.get("/admin/users")
async def api_admin_users(
    q: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    city: Optional[str] = None,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    items = await user_admin.search_users(q, limit=limit)
    cities = await city_scope.effective(admin_id, city)
    if cities is not None:
        keep = await city_scope.filter_user_ids(cities, (x.get("user_id") for x in items))
        items = [x for x in items if int(x.get("user_id") or 0) in keep]
    cmap = await city_scope.user_city_map(x.get("user_id") for x in items)
    out = []
    for x in items:
        u = _ser_user(x)
        u["cities"] = cmap.get(int(x.get("user_id") or 0), [])
        out.append(u)
    return {"ok": True, "items": out}


@router.post("/admin/users/ban")
async def api_admin_ban(
    body: BanBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    if get_settings().is_admin(body.user_id):
        raise HTTPException(status_code=400, detail="不能拉黑管理员")
    await city_scope.guard_user(admin_id, body.user_id)
    user = await user_admin.set_banned(body.user_id, body.banned, body.reason)
    if body.banned:
        async with session_scope() as s:
            res = await s.execute(select(Lamp.lamp_id).where(Lamp.user_id == body.user_id))
            ids = [row[0] for row in res.all()]
        for lid in ids:
            try:
                await listing_ops.unlist_lamp(lid, reason="banned")
            except Exception:
                logger.exception("unlist on ban failed %s", lid)
    return {"ok": True, "user": _ser_user(user), "by": admin_id}


@router.post("/admin/users/shadow")
async def api_admin_shadow(
    body: ShadowBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    raise HTTPException(status_code=410, detail="兰花信用已停用")


@router.post("/admin/users/unlock-role")
async def api_admin_unlock_role(
    body: UnlockRoleBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard_user(admin_id, body.user_id)
    user = await unlock_role(body.user_id)
    return {"ok": True, "user": _ser_user(user), "by": admin_id}


@router.get("/admin/listings")
async def api_admin_listings(
    city: Optional[str] = None,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    cities = await city_scope.effective(admin_id, city)
    items = await listing_ops.list_listed(cities=cities)
    for it in items:
        it["expires_at"] = _ser_dt(it.get("expires_at"))
        it["updated_at"] = _ser_dt(it.get("updated_at"))
    from bot.services.listing_edits import latest
    states=await latest([x["lamp_id"] for x in items])
    from bot.services.relist_delivery import latest as relist_latest
    relists=await relist_latest([x["lamp_id"] for x in items])
    for it in items:
        it["edit_state"]=states.get(it["lamp_id"]);it["relist_state"]=relists.get(it["lamp_id"])
    return {"ok": True, "items": items}


@router.post("/admin/listings/{lamp_id}/unlist")
async def api_admin_unlist(
    lamp_id: str, body: LampOpBody = LampOpBody(), admin_id: int = Depends(get_admin_user_id)
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "lamp", lamp_id)
    try:
        data = await listing_ops.unlist_lamp(lamp_id, reason=body.reason or "admin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **data}


@router.post("/admin/listings/{lamp_id}/delete")
async def api_admin_delete_lamp(lamp_id: str, admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    """Permanent hard-delete. Soft 下架 stays on /unlist."""
    await city_scope.guard(admin_id, "lamp", lamp_id)
    try:
        data = await listing_ops.delete_lamp(lamp_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **data}


@router.post("/admin/listings/{lamp_id}/relist")
async def api_admin_relist(lamp_id: str, body: RelistBody = RelistBody(), admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "lamp", lamp_id)
    try:
        data = await listing_ops.relist_lamp(lamp_id,actor_id=admin_id,operation_id=body.operation_id,expected_updated_at=body.expected_updated_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "operation":data, "message":data.get("message")}


@router.post("/admin/listings/{lamp_id}/renew")
async def api_admin_renew(
    lamp_id: str, body: LampOpBody = LampOpBody(), admin_id: int = Depends(get_admin_user_id)
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "lamp", lamp_id)
    import re
    from bot.services import renewal
    operation_id = str(body.operation_id or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", operation_id):
        raise HTTPException(status_code=400, detail="请刷新后台后重试续期（缺少防重复操作编号）")
    try:
        from bot.services.listing_lock import listing_lock
        async with listing_lock(lamp_id):
            return await renewal.renew_listing(lamp_id, admin_id, body.days, operation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/homepage/pins/remove")
async def api_pin_remove(body: PinRemoveBody, admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    if body.pin_id:
        await city_scope.guard(admin_id, "pin", body.pin_id)
    if body.lamp_id:
        await city_scope.guard(admin_id, "lamp", body.lamp_id)
    removed = 0
    async with session_scope() as s:
        if body.pin_id:
            res = await s.execute(sql_delete(HomepagePin).where(HomepagePin.id == int(body.pin_id)))
            removed += int(res.rowcount or 0)
        if removed == 0 and body.lamp_id:
            res = await s.execute(sql_delete(HomepagePin).where(HomepagePin.lamp_id == str(body.lamp_id)))
            removed += int(res.rowcount or 0)
    if removed <= 0:
        raise HTTPException(status_code=404, detail="轮播不存在")
    return {"ok": True, "removed": removed}


@router.post("/admin/bot-identity/refresh")
async def api_admin_refresh_bot(_: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    from bot.services import bot_info
    ident = await bot_info.refresh_bot_identity()
    return {"ok": True, "identity": ident}


@router.post("/admin/broadcast/preview")
async def api_broadcast_preview(
    body: PreviewBody,
    _: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    from bot.services import bot_info
    from bot.services.broadcast import fill_template, footer_text
    from bot.services.community import safe_url
    from bot.services.home_feed import ops_for_city

    raw_site = await home_service.get_or_create_settings()
    site = raw_site
    tpl = (body.template or site.get("broadcast_template") or "").strip()
    lamp: Dict[str, Any] | None = None
    if body.lamp_id:
        lamp = await search_service.get_lamp(body.lamp_id)
    if not lamp:
        listed = await listing_ops.list_listed()
        if listed:
            lamp = await search_service.get_lamp(listed[0].get("lamp_id") or "") or listed[0]
    if not lamp:
        lamp = {
            "title": "示例花名",
            "city": "深圳",
            "district": "南山",
            "price_text": "面议",
            "description": "这是预览用的简介",
            "tags": ["预览"],
            "approx_label": "后海",
            "extras": {"微信": "demo_wx", "联系": "demo_wx"},
        }
    extras = dict((lamp.get("extras") if isinstance(lamp.get("extras"), dict) else {}) or {})
    from bot.services.broadcast import start_url
    ident = await bot_info.get_bot_identity()
    name = ident.get("username") or ""
    link = "" if start_url(name, str(lamp.get("lamp_id") or ""), "s") else bot_info.bot_tme_url(name)
    site = ops_for_city(raw_site, lamp.get("city"))
    text = fill_template(tpl, lamp, extras, link)
    # Match the live channel footer (same labels / hide-if-empty / U+00A0 separator),
    # but as plain text: the admin UI shows this via textContent, not as HTML tags.
    location = safe_url(site.get("community_location_url")) if site.get("community_enabled", True) else ""
    loc_label = str(site.get("link_label_location") or "").strip()[:16]
    extra = loc_label if location and loc_label else ""
    footer = footer_text(site, extra=extra)
    if footer:
        text = (text + "\n\n" + footer).strip()
    return {"ok": True, "text": text, "title": lamp.get("title"), "lamp_id": lamp.get("lamp_id")}
