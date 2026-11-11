"""管理侧列表、审核动作与运维查询（API / Bot 共用同一业务逻辑）。"""

from __future__ import annotations

from html import escape as _h

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from bot.db import session_scope
from bot.models import Post, PostStatus, Report, ReportStatus
from bot.services import search_service

logger = logging.getLogger(__name__)


async def notify_user_best_effort(user_id: int, text: str) -> bool:
    """
    尽力通过 Bot 私聊通知用户。
    HTTP 请求上下文可能无 Bot 注入；失败不抛出，避免阻断审核 API。
    """
    try:
        from bot.main import bot

        await bot.send_message(user_id, text)
        return True
    except Exception:
        logger.exception("best-effort notify failed for user_id=%s", user_id)
        return False


async def list_pending_posts(limit: int = 50, cities: Optional[set] = None) -> List[Dict[str, Any]]:
    stmt = select(Post).where(Post.status == PostStatus.PENDING.value)
    if cities is not None:
        from bot.services.city_scope import city_in
        stmt = stmt.where(city_in(Post.lamp_data["city"].astext, cities))
    async with session_scope() as s:
        res = await s.execute(stmt.order_by(Post.created_at.asc()).limit(max(1, min(limit, 200))))
        rows = res.scalars().all()
        return [
            {
                "post_id": p.post_id,
                "user_id": p.user_id,
                "lamp_data": dict(p.lamp_data or {}),
                "status": p.status,
                "created_at": p.created_at,
            }
            for p in rows
        ]


