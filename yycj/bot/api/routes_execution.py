"""Read-only-by-default execution center; explicit retries delegate to business checkpoints."""
from datetime import datetime,timezone
from typing import Optional,Any
from fastapi import Depends,HTTPException,Query
from pydantic import BaseModel
from sqlalchemy import select,func
from bot.api.routes_core import router
from bot.api.deps import get_admin_user_id
from bot.db import session_scope
from bot.models import ExecutionRecord,ListingEditOperation,ListingRenewal,ListingRelistOperation
from bot.models_extra import LabelSyncState
from bot.services import city_scope,home_service,execution_log
from bot.services.admin_acl import is_super


async def scope_query(admin_id,city=None):
    # Global/private welcome records are super-only, including legacy all-city operators.
    if city:await city_scope.ensure_city(admin_id,city)
    scope=await city_scope.scope_for(admin_id)
    q=select(ExecutionRecord)
    if not is_super(admin_id):q=q.where(ExecutionRecord.city.is_not(None))
    if scope is not None:q=q.where(ExecutionRecord.city.in_(scope))
    if city:q=q.where(ExecutionRecord.city==city)
    return q


def iso(value):return value.isoformat()+'Z' if value else None


def utc(value):return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value


async def public_rows(rows):
    ids={x.operation_id for x in rows if x.operation_id}
    cities={x.city for x in rows if x.city and x.retry_kind=='label_sync'}
    async with session_scope() as s:
        edits={x.operation_id:x for x in (await s.execute(select(ListingEditOperation).where(ListingEditOperation.operation_id.in_(ids)))).scalars()} if ids else {}
        renewals={x.operation_id:x for x in (await s.execute(select(ListingRenewal).where(ListingRenewal.operation_id.in_(ids)))).scalars()} if ids else {}
        relists={x.operation_id:x for x in (await s.execute(select(ListingRelistOperation).where(ListingRelistOperation.operation_id.in_(ids)))).scalars()} if ids else {}
        labels={x.city:x for x in (await s.execute(select(LabelSyncState).where(LabelSyncState.city.in_(cities)))).scalars()} if cities else {}
    out=[]
    for row in rows:
        retry=False;action=None;op=edits.get(row.operation_id)
        step='notification' if row.step=='notification' else 'channel'
        if op and row.feature!='relist' and row.status in ('failed','not_configured','pending'):
            st=op.state or {};retry=bool(st.get(step+'_retryable')) and st.get(step+'_status') in ('failed','not_configured','pending');action='edit_'+step if retry else None
        renewal=renewals.get(row.operation_id)
        if row.feature!='relist' and not op and renewal and row.step=='notification' and row.status=='failed':
            st=renewal.result or {};retry=st.get('channel_status')=='sent' and st.get('notify_status')=='failed' and bool(st.get('notification_retryable'));action='renewal_notification' if retry else None
        if row.feature=='relist' and row.operation_id in relists:
            from bot.services.relist_delivery import latest
            current=(await latest([row.listing_id])).get(row.listing_id) or {}
            st=relists[row.operation_id].state or {}
            retry=bool(current.get('operation_id')==row.operation_id and current.get(step+'_retryable') and not current.get('superseded') and st.get(step+'_status') in ('failed','not_configured','pending') and row.status in ('failed','not_configured','pending','running'))
            if step=='notification' and st.get('channel_status')!='synced':retry=False
            action='relist_'+step if retry else None
        if row.retry_kind=='label_sync' and row.status=='failed':
            label=labels.get(row.city);retry=bool(label and label.failed and label.status!='running');action='label_sync' if retry else None
        out.append({'record_id':row.record_id,'operation_id':row.operation_id,'feature':row.feature,'step':row.step,
          'source':row.source,'city':row.city,'listing_id':row.listing_id,'actor_id':row.actor_id,'config_revision':row.config_revision,
          'target_ref':row.target_ref,'status':row.status,'reason_code':row.reason_code,
          'reason':execution_log.REASONS.get(row.reason_code,''),'started_at':iso(row.started_at),'finished_at':iso(row.finished_at),
          'retryable':bool(retry),'retry_action':action})
    return out


