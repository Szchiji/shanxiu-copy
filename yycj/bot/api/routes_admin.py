"""月影车姬 API：管理端审核与首页运营。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Query

from bot.api.deps import get_admin_user_id, get_super_user_id
from bot.models import ReviewStatus
from bot.services import home_service, session_service
from bot.services import admin_ops, city_scope
from bot.services.admin_ops import AdminActionError
from bot.api.routes_core import (
    router,
    CreditAdjustBody,
    AnnounceBody,
    CitiesBody,
    PinBody,
    PinReorderBody,
    ApprovePinBody,
    FeedPinBody,
    OpsSettingsBody,
    _ser_dt,
    _ser_user,
    _ser_lamp,
    _admin_http_error,
)

logger = logging.getLogger(__name__)


@router.get("/admin/posts/pending")
async def api_admin_pending_posts(
    limit: int = Query(default=50, ge=1, le=200),
    city: Optional[str] = None,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    cities = await city_scope.effective(admin_id, city)
    items = await admin_ops.list_pending_posts(limit=limit, cities=cities)
    for it in items:
        it["created_at"] = _ser_dt(it.get("created_at"))
        it["city"] = (it.get("lamp_data") or {}).get("city") or city_scope.UNKNOWN
    return {"ok": True, "items": items}

@router.post("/admin/posts/{post_id}/approve")
async def api_admin_approve_post(
    post_id: str,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "post", post_id)
    try:
        result = await admin_ops.approve_post(post_id, notify=True, actor_id=admin_id)
    except AdminActionError as exc:
        raise _admin_http_error(exc) from exc
    result["reviewed_by"] = admin_id
    return result

@router.post("/admin/posts/{post_id}/approve-pin")
async def api_admin_approve_pin(
    post_id: str,
    body: ApprovePinBody = ApprovePinBody(),
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "post", post_id)
    try:
        result = await admin_ops.approve_post_and_pin(
            post_id,
            expires_hours=body.expires_hours,
            sort_order=body.sort_order,
            notify=True,
            admin_id=admin_id,
        )
    except AdminActionError as exc:
        raise _admin_http_error(exc) from exc
    if result.get("pin") and result["pin"].get("expires_at"):
        result["pin"]["expires_at"] = _ser_dt(result["pin"]["expires_at"])
    result["reviewed_by"] = admin_id
    return result

@router.post("/admin/posts/{post_id}/reject")
async def api_admin_reject_post(
    post_id: str,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "post", post_id)
    try:
        result = await admin_ops.reject_post(post_id, notify=True, actor_id=admin_id)
    except AdminActionError as exc:
        raise _admin_http_error(exc) from exc
    result["reviewed_by"] = admin_id
    return result

@router.get("/admin/reports/pending")
async def api_admin_pending_reports(
    limit: int = Query(default=50, ge=1, le=200),
    city: Optional[str] = None,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    cities = await city_scope.effective(admin_id, city)
    items = await admin_ops.list_pending_reports(limit=limit, cities=cities)
    info = await city_scope.cities_of_lamps(x.get("lamp_id") for x in items)
    for it in items:
        it["created_at"] = _ser_dt(it.get("created_at"))
        it["city"] = (info.get(it.get("lamp_id")) or {}).get("city") or city_scope.UNKNOWN
        it["lamp_title"] = (info.get(it.get("lamp_id")) or {}).get("title") or ""
    return {"ok": True, "items": items}

@router.post("/admin/reports/{report_id}/accept")
async def api_admin_accept_report(
    report_id: str,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "report", report_id)
    try:
        result = await admin_ops.accept_report(report_id, notify=True)
    except AdminActionError as exc:
        raise _admin_http_error(exc) from exc
    result["reviewed_by"] = admin_id
    return result

@router.post("/admin/reports/{report_id}/reject")
async def api_admin_reject_report(
    report_id: str,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "report", report_id)
    try:
        result = await admin_ops.reject_report(report_id, notify=True)
    except AdminActionError as exc:
        raise _admin_http_error(exc) from exc
    result["reviewed_by"] = admin_id
    return result

@router.get("/admin/reviews/pending")
async def api_admin_pending_reviews(
    limit: int = Query(default=50, ge=1, le=200),
    city: Optional[str] = None,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    cities = await city_scope.effective(admin_id, city)
    items = await home_service.list_pending_reviews(limit=limit, cities=cities)
    info = await city_scope.cities_of_lamps(x.get("lamp_id") for x in items)
    for it in items:
        it["created_at"] = _ser_dt(it.get("created_at"))
        it["city"] = (info.get(it.get("lamp_id")) or {}).get("city") or city_scope.UNKNOWN
        it["lamp_title"] = (info.get(it.get("lamp_id")) or {}).get("title") or ""
    return {"ok": True, "items": items}

@router.post("/admin/reviews/{review_id}/approve")
async def api_admin_approve_review(
    review_id: str,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "review", review_id)
    try:
        rev = await home_service.set_review_status(review_id, ReviewStatus.APPROVED.value)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    rev["created_at"] = _ser_dt(rev.get("created_at"))
    rev["reviewed_at"] = _ser_dt(rev.get("reviewed_at"))
    from bot.services import review_notify
    review_notify.sync_soon([review_id], ReviewStatus.APPROVED.value, admin_id)
    return {"ok": True, "review": rev}

@router.post("/admin/reviews/{review_id}/reject")
async def api_admin_reject_review(
    review_id: str,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "review", review_id)
    try:
        rev = await home_service.set_review_status(review_id, ReviewStatus.REJECTED.value)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    rev["created_at"] = _ser_dt(rev.get("created_at"))
    rev["reviewed_at"] = _ser_dt(rev.get("reviewed_at"))
    from bot.services import review_notify
    review_notify.sync_soon([review_id], ReviewStatus.REJECTED.value, admin_id)
    return {"ok": True, "review": rev}

@router.post("/admin/reviews/{review_id}/brush")
async def api_admin_brush_review(
    review_id: str,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "review", review_id)
    try:
        rev = await home_service.set_review_status(
            review_id, ReviewStatus.BRUSH.value, note="拒绝刷评"
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    rev["created_at"] = _ser_dt(rev.get("created_at"))
    rev["reviewed_at"] = _ser_dt(rev.get("reviewed_at"))
    from bot.services import review_notify
    review_notify.sync_soon([review_id], ReviewStatus.BRUSH.value, admin_id)
    return {"ok": True, "review": rev}

@router.get("/admin/homepage")
async def api_admin_homepage(
    city: Optional[str] = None,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    cities = await city_scope.effective(admin_id, city)
    settings = await home_service.get_or_create_settings()
    pins = await home_service.list_all_pins()
    if cities is not None:
        pins = [p for p in pins if city_scope.allowed(cities, (p.get("lamp") or {}).get("city"))]
    for p in pins:
        p["expires_at"] = _ser_dt(p.get("expires_at"))
        p["created_at"] = _ser_dt(p.get("created_at"))
        if p.get("lamp"):
            p["lamp"] = _ser_lamp(p["lamp"])
    feed_pins = await home_service.list_feed_pins()
    lamps = await home_service.list_approved_lamps_brief(limit=100)
    if cities is not None:
        feed_pins = [x for x in feed_pins if city_scope.allowed(cities, x.get("city"))]
        lamps = [x for x in lamps if city_scope.allowed(cities, x.get("city"))]
    settings["updated_at"] = _ser_dt(settings.get("updated_at"))
    return {
        "ok": True,
        "settings": settings,
        "pins": pins,
        "carousel_pins": pins,
        "feed_pins": feed_pins,
        "approved_lamps": lamps,
    }

@router.post("/admin/homepage/announcement")
async def api_admin_announcement(
    body: AnnounceBody,
    _: int = Depends(get_super_user_id),
) -> Dict[str, Any]:
    settings = await home_service.update_settings(
        announcement_text=body.text,
        announcement_enabled=body.enabled,
    )
    settings["updated_at"] = _ser_dt(settings.get("updated_at"))
    return {"ok": True, "settings": settings}

@router.post("/admin/homepage/cities")
async def api_admin_cities(
    body: CitiesBody,
    _: int = Depends(get_super_user_id),
) -> Dict[str, Any]:
    settings = await home_service.update_settings(enabled_cities=body.cities)
    settings["updated_at"] = _ser_dt(settings.get("updated_at"))
    return {"ok": True, "settings": settings}

@router.post("/admin/homepage/pins")
async def api_admin_add_pin(
    body: PinBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "lamp", body.lamp_id)
    try:
        pin = await home_service.add_pin(
            body.lamp_id,
            sort_order=body.sort_order,
            expires_hours=body.expires_hours,
            created_by=admin_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    pin["expires_at"] = _ser_dt(pin.get("expires_at"))
    return {"ok": True, "pin": pin}

@router.delete("/admin/homepage/pins/{pin_id}")
async def api_admin_remove_pin(
    pin_id: int,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "pin", pin_id)
    ok = await home_service.remove_pin(pin_id)
    if not ok:
        raise HTTPException(status_code=404, detail="置顶不存在")
    return {"ok": True}

@router.post("/admin/homepage/pins/reorder")
async def api_admin_reorder_pins(
    body: PinReorderBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    for pid in body.ordered_ids:
        await city_scope.guard(admin_id, "pin", pid)
    pins = await home_service.reorder_pins(body.ordered_ids)
    for p in pins:
        p["expires_at"] = _ser_dt(p.get("expires_at"))
        p["created_at"] = _ser_dt(p.get("created_at"))
        if p.get("lamp"):
            p["lamp"] = _ser_lamp(p["lamp"])
    return {"ok": True, "pins": pins}

@router.post("/admin/seed-demo")
async def api_admin_seed_demo(_: int = Depends(get_super_user_id)) -> Dict[str, Any]:
    result = await home_service.seed_demo_if_empty()
    return {"ok": True, **result}

@router.post("/admin/credit/adjust")
async def api_admin_credit_adjust(
    body: CreditAdjustBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    raise HTTPException(status_code=410, detail="兰花信用已停用")

@router.get("/admin/shadow")
async def api_admin_shadow_list(
    limit: int = Query(default=100, ge=1, le=500),
    city: Optional[str] = None,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    raise HTTPException(status_code=410, detail="兰花信用已停用")

@router.get("/admin/sessions/{session_id}/messages")
async def api_admin_session_messages(
    session_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "session", session_id)
    sess = await session_service.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    msgs = await session_service.list_messages_for_admin(session_id, limit=limit)
    for m in msgs:
        m["created_at"] = _ser_dt(m.get("created_at"))
    out_sess = dict(sess)
    for k in ("created_at", "expire_at", "last_activity", "ended_at", "messages_purge_at"):
        if k in out_sess:
            out_sess[k] = _ser_dt(out_sess.get(k))
    return {"ok": True, "session": out_sess, "messages": msgs, "count": len(msgs)}


@router.post("/admin/homepage/feed-pins")
async def api_admin_set_feed_pin(
    body: FeedPinBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    await city_scope.guard(admin_id, "lamp", body.lamp_id)
    try:
        data = await home_service.set_feed_pin(body.lamp_id, pinned=body.pinned)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "feed_pin": data}

@router.post("/admin/homepage/ops")
async def api_admin_ops_settings(
    body: OpsSettingsBody,
    admin_id: int = Depends(get_super_user_id),
) -> Dict[str, Any]:
    ops = {}
    if body.home_feed_page_size is not None:
        ops["home_feed_page_size"] = body.home_feed_page_size
    if body.chat_cta_label is not None:
        ops["chat_cta_label"] = body.chat_cta_label
    if body.bot_welcome_text is not None:
        ops["bot_welcome_text"] = body.bot_welcome_text
    if body.media_max_count is not None:
        ops["media_max_count"] = body.media_max_count
    if body.review_require_audit is not None:
        ops["review_require_audit"] = body.review_require_audit
    settings = await home_service.update_settings(
        announcement_text=body.announcement_text,
        announcement_enabled=body.announcement_enabled,
        enabled_cities=body.cities,
        ops_config=ops or None,actor_id=admin_id,
    )
    settings["updated_at"] = _ser_dt(settings.get("updated_at"))
    return {"ok": True, "settings": settings}
