"""Edit workflow: atomic review + operational checkpoints; proven history is recorded on apply."""
from __future__ import annotations
import hashlib
import json
import re
import uuid
from datetime import datetime
from sqlalchemy import select, text, update, or_
from bot.db import session_scope
from bot.models import Lamp, Post, PostStatus, ListingEditOperation as Operation
from bot.services import search_service, edit_delivery
from bot.services.listing_lock import listing_lock


class EditConflict(ValueError):
    pass


def fingerprint(media):
    return [(str(x.get("type") or "image"),str(x.get("file_id") or x.get("url") or "").removeprefix("file_id:")) for x in (media or [])]


def live(lamp):
    return lamp.status == "active" and (not lamp.expires_at or lamp.expires_at > datetime.utcnow())


def public(op):
    state = op.state if hasattr(op,"state") else op
    keys = ("operation_id","text_saved","media_status","review_status","channel_status","channel_retryable","notification_status","notification_retryable","album","post_id","action","superseded_pending")
    return {k:state.get(k) for k in keys}


def request_hash(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


async def _extras(s,lamp_id,payload):
    from bot.services.extras_clean import clean_user_extras
    from bot.services.open_shift import normalize_hours
    old = (await s.execute(text("SELECT extras FROM lamps WHERE lamp_id=:id"),{"id":lamp_id})).scalar() or {}
    result = {k:v for k,v in old.items() if str(k).startswith("_")}
    result.update(clean_user_extras(payload.get("extras") or {}))
    if payload.get("hours") is not None:
        hours=normalize_hours(payload["hours"])
        if hours:result["_hours"]=hours
        else:result.pop("_hours",None)
    await s.execute(text("UPDATE lamps SET extras=CAST(:v AS jsonb) WHERE lamp_id=:id"),{"id":lamp_id,"v":json.dumps(result,ensure_ascii=False)})


async def apply_text(s,lamp,p):
    # Preserve existing scalar edit semantics, but NEVER mutate listing status/expiry.
    lamp.city=str(p.get("city") or lamp.city)[:32]
    lamp.title=str(p.get("title") or lamp.title)[:128]
    lamp.tags=list(p.get("tags") or lamp.tags or [])[:5]
    lamp.price_text=str(p.get("price_text") or lamp.price_text or "")[:32] or None
    lamp.price=search_service.clean_price(p.get("price",lamp.price),lamp.price_text)
    lamp.description=str(p.get("description") or "")[:2000]
    lamp.district=p.get("district") or None
    lamp.approx_label=p.get("approx_label") or None
    lamp.edit_revision=(lamp.edit_revision or 0)+1
    lamp.updated_at=datetime.utcnow()
    await _extras(s,lamp.lamp_id,p)


def apply_media(lamp,media):
    lamp.media=list(media)
    lamp.photos=[x.get("url") or x.get("file_id") for x in media if (x.get("type") or "image")=="image"]
    lamp.media_revision=(lamp.media_revision or 0)+1
    lamp.edit_revision=(lamp.edit_revision or 0)+1
    lamp.updated_at=datetime.utcnow()


async def pending(s,lamp_id):
    return list((await s.execute(select(Post).where(Post.status==PostStatus.PENDING.value,Post.lamp_data["edit_lamp_id"].astext==lamp_id).with_for_update())).scalars())


async def _invalidate(s,posts):
    for post in posts:
        post.status=PostStatus.REJECTED.value;post.reviewed_at=datetime.utcnow()
        key=(post.lamp_data or {}).get("edit_operation_id")
        if key:
            op=await s.get(Operation,key)
            if op:
                st=dict(op.state);st.update(review_status="stale",media_status="stale" if st.get("media_status")=="pending" else "unchanged",channel_status="not_required",channel_retryable=False)
                op.state=st;op.updated_at=datetime.utcnow()


async def submit(lamp_id,actor_id,payload,operation_id,*,admin=False,history_source=None,source_version_id=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}",str(operation_id or "")):
        raise EditConflict("请刷新页面后重试（缺少防重复操作编号）")
    digest=request_hash(payload)
    async with listing_lock(lamp_id):
        created_post=None
        async with session_scope() as s:
            lamp=(await s.execute(select(Lamp).where(Lamp.lamp_id==lamp_id).with_for_update())).scalar_one_or_none()
            if not lamp or (not admin and int(lamp.user_id)!=int(actor_id)):
                raise EditConflict("找不到你有权修改的资料")
            if admin:
                from bot.services import city_scope
                await city_scope.ensure_city(actor_id,lamp.city)
                await city_scope.ensure_city(actor_id,payload.get("city") or lamp.city)
            existing=await s.get(Operation,operation_id)
            if existing:
                if (existing.lamp_id,existing.actor_id,existing.request_hash)!=(lamp_id,actor_id,digest):raise EditConflict("操作编号与内容不符，请重新提交")
                return public(existing)
            proposed=list(payload.get("media") or lamp.media or [])
            changed=bool(payload.get("media")) and fingerprint(proposed)!=fingerprint(lamp.media)
            queue=await pending(s,lamp_id)
            if not admin and queue and (changed or not live(lamp)):
                raise EditConflict("已有待审核修改，请先等待处理；仍可单独修改文字")
            st={"operation_id":operation_id,"text_saved":False,"media_status":"unchanged","review_status":"none",
                "channel_status":"not_required","channel_retryable":False,"notification_status":"not_required","notification_retryable":False,
                "album":None,"post_id":None,"action":"none","expected_media_revision":lamp.media_revision or 0}
            op=Operation(operation_id=operation_id,lamp_id=lamp_id,actor_id=actor_id,request_hash=digest,state=dict(st))
            s.add(op)
            active=live(lamp)
            old_city=lamp.city
            from bot.services import listing_history
            before=await listing_history.capture(s,lamp)
            if admin or active:
                await apply_text(s,lamp,payload);st["text_saved"]=True
                if active:st.update(action="text",channel_status="pending",channel_retryable=True)
            if admin and changed:
                await _invalidate(s,queue)
                apply_media(lamp,proposed)
                st.update(media_status="approved_admin",superseded_pending=bool(queue),expected_media_revision=lamp.media_revision)
                if active:st.update(action="repost",channel_status="pending",channel_retryable=True,notification_status="pending",notification_retryable=True)
            elif not admin and (changed or not active):
                kind="media" if active else "full"
                post_id=str(uuid.uuid4())
                data=dict(payload)
                data.update(edit_lamp_id=lamp_id,edit_workflow_v2=kind,edit_operation_id=operation_id,
                            base_media_revision=lamp.media_revision or 0,base_edit_revision=lamp.edit_revision or 0,
                            base_status=lamp.status,base_live=active,media=proposed)
                s.add(Post(post_id=post_id,user_id=lamp.user_id,lamp_data=data,status=PostStatus.PENDING.value))
                st.update(post_id=post_id,review_status="pending",media_status="pending" if changed else "unchanged")
                op.post_id=post_id;created_post=(post_id,lamp.user_id,data)
            st["directory_cities"]=[old_city,lamp.city] if active and old_city!=lamp.city else []
            if st.get("text_saved") or st.get("media_status")=="approved_admin":
                await listing_history.record_applied(
                    s,lamp,before=before,
                    source=history_source or ("admin_edit" if admin else "teacher_edit"),
                    actor_id=actor_id,actor_kind="admin" if admin else "teacher",
                    operation_id=operation_id,source_version_id=source_version_id)
            op.state=dict(st)
        if created_post:
            from bot.services.listing_notify import notify_admins_new_post
            try:await notify_admins_new_post(*created_post)
            except Exception:pass
        await run_steps(operation_id)
        return await state(operation_id)


async def state(operation_id):
    async with session_scope() as s:
        op=await s.get(Operation,operation_id)
        if not op:raise EditConflict("修改记录不存在")
        return public(op)


async def save_state(operation_id,st):
    async with session_scope() as s:
        await s.execute(update(Operation).where(Operation.operation_id==operation_id).values(state=dict(st),updated_at=datetime.utcnow()))


async def run_steps(operation_id):
    """Caller holds listing_lock; checkpoints commit BEFORE external calls."""
    async with session_scope() as s:
        op=await s.get(Operation,operation_id);st=dict(op.state);lamp_id=op.lamp_id;actor_id=op.actor_id
    full=await search_service.get_lamp(lamp_id)
    if not full:return
    full["_edit_operation_id"]=operation_id;full["_edit_actor_id"]=actor_id
    async with session_scope() as s:
        current=(await s.execute(select(Lamp).where(Lamp.lamp_id==lamp_id))).scalar_one()
        full["_edit_listing_active"]=live(current)
    if st.get("directory_cities"):
        from bot.services.channel_directory import on_cities_changed
        try:await on_cities_changed(st["directory_cities"])
        except Exception:pass
        st["directory_cities"]=[];await save_state(operation_id,st)
    if st["channel_status"]=="pending":
        # Refresh current content under the shared listing lock, not approval snapshot text.
        async with session_scope() as s:
            lamp=(await s.execute(select(Lamp).where(Lamp.lamp_id==lamp_id))).scalar_one()
            active=live(lamp)
            stale=st["action"]=="repost" and lamp.media_revision!=st.get("expected_media_revision")
        if not active or stale:
            st.update(channel_status="stale" if stale else "not_required",channel_retryable=False,notification_status="not_required",notification_retryable=False)
        else:
            st.update(channel_status="sending",channel_retryable=False);await save_state(operation_id,st)
            from bot.services import execution_log
            record=await execution_log.begin('listing_edit',st['action'],city=full.get('city'),listing_id=lamp_id,operation_id=operation_id,actor_id=actor_id,target='channel',retry_kind='edit_channel')
            result=await edit_delivery.channel(full,st["action"])
            await execution_log.finish(record,result['status'],result['status'] if result['status'] in execution_log.REASONS else 'delivery_unknown' if result['status']=='unknown' else None)
            st.update(channel_status=result["status"],channel_retryable=result["retryable"],album=result.get("album"))
        await save_state(operation_id,st)
    if st["notification_status"]=="pending" and (st["channel_status"] in ("synced","not_required") or st.get("notify_rejected")):
        st.update(notification_status="sending",notification_retryable=False);await save_state(operation_id,st)
        from bot.services import execution_log
        record=await execution_log.begin('listing_edit','notification',city=full.get('city'),listing_id=lamp_id,operation_id=operation_id,actor_id=actor_id,target='private',retry_kind='edit_notification')
        result=await edit_delivery.notify(full,st.get("album"),rejected=st.get("notify_rejected",False))
        await execution_log.finish(record,result['status'],'disabled' if result['status']=='skipped' else 'failed' if result['status']=='failed' else 'delivery_unknown' if result['status']=='unknown' else None)
        st.update(notification_status=result["status"],notification_retryable=result["retryable"]);await save_state(operation_id,st)


async def review(post_id,approve,*,notify=True,actor_id=None):
    async with session_scope() as s:
        post=(await s.execute(select(Post).where(Post.post_id==post_id))).scalar_one_or_none()
        data=dict(post.lamp_data or {}) if post else {}
    if not data.get("edit_lamp_id"):return None
    if not data.get("edit_workflow_v2"):
        if approve:raise EditConflict("旧版改稿需拒绝后重新提交，不能用旧快照覆盖现有资料")
        return None
    lamp_id=data["edit_lamp_id"];key=data["edit_operation_id"]
    async with listing_lock(lamp_id):
        async with session_scope() as s:
            lamp=(await s.execute(select(Lamp).where(Lamp.lamp_id==lamp_id).with_for_update())).scalar_one_or_none()
            post=(await s.execute(select(Post).where(Post.post_id==post_id).with_for_update())).scalar_one()
            op=await s.get(Operation,key)
            if not lamp or not op:raise EditConflict("资料或修改记录已不存在")
            if actor_id is not None:
                from bot.services import city_scope
                await city_scope.ensure_city(actor_id,lamp.city)
                await city_scope.ensure_city(actor_id,data.get('city') or lamp.city)
            st=dict(op.state)
            if post.status==PostStatus.PENDING.value:
                stale=(lamp.status!=data["base_status"] or (live(lamp)!=data.get("base_live",data["edit_workflow_v2"]=="media")) or
                       (lamp.media_revision or 0)!=data["base_media_revision"] or
                       (data["edit_workflow_v2"]=="full" and (lamp.edit_revision or 0)!=data["base_edit_revision"]))
                if approve and stale:
                    post.status=PostStatus.REJECTED.value;st.update(review_status="stale",media_status="stale" if st.get("media_status")=="pending" else "unchanged",channel_status="not_required",channel_retryable=False)
                elif approve:
                    from bot.services import listing_history
                    before=await listing_history.capture(s,lamp)
                    if data["edit_workflow_v2"]=="full":await apply_text(s,lamp,data)
                    if fingerprint(lamp.media)!=fingerprint(data.get("media")):
                        apply_media(lamp,data["media"])
                    await listing_history.record_applied(
                        s,lamp,before=before,source="review_approved",
                        actor_id=actor_id,actor_kind="admin" if actor_id is not None else "system",
                        operation_id=key,source_version_id=data.get("_restore_version_id"))
                    post.status=PostStatus.APPROVED.value
                    st.update(text_saved=True,review_status="approved",media_status="approved" if st.get("media_status")=="pending" else "unchanged",expected_media_revision=lamp.media_revision or 0,
                              action="repost" if live(lamp) else "none",channel_status="pending" if live(lamp) else "not_required",
                              channel_retryable=live(lamp),notification_status="pending" if notify else "not_required",notification_retryable=notify)
                else:
                    post.status=PostStatus.REJECTED.value
                    # Text saved earlier stays saved; old media remain untouched.
                    st.update(review_status="rejected",media_status="rejected" if st.get("media_status")=="pending" else "unchanged",notification_status="pending" if notify else "not_required",notification_retryable=notify,notify_rejected=True)
                post.reviewed_at=datetime.utcnow();op.state=dict(st);op.updated_at=datetime.utcnow()
        await run_steps(key)
        result=await state(key)
        if result["review_status"]=="stale":raise EditConflict("资料状态或版本已变，该申请已失效，请重新提交")
        return {"ok":result["review_status"]!="stale","post_id":post_id,"lamp_id":lamp_id,
                "status":"approved" if result["review_status"]=="approved" else "rejected","edited":True,"operation":result}


async def retry(operation_id,step,actor_id,*,admin=False):
    if step not in ("channel","notification"):raise EditConflict("无效重试步骤")
    async with session_scope() as s:
        op=await s.get(Operation,operation_id)
        if not op:raise EditConflict("修改记录不存在")
        lamp_id=op.lamp_id
    async with listing_lock(lamp_id):
        async with session_scope() as s:
            lamp=(await s.execute(select(Lamp).where(Lamp.lamp_id==lamp_id).with_for_update())).scalar_one_or_none()
            if not lamp or (not admin and int(lamp.user_id)!=int(actor_id)):raise EditConflict("无权处理这条资料")
            if admin:
                from bot.services import city_scope
                await city_scope.ensure_city(actor_id,lamp.city)
            op=(await s.execute(select(Operation).where(Operation.operation_id==operation_id).with_for_update())).scalar_one()
            st=dict(op.state)
            if not st.get(step+"_retryable") or st.get(step+"_status") not in ("failed","not_configured","pending"):
                raise EditConflict("该步骤不可直接重试；处理中、成功或不确定送达不会再次发送")
            if step=="notification" and not st.get("notify_rejected") and st["channel_status"] not in ("synced","not_required"):
                raise EditConflict("请先完成频道同步，不能提前发送成功通知")
            if st["action"]=="repost" and not st.get('notify_rejected') and lamp.media_revision!=st.get("expected_media_revision"):
                st[step+'_status']='stale';st[step+'_retryable']=False;op.state=dict(st)
                return public(st)
            st[step+"_status"]="pending";st[step+"_retryable"]=False;op.state=st
        await run_steps(operation_id)
        return await state(operation_id)


async def latest(lamp_ids):
    if not lamp_ids:return {}
    async with session_scope() as s:
        ops=(await s.execute(select(Operation).where(Operation.lamp_id.in_(lamp_ids)).order_by(Operation.lamp_id,Operation.updated_at.desc()).distinct(Operation.lamp_id))).scalars()
        result={op.lamp_id:public(op) for op in ops}
        # Operational backlog only, not audit history: keep older failed steps / pending media reachable after a later text edit.
        outstanding=(await s.execute(select(Operation).where(Operation.lamp_id.in_(lamp_ids),or_(
            Operation.state['review_status'].astext=='pending',
            Operation.state['channel_status'].astext.in_(['failed','not_configured','unknown','sending','pending']),
            Operation.state['notification_status'].astext.in_(['failed','unknown','sending','pending'])
        )).order_by(Operation.updated_at.desc()))).scalars()
        for op in outstanding:
            if op.lamp_id in result and result[op.lamp_id]['operation_id']!=op.operation_id:
                result[op.lamp_id].setdefault('outstanding',[]).append(public(op))
        return result
