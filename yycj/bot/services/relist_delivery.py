"""One permitted hidden->active transition, then durable fresh-post/DM delivery.
No expiry mutation, historical backfill or automatic replay of uncertain sends.
"""
from datetime import datetime, timezone
import re
from sqlalchemy import select, update
from bot.db import session_scope
from bot.models import Lamp, ListingRelistOperation as Operation
from bot.services import edit_delivery, execution_log, extras_store, search_service
from bot.services.listing_lock import listing_lock


def stamp(value):
    return value.isoformat() if value else None


def version(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError,TypeError):
        raise ValueError('请刷新资料后重新上架（缺少有效版本）')


def public(state):
    keys=('operation_id','restored','action','channel_status','channel_retryable','notification_status','notification_retryable','album','superseded','review_status','media_status','expires_at')
    out={k:state.get(k) for k in keys}
    out['message']='资料已恢复上架，有效期不变；频道：'+{'pending':'待执行','sending':'处理中或送达待核对，禁止重复发送','synced':'新帖已发送','failed':'明确失败，可仅重试频道步骤','unknown':'送达不明，禁止盲目重发','not_configured':'未配置频道','not_required':'未新增发送'}.get(state.get('channel_status'),'请查看步骤')+'；老师通知：'+{'pending':'等待频道步骤成功','sending':'处理中或送达待核对','sent':'已发送','failed':'明确失败','unknown':'送达不明','skipped':'按当前开关跳过','not_required':'未触发'}.get(state.get('notification_status'),'请查看步骤')
    if state.get('superseded'):
        out.update(channel_retryable=False,notification_retryable=False,message='旧重新上架操作已失效；不会重复发送或恢复状态。')
    return out


async def invalidate(s,lamp_id):
    rows=(await s.execute(select(Operation).where(Operation.lamp_id==lamp_id))).scalars()
    for op in rows:
        st=dict(op.state);st.update(superseded=True,channel_retryable=False,notification_retryable=False);op.state=st


async def save(key,st):
    async with session_scope() as s:
        await s.execute(update(Operation).where(Operation.operation_id==key).values(state=dict(st),updated_at=datetime.utcnow()))


def valid(lamp,st):
    return bool(lamp and lamp.status=='active' and not st.get('superseded') and
        (not lamp.expires_at or lamp.expires_at>datetime.utcnow()) and
        lamp.city==st.get('city') and stamp(lamp.expires_at)==st.get('expires_at'))


async def authorize(lamp,actor,admin):
    if not lamp or (not admin and int(lamp.user_id)!=int(actor)):
        raise ValueError('找不到你有权操作的资料')
    if admin:
        from bot.services.city_scope import ensure_city
        await ensure_city(actor,lamp.city)


async def restore(lamp_id,actor_id,operation_id,expected_updated_at,*,admin=False):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}',str(operation_id or '')):
        raise ValueError('请刷新页面后重试（缺少防重复操作编号）')
    expected=version(expected_updated_at)
    async with listing_lock(lamp_id):
        async with session_scope() as s:
            lamp=(await s.execute(select(Lamp).where(Lamp.lamp_id==lamp_id).with_for_update())).scalar_one_or_none()
            await authorize(lamp,actor_id,admin)
            old=await s.get(Operation,operation_id)
            if old:
                if old.lamp_id!=lamp_id or old.actor_id!=actor_id or old.state.get('expected_version')!=stamp(expected):
                    raise ValueError('操作编号与资料/版本不匹配，请刷新')
                st=dict(old.state)
                if not valid(lamp,st):st['superseded']=True
                return public(st)
            if lamp.status=='active':
                return {'restored':False,'message':'资料已经上架；未重发频道或通知，有效期不变。','channel_status':'not_required','notification_status':'not_required'}
            if lamp.status!='hidden':raise ValueError('只有已下架的资料可以恢复；待审或已拒绝资料须走审核')
            if not admin and lamp.unlist_reason!='teacher':raise ValueError('管理员或平台下架的条目，不能自己恢复')
            if lamp.expires_at and lamp.expires_at<=datetime.utcnow():raise ValueError('已过期，请联系管理员续期')
            if lamp.updated_at!=expected:raise ValueError('资料状态已有变化，请刷新后重新上架')
            before=await extras_store.load_broadcast(lamp_id)
            await invalidate(s,lamp_id)
            lamp.status='active';lamp.unlist_reason=None;lamp.updated_at=datetime.utcnow()
            st={'operation_id':operation_id,'restored':True,'action':'relist','city':lamp.city,'actor_id':actor_id,
                'expected_version':stamp(expected),'expires_at':stamp(lamp.expires_at),'before_ref':[str(x) if x is not None else None for x in before],
                'review_status':'none','media_status':'unchanged','channel_status':'pending','channel_retryable':True,
                'notification_status':'pending','notification_retryable':True,'album':None,'superseded':False}
            s.add(Operation(operation_id=operation_id,lamp_id=lamp_id,actor_id=actor_id,state=dict(st)))
        from bot.services.listing_ops import _directory_on_live
        await _directory_on_live(lamp_id,st['city'])
        return await deliver(operation_id,st)