async def list_pending_reports(limit: int = 50, cities: Optional[set] = None) -> List[Dict[str, Any]]:
    stmt = select(Report).where(Report.status == ReportStatus.PENDING.value)
    if cities is not None:
        from bot.models import Lamp
        from bot.services.city_scope import city_in
        stmt = stmt.where(Report.lamp_id.in_(select(Lamp.lamp_id).where(city_in(Lamp.city, cities))))
    async with session_scope() as s:
        res = await s.execute(stmt.order_by(Report.created_at.asc()).limit(max(1, min(limit, 200))))
        rows = res.scalars().all()
        return [
            {
                "report_id": r.report_id,
                "lamp_id": r.lamp_id,
                "reporter_id": r.reporter_id,
                "reason": r.reason,
                "description": r.description or "",
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in rows
        ]


class AdminActionError(Exception):
    """业务层审核错误（已处理 / 不存在等）。"""

    def __init__(self, message: str, *, code: str = "conflict"):
        super().__init__(message)
        self.code = code


async def approve_post(post_id: str, *, notify: bool = True) -> Dict[str, Any]:
    """通过投稿 → 上架灯笼 + 投稿人 +15。与 Bot admin_post_ok 一致。"""
    async with session_scope() as s:
        res = await s.execute(select(Post).where(Post.post_id == post_id))
        post = res.scalar_one_or_none()
        if not post:
            raise AdminActionError("投稿不存在", code="not_found")
        if post.status != PostStatus.PENDING.value:
            raise AdminActionError("已处理或不在待审状态", code="conflict")
        data = dict(post.lamp_data or {})
        post.status = PostStatus.APPROVED.value
        post.reviewed_at = datetime.utcnow()
        user_id = post.user_id

    media = list(data.get("media") or [])
    photos = list(data.get("photos") or [])
    lamp = await search_service.create_lamp_from_post(
        user_id=user_id,
        city=data.get("city") or "未知",
        title=data.get("title") or "未命名",
        tags=list(data.get("tags") or [])[:5],
        price=data.get("price"),
        price_text=data.get("price_text"),
        description=data.get("description") or "",
        photos=photos,
        authenticity_score=80,
        district=data.get("district"),
        approx_lat=data.get("approx_lat"),
        approx_lng=data.get("approx_lng"),
        approx_label=data.get("approx_label"),
        media=media,
        publisher_role=data.get("publisher_role"),
    )
    await search_service.approve_lamp(lamp["lamp_id"])
    if notify:
        await notify_user_best_effort(
            user_id,
            f"你的灯笼 <b>{_h(str(lamp['title']))}</b> 已通过审核并上架。",
        )
    return {
        "ok": True,
        "post_id": post_id,
        "status": PostStatus.APPROVED.value,
        "lamp_id": lamp["lamp_id"],
        "user_id": user_id,
    }


async def reject_post(post_id: str, *, notify: bool = True) -> Dict[str, Any]:
    """拒绝投稿。与 Bot admin_post_no 一致。"""
    async with session_scope() as s:
        res = await s.execute(select(Post).where(Post.post_id == post_id))
        post = res.scalar_one_or_none()
        if not post:
            raise AdminActionError("投稿不存在", code="not_found")
        if post.status != PostStatus.PENDING.value:
            raise AdminActionError("已处理或不在待审状态", code="conflict")
        post.status = PostStatus.REJECTED.value
        post.reviewed_at = datetime.utcnow()
        user_id = post.user_id

    if notify:
        await notify_user_best_effort(user_id, "你的灯笼投稿未通过审核。")
    return {
        "ok": True,
        "post_id": post_id,
        "status": PostStatus.REJECTED.value,
        "user_id": user_id,
    }


async def accept_report(report_id: str, *, notify: bool = True) -> Dict[str, Any]:
    """采纳报告 → 下架灯笼。与 Bot admin_report_ok 一致。"""
    async with session_scope() as s:
        res = await s.execute(select(Report).where(Report.report_id == report_id))
        rep = res.scalar_one_or_none()
        if not rep:
            raise AdminActionError("报告不存在", code="not_found")
        if rep.status != ReportStatus.PENDING.value:
            raise AdminActionError("已处理或不在待审状态", code="conflict")
        rep.status = ReportStatus.ACCEPTED.value
        rep.reviewed_at = datetime.utcnow()
        lamp_id = rep.lamp_id
        reporter_id = rep.reporter_id

    target_id: Optional[int] = None
    lamp = await search_service.get_lamp(lamp_id)
    if lamp:
        target_id = int(lamp["user_id"])
        await search_service.reject_lamp(lamp_id)
    if notify:
        await notify_user_best_effort(
            reporter_id,
            "你的举报已采纳，相关资料已下架。",
        )
        if target_id is not None:
            await notify_user_best_effort(
                target_id,
                "有举报成立，相关资料已下架。",
            )
    return {
        "ok": True,
        "report_id": report_id,
        "status": ReportStatus.ACCEPTED.value,
        "lamp_id": lamp_id,
        "reporter_id": reporter_id,
        "target_id": target_id,
    }


async def reject_report(report_id: str, *, notify: bool = True) -> Dict[str, Any]:
    """驳回报告。与 Bot admin_report_no 一致。"""
    async with session_scope() as s:
        res = await s.execute(select(Report).where(Report.report_id == report_id))
        rep = res.scalar_one_or_none()
        if not rep:
            raise AdminActionError("报告不存在", code="not_found")
        if rep.status != ReportStatus.PENDING.value:
            raise AdminActionError("已处理或不在待审状态", code="conflict")
        rep.status = ReportStatus.REJECTED.value
        rep.reviewed_at = datetime.utcnow()
        reporter_id = rep.reporter_id

    if notify:
        await notify_user_best_effort(
            reporter_id,
            "你的举报未通过审核。",
        )
    return {
        "ok": True,
        "report_id": report_id,
        "status": ReportStatus.REJECTED.value,
        "reporter_id": reporter_id,
    }



async def approve_post_and_pin(
    post_id: str,
    *,
    expires_hours: int | None = 72,
    sort_order: int = 0,
    notify: bool = True,
    admin_id: int | None = None,
) -> Dict[str, Any]:
    """通过投稿并置顶到首页精选。"""
    from bot.services import home_service

    result = await approve_post(post_id, notify=notify, actor_id=admin_id)
    pin = await home_service.add_pin(
        result["lamp_id"],
        sort_order=sort_order,
        expires_hours=expires_hours,
        created_by=admin_id,
    )
    result["pin"] = pin
    return result
