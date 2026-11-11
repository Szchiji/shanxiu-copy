"""审核接线：新编辑工作流原子审批；普通新上架沿用既有路径。"""
from bot.db import session_scope
from bot.models import Post, PostStatus
from bot.services import admin_ops, listing_flow, search_service
from bot.services.listing_notify import notify_publisher_approved
from bot.services.extras_store import save_extras
from fastapi import HTTPException
from sqlalchemy import select

_orig = admin_ops.approve_post


def _extras(data: dict) -> dict:
    from bot.services.extras_clean import clean_user_extras
    extra = data.get("extras") if isinstance(data.get("extras"), dict) else {}
    out = clean_user_extras(extra)
    for key in ("联系", "频道", "微信"):
        if data.get(key) and key not in out:
            out[key] = data.get(key)
    return out


async def approve_post(post_id: str, *, notify: bool = True, actor_id=None):
    from bot.services import listing_edits
    try:edit_result=await listing_edits.review(post_id,True,notify=notify,actor_id=actor_id)
    except ValueError as exc:raise admin_ops.AdminActionError(str(exc),code="conflict") from exc
    except HTTPException as exc:raise admin_ops.AdminActionError(str(exc.detail),code="forbidden") from exc
    if edit_result is not None:return edit_result
    async with session_scope() as s:
        res = await s.execute(select(Post).where(Post.post_id == post_id))
        post = res.scalar_one_or_none()
        data = dict((post.lamp_data or {}) if post else {})
        user_id = post.user_id if post else 0
        edit_id = data.get("edit_lamp_id")
        status = post.status if post else None
    extras = _extras(data)
    result = await _orig(post_id, notify=False)
    if data.get("hours") and result.get("lamp_id"):
        from bot.services.extras_store import save_user_fields
        await save_user_fields(result["lamp_id"], {"extras": extras, "hours": data.get("hours")})
    if notify:
        lamp = await search_service.get_lamp(result.get("lamp_id") or "")
        title = (lamp or {}).get("title") or "资料"
        album = None
        if lamp:
            await save_extras((lamp or {}).get("lamp_id") or result.get("lamp_id"), extras)
            album = await listing_flow.broadcast_listing(lamp, extras)
        await notify_publisher_approved(result["user_id"], title, album, city=(lamp or {}).get("city"), lamp_id=result.get("lamp_id"))
    return result


admin_ops.approve_post = approve_post  # type: ignore[assignment]


_orig_reject = admin_ops.reject_post


async def reject_post(post_id: str, *, notify: bool = True, actor_id=None):
    from bot.services import listing_edits
    try:result=await listing_edits.review(post_id,False,notify=notify,actor_id=actor_id)
    except ValueError as exc:raise admin_ops.AdminActionError(str(exc),code="conflict") from exc
    except HTTPException as exc:raise admin_ops.AdminActionError(str(exc.detail),code="forbidden") from exc
    if result is not None:return result
    return await _orig_reject(post_id,notify=notify)


admin_ops.reject_post = reject_post