async def deliver(key,st):
    async with session_scope() as s:
        op=await s.get(Operation,key);lamp=(await s.execute(select(Lamp).where(Lamp.lamp_id==op.lamp_id))).scalar_one_or_none()
        if not valid(lamp,st):
            st.update(superseded=True,channel_retryable=False,notification_retryable=False)
            await save(key,st);return public(st)
        lamp_id=lamp.lamp_id
    full=await search_service.get_lamp(lamp_id)
    if not full:raise ValueError('资料不存在')
    from bot.services.media_urls import enrich_media
    full['media']=enrich_media(list(lamp.media or []),list(lamp.photos or []))
    full['_edit_operation_id']=key;full['_edit_actor_id']=st['actor_id']
    if st['channel_status']=='pending':
        st.update(channel_status='sending',channel_retryable=False);await save(key,st)
        record=await execution_log.begin('relist','channel',city=st['city'],listing_id=lamp_id,operation_id=key,actor_id=st['actor_id'],target='channel',source='business',retry_kind='relist_channel')
        # Explicit fresh-send path shared with approved media. No ordinary broadcast dedupe.
        result=await edit_delivery.channel(full,'repost')
        st.update(channel_status=result['status'],channel_retryable=result['retryable'],album=result.get('album'))
        await save(key,st)
        await execution_log.finish(record,result['status'],'not_configured' if result['status']=='not_configured' else 'delivery_unknown' if result['status']=='unknown' else 'failed' if result['status']=='failed' else None)
    if st['notification_status']=='pending' and st['channel_status']=='synced':
        st.update(notification_status='sending',notification_retryable=False);await save(key,st)
        record=await execution_log.begin('relist','notification',city=st['city'],listing_id=lamp_id,operation_id=key,actor_id=st['actor_id'],target='publisher',source='business',retry_kind='relist_notification')
        result=await edit_delivery.notify(full,st['album'])
        st.update(notification_status=result['status'],notification_retryable=result['retryable']);await save(key,st)
        await execution_log.finish(record,result['status'],'disabled' if result['status']=='skipped' else 'delivery_unknown' if result['status']=='unknown' else 'failed' if result['status']=='failed' else None)
    return public(st)


async def retry(key,step,actor_id,*,admin=False):
    if step not in ('channel','notification'):raise ValueError('不支持的交付步骤')
    async with session_scope() as s:
        op=await s.get(Operation,key)
        if not op:raise ValueError('重新上架操作不存在')
        lamp_id=op.lamp_id
    async with listing_lock(lamp_id):
        async with session_scope() as s:
            op=(await s.execute(select(Operation).where(Operation.operation_id==key).with_for_update())).scalar_one()
            lamp=(await s.execute(select(Lamp).where(Lamp.lamp_id==lamp_id))).scalar_one_or_none();await authorize(lamp,actor_id,admin);st=dict(op.state)
            if not valid(lamp,st):raise ValueError('资料已下架、过期或被后续操作替代，旧步骤不可重试')
            if not st.get(step+'_retryable') or st[step+'_status'] not in ('failed','not_configured','pending'):
                raise ValueError('成功、处理中或送达不明的步骤不可重发')
            if step=='notification' and st['channel_status']!='synced':raise ValueError('须先完成频道步骤')
            chat,mid=await extras_store.load_broadcast(lamp_id)
            if step=='channel' and [str(x) if x is not None else None for x in (chat,mid)]!=st['before_ref']:
                raise ValueError('频道引用已变化，旧步骤不可重发')
            if step=='notification':
                from bot.services.broadcast import album_link
                if not mid or album_link(chat,int(mid))!=st.get('album'):raise ValueError('频道已更新，旧通知不可重发')
            st[step+'_status']='pending';op.state=dict(st)
        return await deliver(key,st)


async def latest(lamp_ids):
    if not lamp_ids:return {}
    async with session_scope() as s:
        rows=(await s.execute(select(Operation).where(Operation.lamp_id.in_(lamp_ids)).order_by(Operation.created_at.desc(),Operation.operation_id.desc()))).scalars()
        lamps={x.lamp_id:x for x in (await s.execute(select(Lamp).where(Lamp.lamp_id.in_(lamp_ids)))).scalars()}
        out={}
        for op in rows:
            if op.lamp_id in out:continue
            st=dict(op.state)
            if not valid(lamps.get(op.lamp_id),st):st['superseded']=True
            if not st.get('superseded') and (st.get('channel_retryable') or st.get('notification_retryable') and st.get('channel_status')=='synced'):
                chat,mid=await extras_store.load_broadcast(op.lamp_id)
                from bot.services.broadcast import album_link
                if st.get('channel_status')!='synced':st['superseded']=[str(x) if x is not None else None for x in (chat,mid)]!=st.get('before_ref')
                elif not mid or album_link(chat,int(mid))!=st.get('album'):st['superseded']=True
            out[op.lamp_id]=public(st)
        return out
