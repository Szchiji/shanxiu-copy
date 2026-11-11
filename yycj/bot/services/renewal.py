"""Future renewals: explicit fresh channel post + publisher DM; no historical backfill.

Telegram has no idempotency key for sends. Checkpoint BEFORE each external send and
never automatically retry an ambiguous send. A repeated operation returns its saved
outcome, including partial failure, rather than duplicating the post or DM.
"""
from __future__ import annotations

import logging
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from bot.db import session_scope
from bot.models import ListingRenewal
from bot.services import listing_ops, search_service, broadcast, listing_notify

logger = logging.getLogger(__name__)


async def claim(operation_id, lamp_id, admin_id, days):
    async with session_scope() as s:
        result = await s.execute(insert(ListingRenewal).values(
            operation_id=operation_id, lamp_id=lamp_id, admin_id=admin_id, days=days,
            result={"ok": False, "renewed": False, "operation_id": operation_id,
                    "channel_status": "not_started", "notify_status": "not_started",
                    "message": "续期请求正在处理或结果尚未确认；不会自动重复推送，请稍后刷新。"}
        ).on_conflict_do_nothing(index_elements=[ListingRenewal.operation_id]).returning(ListingRenewal.operation_id))
        return result.scalar_one_or_none() is not None


async def load(operation_id, lamp_id, admin_id, days):
    async with session_scope() as s:
        row = (await s.execute(select(ListingRenewal).where(ListingRenewal.operation_id == operation_id))).scalar_one()
        if (row.lamp_id, row.admin_id, row.days) != (lamp_id, admin_id, days):
            raise ValueError("操作编号与本次续期不匹配，请刷新后台")
        return dict(row.result)


async def save(operation_id, result):
    async with session_scope() as s:
        await s.execute(update(ListingRenewal).where(ListingRenewal.operation_id == operation_id).values(result=dict(result)))


async def renew_listing(lamp_id: str, admin_id: int, days: int | None, operation_id: str):
    if not await claim(operation_id, lamp_id, admin_id, days):
        return await load(operation_id, lamp_id, admin_id, days)
    out = {"ok": False, "renewed": False, "operation_id": operation_id,
           "album": None, "channel_status": "not_started", "notify_status": "not_started"}
    try:
        data = await listing_ops.set_listing_expiry(lamp_id, days=days)
    except ValueError as exc:
        out["message"] = str(exc)
        await save(operation_id, out)
        raise
    out.update(data)
    expiry = out.get("expires_at")
    out["expires_at"] = expiry.isoformat() if hasattr(expiry, "isoformat") else expiry
    out.update(renewed=True, channel_status="sending",
               message="续期已生效；频道推送处理中或结果不明，请勿重复续期。")
    # If this durable checkpoint fails, do not start any external send.
    await save(operation_id, out)
    record=None
    try:
        full = await search_service.get_lamp(lamp_id) or {}
        if not full or not full.get("user_id"):
            raise ValueError("资料或发布者不存在")
        full["_renew_operation_id"]=operation_id
        from bot.services import execution_log
        record=await execution_log.begin('renewal','repost',city=full.get('city'),listing_id=lamp_id,operation_id=operation_id,actor_id=admin_id,target='channel')
        album = await broadcast.broadcast_listing(full, dict(full.get("extras") or {}), force_new=True)
        await execution_log.finish(record,'sent' if album else 'unknown',None if album else 'delivery_unknown')
    except Exception as exc:
        from bot.services import execution_log
        code=execution_log.reason(exc)
        await execution_log.finish(record,"unknown" if code=="delivery_unknown" else "failed",code)
        logger.exception("renew channel repost failed for %s", lamp_id)
        album = None
    if not album:
        out.update(channel_status="failed_or_unknown", notify_status="not_attempted",
                   message="续期已生效，但频道重发失败或结果不明；未发送上架成功通知。请检查频道，勿重复续期。")
        await save(operation_id, out)
        return out
    out.update(album=album, channel_status="sent", notify_status="sending",
               message="续期和频道重发已完成；老师通知处理中或结果不明，请勿重复续期。")
    # New post refs have been persisted by broadcast before it returns the album.
    await save(operation_id, out)
    try:
        notified = await listing_notify.notify_publisher_approved(
            int(full["user_id"]), full.get("title") or "资料", album,
            city=full.get("city"), lamp_id=lamp_id,raise_errors=True,operation_id=operation_id,actor_id=admin_id)
    except Exception as exc:
        from bot.services.execution_log import reason
        classification=reason(exc)
        logger.exception("renew publisher notification failed for %s", lamp_id)
        notified = False
        out['notify_status']='failed' if classification!='delivery_unknown' else 'unknown'
        out['notification_retryable']=classification!='delivery_unknown'
    out.update(ok=bool(notified) or notified is None, notify_status="skipped" if notified is None else "sent" if notified else out.get("notify_status") if out.get("notify_status") in ("failed","unknown") else "failed_or_unknown",
               message="续期和频道重发成功；老师成功通知已按开关跳过。" if notified is None else "续期成功，已重新推送到频道最新并通知老师。" if notified else
                       "续期和频道重发成功，但老师通知失败或结果不明；不会自动重发，请检查老师是否屏蔽机器人。")
    await save(operation_id, out)
    return out


async def retry_notification(operation_id,admin_id):
    """Only a known rejected DM, never expiry mutation or channel repost."""
    from bot.services import city_scope,execution_log
    from bot.services.listing_lock import listing_lock
    async with session_scope() as s:
        row=(await s.execute(select(ListingRenewal).where(ListingRenewal.operation_id==operation_id))).scalar_one_or_none()
        if not row:raise ValueError('续期记录不存在')
        lamp_id=row.lamp_id
    async with listing_lock(lamp_id):
        full=await search_service.get_lamp(lamp_id)
        if not full:raise ValueError('资料不存在')
        await city_scope.ensure_city(admin_id,full.get('city'))
        async with session_scope() as s:
            row=(await s.execute(select(ListingRenewal).where(ListingRenewal.operation_id==operation_id).with_for_update())).scalar_one()
            result=dict(row.result)
            if not result.get('notification_retryable') or result.get('notify_status')!='failed' or result.get('channel_status')!='sent':raise ValueError('该步骤不可直接重试；成功或未知送达不会重发')
            from bot.services.extras_store import load_broadcast
            chat,mid=await load_broadcast(lamp_id)
            if not mid or broadcast.album_link(chat,int(mid))!=result.get('album'):raise ValueError('频道资料已有更新，旧成功通知不能重发')
            result.update(notify_status='sending',notification_retryable=False);row.result=dict(result)
        try:
            sent=await listing_notify.notify_publisher_approved(int(full['user_id']),full.get('title') or '资料',result['album'],city=full.get('city'),lamp_id=lamp_id,raise_errors=True,operation_id=operation_id,actor_id=admin_id,listing_active=full.get('status')=='active')
            result.update(ok=bool(sent) or sent is None,notify_status='skipped' if sent is None else 'sent' if sent else 'unknown',notification_retryable=False)
        except Exception as exc:
            code=execution_log.reason(exc);result.update(ok=False,notify_status='unknown' if code=='delivery_unknown' else 'failed',notification_retryable=code!='delivery_unknown')
        await save(operation_id,result)
        return result