@router.get('/admin/executions')
async def executions(city:Optional[str]=None,feature:Optional[str]=None,status:Optional[str]=None,
    listing_id:Optional[str]=None,operation_id:Optional[str]=None,since:Optional[datetime]=None,until:Optional[datetime]=None,
    limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0,le=100000),admin_id:int=Depends(get_admin_user_id)):
    q=await scope_query(admin_id,city)
    for col,value in ((ExecutionRecord.feature,feature),(ExecutionRecord.status,status),(ExecutionRecord.listing_id,listing_id),(ExecutionRecord.operation_id,operation_id)):
        if value:q=q.where(col==value)
    if since:q=q.where(ExecutionRecord.started_at>=utc(since))
    if until:q=q.where(ExecutionRecord.started_at<=utc(until))
    async with session_scope() as s:
        total=(await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
        rows=list((await s.execute(q.order_by(ExecutionRecord.started_at.desc(),ExecutionRecord.record_id).offset(offset).limit(limit))).scalars())
    return {'items':await public_rows(rows),'total':total,'offset':offset,'limit':limit,'has_more':offset+len(rows)<total}


async def find_record(key,admin):
    async with session_scope() as s:row=(await s.execute((await scope_query(admin)).where(ExecutionRecord.record_id==key))).scalar_one_or_none()
    if not row:raise HTTPException(status_code=404,detail='记录不存在或无权查看')
    return row


@router.get('/admin/executions/{record_id}')
async def execution_detail(record_id:str,admin_id:int=Depends(get_admin_user_id)):
    return (await public_rows([await find_record(record_id,admin_id)]))[0]


@router.post('/admin/executions/{record_id}/retry')
async def execution_retry(record_id:str,admin_id:int=Depends(get_admin_user_id)):
    row=await find_record(record_id,admin_id);view=(await public_rows([row]))[0]
    if not view['retryable']:raise HTTPException(status_code=409,detail='当前步骤不可直接重试；成功、关闭或不确定送达不会重发')
    try:
        action=view['retry_action']
        if action.startswith('relist_'):
            from bot.services.relist_delivery import retry
            await retry(row.operation_id,action.removeprefix('relist_'),admin_id,admin=True)
        elif action.startswith('edit_'):
            from bot.services.listing_edits import retry
            await retry(row.operation_id,action.removeprefix('edit_'),admin_id,admin=True)
        elif action=='renewal_notification':
            from bot.services.renewal import retry_notification
            await retry_notification(row.operation_id,admin_id)
        elif action=='label_sync':
            from bot.services.label_sync import retry_failed
            await retry_failed(row.city)
        else:raise ValueError('没有安全重试适配器')
    except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc
    return {'ok':True,'record':(await public_rows([row]))[0],'message':'只处理目标步骤，请刷新查看最新执行状态；未重新保存资料或续期'}


@router.get('/admin/features')
async def features(city:Optional[str]=None,admin_id:int=Depends(get_admin_user_id)):
    if not city and not is_super(admin_id):raise HTTPException(status_code=403,detail='请选择负责的城市')
    if city:await city_scope.ensure_city(admin_id,city)
    from bot.services.home_feed import ops_for_city
    from bot.services.feature_registry import metadata
    settings=ops_for_city(await home_service.get_or_create_settings(),city)
    return {'items':metadata(settings),'config_revision':settings.get('config_revision'),'updated_at':str(settings.get('updated_at') or ''),
       'note':'开关作用于后续执行，不自动修改历史消息；小程序可能有10/60秒缓存，目录下次北京时间12:00生效。'}


class ButtonPreview(BaseModel):
    context:str
    items:list[dict[str,Any]]=[]
    config:Optional[dict[str,Any]]=None
    city:Optional[str]=None
    has_album:bool=True
    attach_detail:Optional[bool]=None
    attach_channel:Optional[bool]=None


@router.post('/admin/buttons/preview')
async def buttons_preview(body:ButtonPreview,admin_id:int=Depends(get_admin_user_id)):
    if body.context not in ('welcome','approval'):raise HTTPException(status_code=400,detail='不支持的消息场景')
    if body.context=='welcome' or not body.city:
        if not is_super(admin_id):raise HTTPException(status_code=403,detail='全站按钮仅超管可设置')
    elif body.city:await city_scope.ensure_city(admin_id,body.city)
    from bot.services.home_feed import ops_for_city
    from bot.services.message_buttons import resolve,clean,approval_items,LIMITS
    site=ops_for_city(await home_service.get_or_create_settings(),body.city)
    if body.attach_detail is not None:site={**site,'approve_attach_detail':body.attach_detail}
    if body.attach_channel is not None:site={**site,'approve_attach_channel_btn':body.attach_channel}
    if body.context=='approval':site={**site,'approval_buttons':body.config or {'mode':'custom','items':body.items}};items=approval_items(site)
    else:items=body.items
    cleaned=clean(items,body.context)
    return {'items':cleaned,'rows':resolve(cleaned,body.context,base='https://example.invalid/app',detail_webapp='https://example.invalid/app/go.html?lamp=example',detail_url='https://t.me/examplebot?start=s_example',album='https://t.me/example/1' if body.has_album else ''),
        'limits':LIMITS[body.context],'notice':'示例预览，使用与实际发送相同的校验和排布；不会发送消息。无有效目标的按钮隐藏。'}
