"""Isolated real PG permissions/checkpoints; FakeBot only, no production endpoints."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from test_pg_edit_upgrade import setup,key
pytestmark=pytest.mark.skipif(not os.environ.get('YYCJ_TEST_PG'),reason='YYCJ_TEST_PG not set')


def test_logs_scope_filters_secret_safety_and_read_only(monkeypatch):
 async def run():
  async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
   import bot.main as main
   import httpx
   from bot.services import home_service,execution_log,city_scope,admin_acl
   from bot.api.telegram_webapp import issue_access_token
   from bot.config import get_settings
   await home_service.update_settings(ops_config={'extra_admin_ids':[600,700,800],'operator_cities':{'600':['深圳'],'700':['广州']}})
   await admin_acl.save([600,700,800])
   async def scope(uid):return {'深圳'} if uid==600 else {'广州'} if uid==700 else None
   monkeypatch.setattr(city_scope,'scope_for',scope)
   global_id=await execution_log.event('welcome','message','skipped','disabled',target='private')
   sz=await execution_log.event('listing_edit','text','failed','failed',city='深圳',listing_id=lid,operation_id='123456789:TESTTOKENxxxxxxxxxxxxxxxxxxxxxxxxxxx',target='Bearer.private')
   gz=await execution_log.event('renewal','repost','unknown','delivery_unknown',city='广州')
   main._ready=True
   headers=lambda uid:{'Authorization':'Bearer '+issue_access_token(uid,get_settings().bot_token)}
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://local.test') as c:
    assert (await c.get('/api/admin/executions')).status_code==401
    r=await c.get('/api/admin/executions',headers=headers(600));assert r.status_code==200,r.text
    data=r.json();assert data['total']==1 and data['items'][0]['record_id']==sz
    assert 'TESTTOKEN' not in r.text and 'Bearer.private' not in r.text
    assert (await c.get('/api/admin/executions/'+gz,headers=headers(600))).status_code==404
    assert (await c.post('/api/admin/executions/'+gz+'/retry',headers=headers(600),json={})).status_code==404
    assert (await c.get('/api/admin/executions/'+global_id,headers=headers(800))).status_code==404
    assert (await c.get('/api/admin/executions',headers=headers(800))).json()['total']==2
    assert (await c.get('/api/admin/executions?status=unknown&limit=1',headers=headers(1))).json()['items'][0]['record_id']==gz
    assert (await c.get('/api/admin/executions?limit=1&offset=1',headers=headers(1))).json()['total']==3
    assert (await c.post('/api/admin/executions/'+gz+'/retry',headers=headers(1),json={})).status_code==409
    assert (await c.get('/api/admin/features',headers=headers(600))).status_code==403
    assert (await c.get('/api/admin/features?city=广州',headers=headers(600))).status_code==403
    assert (await c.get('/api/admin/features?city=深圳',headers=headers(600))).status_code==200
    assert (await c.post('/api/admin/buttons/preview',headers=headers(600),json={'context':'welcome'})).status_code==403
    preview=await c.post('/api/admin/buttons/preview',headers=headers(600),json={'context':'approval','city':'深圳','config':{'mode':'custom','items':[{'type':'url','label':'新频道帖','target':'{album}'}]},'has_album':False})
    assert preview.status_code==200 and preview.json()['rows']==[]
   channel.assert_not_awaited();notify.assert_not_awaited()
 asyncio.run(run())


def test_known_failed_log_retry_is_step_only_and_concurrent_safe(monkeypatch):
 async def run():
  async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
   import bot.main as main
   import httpx
   from bot.db import session_scope
   from bot.models import ExecutionRecord
   from bot.services.edit_delivery import outcome
   from bot.config import get_settings
   from bot.api.telegram_webapp import issue_access_token
   channel.return_value=outcome('failed',True)
   op=await svc.submit(lid,owner,body,key());revision=(await read()).edit_revision
   async with session_scope() as s:
    record=(await s.execute(select(ExecutionRecord).where(ExecutionRecord.operation_id==op['operation_id']))).scalars().first()
   assert record and record.status=='failed'
   entered=asyncio.Event();release=asyncio.Event()
   async def send(*_):entered.set();await release.wait();return outcome('synced',album='https://t.me/test/900')
   channel.side_effect=send
   main._ready=True;headers={'Authorization':'Bearer '+issue_access_token(1,get_settings().bot_token)}
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://local.test') as c:
    url='/api/admin/executions/'+record.record_id+'/retry'
    one=asyncio.create_task(c.post(url,headers=headers,json={}))
    await asyncio.wait_for(entered.wait(),2)
    two=await c.post(url,headers=headers,json={});assert two.status_code==409
    release.set();assert (await one).status_code==200
    assert (await c.post(url,headers=headers,json={})).status_code==409
   lamp=await read();assert lamp.edit_revision==revision and lamp.expires_at==until and channel.await_count==2
 asyncio.run(run())


def test_media_approval_disabled_success_dm_logs_skipped_and_no_bypass(monkeypatch):
 async def run():
  from bot.services.edit_delivery import notify as real_delivery
  async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
   import bot.main as main
   from bot.services import home_service,edit_delivery
   from bot.services.home_feed import merge_ops
   from bot.db import session_scope
   from bot.models import ExecutionRecord
   monkeypatch.setattr(edit_delivery,'notify',real_delivery)
   monkeypatch.setattr(home_service,'get_or_create_settings',AsyncMock(return_value=merge_ops({'approval_notify_enabled':False})))
   bot=SimpleNamespace(send_message=AsyncMock());monkeypatch.setattr(main,'bot',bot)
   op=await svc.submit(lid,owner,{**body,'media':[{'type':'image','url':'new'}]},key())
   result=await svc.review(op['post_id'],True)
   assert result['operation']['notification_status']=='skipped'
   assert result['operation']['notification_retryable'] is False
   with pytest.raises(svc.EditConflict):await svc.retry(op['operation_id'],'notification',owner)
   bot.send_message.assert_not_awaited();assert channel.await_count==2 and (await read()).expires_at==until
   async with session_scope() as s:
    records=list((await s.execute(select(ExecutionRecord).where(ExecutionRecord.operation_id==op['operation_id']))).scalars())
   assert any(r.status=='skipped' and r.reason_code=='disabled' for r in records)
 asyncio.run(run())


def test_config_revision_actor_and_city_buttons_round_trip(monkeypatch):
 async def run():
  async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
   from bot.services import home_service
   from bot.services.home_feed import ops_for_city
   first=await home_service.get_or_create_settings()
   custom={'mode':'custom','items':[{'type':'url','label':'频道','target':'{album}','enabled':False,'new_row':True}]}
   saved=await home_service.update_settings(ops_config={'approval_buttons':custom},actor_id=1)
   assert saved['config_revision']==first['config_revision']+1 and saved['updated_by']==1
   saved=await home_service.update_city_overrides('深圳',{'approval_buttons':{'mode':'custom','items':[]},'approval_notify_enabled':False},actor_id=600)
   assert saved['config_revision']==first['config_revision']+2 and saved['updated_by']==600
   assert ops_for_city(saved,'深圳')['approval_buttons']['items']==[]
   restored=await home_service.update_city_overrides('深圳',{'approval_buttons':None,'approval_notify_enabled':None},actor_id=600)
   assert ops_for_city(restored,'深圳')['approval_buttons']['items'][0]['enabled'] is False
   assert ops_for_city(restored,'深圳')['approval_notify_enabled'] is True
   blank=await home_service.update_city_overrides('深圳',{'approval_buttons':''},actor_id=600)
   assert ops_for_city(blank,'深圳')['approval_buttons']['items'][0]['enabled'] is False
 asyncio.run(run())


@pytest.mark.parametrize("disabled",[False,True])
def test_renewal_known_dm_failure_retry_does_not_renew_or_repost(monkeypatch,disabled):
 async def run():
  async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
   import bot.main as main
   from aiogram.exceptions import TelegramForbiddenError
   from aiogram.methods import SendMessage
   from bot.services import renewal,broadcast,bot_info,home_service,extras_store
   from bot.services.home_feed import merge_ops
   from bot.db import session_scope
   from bot.models import ListingRenewal
   monkeypatch.setattr(home_service,'get_or_create_settings',AsyncMock(return_value=merge_ops({})))
   monkeypatch.setattr(bot_info,'get_bot_identity',AsyncMock(return_value={'username':'testbot'}))
   send=AsyncMock(side_effect=TelegramForbiddenError(method=SendMessage(chat_id=1,text='x'),message='Forbidden'))
   monkeypatch.setattr(main,'bot',SimpleNamespace(send_message=send))
   post=AsyncMock(return_value='https://t.me/test/900');monkeypatch.setattr(broadcast,'broadcast_listing',post)
   await extras_store.remember_broadcast(lid,'@test',900)
   token=key();result=await renewal.renew_listing(lid,1,30,token)
   assert result['notify_status']=='failed' and result['notification_retryable']
   after_expiry=(await read()).expires_at
   send.side_effect=None;send.return_value=SimpleNamespace(message_id=999)
   if disabled:home_service.get_or_create_settings.return_value=merge_ops({'approval_notify_enabled':False})
   result=await renewal.retry_notification(token,1)
   assert result['notify_status']==('skipped' if disabled else 'sent') and result['ok']
   with pytest.raises(ValueError):await renewal.retry_notification(token,1)
   assert (await read()).expires_at==after_expiry and post.await_count==1 and send.await_count==(1 if disabled else 2)
 asyncio.run(run())
