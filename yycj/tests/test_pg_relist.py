"""Real isolated PG: atomic relist transitions/checkpoints, mocked Telegram only."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid
import pytest
from test_pg_workspace import environment,run,PG
pytestmark=pytest.mark.skipif(not PG,reason='YYCJ_TEST_PG not set')

@asynccontextmanager
async def setup(monkeypatch,*,status='hidden',reason='teacher',expired=False,notify=True,channel=True):
    async with environment(monkeypatch) as (client,headers,home):
        from bot import main
        from bot.db import session_scope
        from bot.models import Lamp,User
        from bot.services import bot_info,extras_store,listing_ops,search_service
        from bot.services import relist_delivery as relist
        monkeypatch.setattr(listing_ops,'_directory_on_live',AsyncMock())
        bot_info._cache.update(username='IsolatedRelistBot',ts=9e12)
        await home.update_settings(ops_config={'broadcast_channel':'@workspace_test' if channel else '', 'approval_notify_enabled':notify})
        lid=str(uuid.uuid4());expiry=datetime.utcnow()+timedelta(days=-1 if expired else 5)
        async with session_scope() as s:
            s.add(User(user_id=81001,role='teacher'))
            s.add(Lamp(lamp_id=lid,user_id=81001,city='深圳',title='重新上架测试',description='当前文字',status=status,unlist_reason=reason,expires_at=expiry,
                media=[{'type':'image','file_id':'fixture-a'},{'type':'image','file_id':'fixture-b'}]))
        await extras_store.remember_broadcast(lid,'@old_channel',77,card_mid=88)
        counter=iter(range(101,500))
        async def send(*a,**kw):return [SimpleNamespace(message_id=next(counter))]
        main.bot.send_media_group.side_effect=send
        main.bot.send_message.return_value=SimpleNamespace(message_id=500)
        async def snapshot():
            from sqlalchemy import select
            data=await search_service.get_lamp(lid)
            async with session_scope() as s:
                row=(await s.execute(select(Lamp).where(Lamp.lamp_id==lid))).scalar_one()
                data['expires_at']=row.expires_at
            return data
        initial=await snapshot();key=str(uuid.uuid4());body={'operation_id':key,'expected_updated_at':initial['updated_at'].isoformat()}
        yield SimpleNamespace(client=client,headers=headers,home=home,lid=lid,key=key,body=body,expiry=expiry,bot=main.bot,relist=relist,snapshot=snapshot)

async def restore(t,*,admin=False,body=None,uid=None):
    return await t.relist.restore(t.lid,uid or (9201 if admin else 81001),(body or t.body)['operation_id'],(body or t.body)['expected_updated_at'],admin=admin)


def test_relist_new_complete_album_current_refs_notification_and_unchanged_expiry(monkeypatch):
    async def check():
        async with setup(monkeypatch) as t:
            out=await restore(t)
            assert out['channel_status']=='synced' and out['notification_status']=='sent'
            assert t.bot.send_media_group.await_count==1 and t.bot.edit_message_caption.await_count==0
            assert len(t.bot.send_media_group.call_args.kwargs['media'])==2
            assert t.bot.send_message.call_args.args[0]==81001
            assert out['album'] in t.bot.send_message.call_args.args[1]
            assert t.bot.send_message.call_args.kwargs['reply_markup'] is not None
            assert (await t.snapshot())['expires_at']==t.expiry
            from bot.services.extras_store import load_broadcast,load_broadcast_card
            assert await load_broadcast(t.lid)==('@workspace_test','101')
            assert not await load_broadcast_card(t.lid)
            assert t.bot.delete_message.await_count==0
            assert (await restore(t))['channel_status']=='synced'
            other={**t.body,'operation_id':str(uuid.uuid4())}
            assert not (await restore(t,body=other))['restored']
            assert t.bot.send_media_group.await_count==1 and t.bot.send_message.await_count==1
    run(check())

@pytest.mark.parametrize('admin',[False,True])
@pytest.mark.parametrize('status',['pending','rejected','gray'])
def test_relist_cannot_bypass_review_states(monkeypatch,admin,status):
    async def check():
        async with setup(monkeypatch,status=status) as t:
            with pytest.raises(ValueError,match='只有已下架'):await restore(t,admin=admin)
            assert (await t.snapshot())['status']==status and not t.bot.send_media_group.called
    run(check())

@pytest.mark.parametrize('case',['owner','teacher_reason','expired','city','version'])
def test_relist_owner_city_expiry_reason_version_guards(monkeypatch,case):
    async def check():
        from fastapi import HTTPException
        async with setup(monkeypatch,reason='admin' if case=='teacher_reason' else 'teacher',expired=case=='expired') as t:
            body={**t.body,'expected_updated_at':'2020-01-01T00:00:00'} if case=='version' else None
            with pytest.raises((ValueError,HTTPException)):
                await restore(t,body=body,uid=9202 if case=='city' else 81002 if case=='owner' else None,admin=case=='city')
            assert (await t.snapshot())['status']=='hidden' and not t.bot.send_media_group.called
    run(check())


def test_repeat_unlist_relist_is_new_cycle_old_requests_cannot_restore(monkeypatch):
    async def check():
        async with setup(monkeypatch) as t:
            await restore(t)
            from bot.services import listing_ops
            await listing_ops.teacher_unlist(t.lid,81001)
            assert (await restore(t))['superseded'] and (await t.snapshot())['status']=='hidden'
            with pytest.raises(ValueError,match='状态已有变化'):
                await restore(t,body={**t.body,'operation_id':str(uuid.uuid4())})
            snap=await t.snapshot();body={'operation_id':str(uuid.uuid4()),'expected_updated_at':snap['updated_at'].isoformat()}
            assert (await restore(t,body=body))['channel_status']=='synced'
            assert t.bot.send_media_group.await_count==2 and (await t.snapshot())['expires_at']==t.expiry
    run(check())


def test_concurrent_relist_and_duplicate_retry_only_one_external_send(monkeypatch):
    async def check():
        async with setup(monkeypatch) as t:
            entered=asyncio.Event();release=asyncio.Event()
            async def send(*a,**kw):entered.set();await release.wait();return [SimpleNamespace(message_id=111)]
            t.bot.send_media_group.side_effect=send
            first=asyncio.create_task(restore(t));await asyncio.wait_for(entered.wait(),5)
            with pytest.raises(ValueError,match='正在处理'):await restore(t,body={**t.body,'operation_id':str(uuid.uuid4())})
            with pytest.raises(ValueError,match='正在处理'):await t.relist.retry(t.key,'channel',81001)
            release.set();await first
            assert t.bot.send_media_group.await_count==1
    run(check())

@pytest.mark.parametrize('failure',['known','unknown','not_configured'])
def test_channel_failure_no_success_dm_known_only_retry(monkeypatch,failure):
    async def check():
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import SendMessage
        async with setup(monkeypatch,channel=failure!='not_configured') as t:
            if failure!='not_configured':t.bot.send_media_group.side_effect=TelegramBadRequest(method=SendMessage(chat_id=1,text='fixture'),message='fixture reject') if failure=='known' else TimeoutError('ambiguous fixture send')
            out=await restore(t);assert out['channel_status']=={'known':'failed','unknown':'unknown','not_configured':'not_configured'}[failure]
            assert t.bot.send_message.await_count==0 and (await t.snapshot())['status']=='active'
            assert (await restore(t))['channel_status']==out['channel_status']
            if failure=='unknown':
                with pytest.raises(ValueError,match='不可重发'):await t.relist.retry(t.key,'channel',81001)
            else:
                await t.home.update_settings(ops_config={'broadcast_channel':'@workspace_test'})
                t.bot.send_media_group.side_effect=None;t.bot.send_media_group.return_value=[SimpleNamespace(message_id=119)]
                out=await t.relist.retry(t.key,'channel',81001)
                assert out['channel_status']=='synced' and out['notification_status']=='sent'
                with pytest.raises(ValueError):await t.relist.retry(t.key,'channel',81001)
            assert (await t.snapshot())['expires_at']==t.expiry
    run(check())

@pytest.mark.parametrize('disabled_on_retry',[False,True])
def test_notification_only_retry_preserves_channel_and_checks_current_switch(monkeypatch,disabled_on_retry):
    async def check():
        from aiogram.exceptions import TelegramForbiddenError
        from aiogram.methods import SendMessage
        async with setup(monkeypatch) as t:
            t.bot.send_message.side_effect=TelegramForbiddenError(method=SendMessage(chat_id=81001,text='fixture'),message='fixture blocked')
            out=await restore(t);assert out['channel_status']=='synced' and out['notification_status']=='failed'
            if disabled_on_retry:await t.home.update_settings(ops_config={'approval_notify_enabled':False})
            t.bot.send_message.side_effect=None
            out=await t.relist.retry(t.key,'notification',81001)
            assert out['notification_status']==('skipped' if disabled_on_retry else 'sent')
            assert t.bot.send_media_group.await_count==1
            with pytest.raises(ValueError):await t.relist.retry(t.key,'notification',81001)
    run(check())


def test_initial_disabled_notification_is_skipped_after_channel(monkeypatch):
    async def check():
        async with setup(monkeypatch,notify=False) as t:
            out=await restore(t);assert out['channel_status']=='synced' and out['notification_status']=='skipped'
            assert t.bot.send_message.await_count==0
    run(check())

@pytest.mark.parametrize('phase',['before_send','after_ack','after_dm'])
def test_checkpoint_interruption_cannot_blindly_replay(monkeypatch,phase):
    async def check():
        async with setup(monkeypatch) as t:
            original=t.relist.save
            async def broken(key,st):
                if (phase=='before_send' and st['channel_status']=='sending') or (phase=='after_ack' and st['channel_status']=='synced') or (phase=='after_dm' and st['notification_status']=='sent'):raise RuntimeError('isolated checkpoint failure')
                await original(key,st)
            monkeypatch.setattr(t.relist,'save',broken)
            with pytest.raises(RuntimeError):await restore(t)
            monkeypatch.setattr(t.relist,'save',original)
            count=t.bot.send_media_group.await_count;dm=t.bot.send_message.await_count
            out=await restore(t)
            assert t.bot.send_media_group.await_count==count and t.bot.send_message.await_count==dm
            if phase=='before_send':
                assert out['channel_status']=='pending';await t.relist.retry(t.key,'channel',81001);assert t.bot.send_media_group.await_count==1
            else:
                step='channel' if phase=='after_ack' else 'notification'
                assert out[step+'_status']=='sending'
                with pytest.raises(ValueError):await t.relist.retry(t.key,step,81001)
    run(check())


def test_relist_api_auth_version_and_execution_retry_city_boundaries(monkeypatch):
    async def check():
        async with setup(monkeypatch) as t:
            path='/api/admin/listings/'+t.lid+'/relist'
            assert (await t.client.post(path,json=t.body)).status_code==401
            assert (await t.client.post(path,json=t.body,headers=t.headers(9202))).status_code==403
            assert (await t.client.post(path,json={},headers=t.headers(9201))).status_code==400
            from aiogram.exceptions import TelegramBadRequest
            from aiogram.methods import SendMessage
            t.bot.send_media_group.side_effect=TelegramBadRequest(method=SendMessage(chat_id=1,text='fixture'),message='fixture')
            result=await t.client.post(path,json=t.body,headers=t.headers(9201));assert result.status_code==200 and result.json()['operation']['channel_status']=='failed'
            rows=(await t.client.get('/api/admin/executions?feature=relist',headers=t.headers(9201))).json()['items']
            row=next(x for x in rows if x['step']=='channel');assert row['retryable']
            retry='/api/admin/executions/'+row['record_id']+'/retry'
            assert (await t.client.post(retry,headers=t.headers(9202))).status_code==404
            t.bot.send_media_group.side_effect=None;t.bot.send_media_group.return_value=[SimpleNamespace(message_id=120)]
            assert (await t.client.post(retry,headers=t.headers(9201))).status_code==200
            assert (await t.client.post(retry,headers=t.headers(9201))).status_code==409
            assert not any(x in str(rows).lower() for x in ['testtoken','initdata','password'])
    run(check())


def test_activation_and_checkpoint_creation_are_one_transaction(monkeypatch):
    async def check():
        from sqlalchemy import event,select
        from bot.models import ListingRelistOperation
        from bot.db import session_scope
        async with setup(monkeypatch) as t:
            def fail(*args):raise RuntimeError('isolated insert failure')
            event.listen(ListingRelistOperation,'before_insert',fail)
            try:
                with pytest.raises(RuntimeError,match='insert failure'):await restore(t)
            finally:event.remove(ListingRelistOperation,'before_insert',fail)
            assert (await t.snapshot())['status']=='hidden' and not t.bot.send_media_group.called
            async with session_scope() as s:assert (await s.execute(select(ListingRelistOperation).where(ListingRelistOperation.operation_id==t.key))).scalar_one_or_none() is None
            assert (await restore(t))['channel_status']=='synced'
    run(check())


@pytest.mark.parametrize('change',['unlist','city','expiry','reference'])
def test_stale_notification_checkpoint_cannot_retry_after_later_changes(monkeypatch,change):
    async def check():
        from aiogram.exceptions import TelegramForbiddenError
        from aiogram.methods import SendMessage
        from sqlalchemy import select
        from bot.db import session_scope
        from bot.models import Lamp
        from bot.services import extras_store,listing_ops
        async with setup(monkeypatch) as t:
            t.bot.send_message.side_effect=TelegramForbiddenError(method=SendMessage(chat_id=81001,text='fixture'),message='fixture')
            assert (await restore(t))['notification_retryable']
            if change=='unlist':await listing_ops.teacher_unlist(t.lid,81001)
            elif change=='reference':await extras_store.remember_broadcast(t.lid,'@workspace_test',999)
            else:
                async with session_scope() as s:
                    lamp=(await s.execute(select(Lamp).where(Lamp.lamp_id==t.lid))).scalar_one()
                    if change=='city':lamp.city='广州'
                    else:lamp.expires_at+=timedelta(days=1)
            with pytest.raises(ValueError):await t.relist.retry(t.key,'notification',81001)
            out=(await t.relist.latest([t.lid]))[t.lid]
            assert out['superseded'] and not out['notification_retryable']
            assert t.bot.send_media_group.await_count==1 and t.bot.send_message.await_count==1
    run(check())


def test_unknown_notification_and_reference_write_failure_never_blind_retry(monkeypatch):
    async def check():
        from bot.services import extras_store
        async with setup(monkeypatch) as t:
            t.bot.send_message.side_effect=TimeoutError('unknown isolated notification')
            out=await restore(t)
            assert out['notification_status']=='unknown' and not out['notification_retryable']
            with pytest.raises(ValueError):await t.relist.retry(t.key,'notification',81001)
            assert t.bot.send_media_group.await_count==1 and t.bot.send_message.await_count==1
    run(check())


def test_reference_write_failure_never_blind_retry(monkeypatch):
    async def check():
        from bot.services import extras_store
        async with setup(monkeypatch) as t:
            monkeypatch.setattr(extras_store,'remember_broadcast',AsyncMock(side_effect=RuntimeError('isolated ref failure')))
            out=await restore(t)
            assert out['channel_status']=='unknown' and not out['channel_retryable']
            with pytest.raises(ValueError):await t.relist.retry(t.key,'channel',81001)
            assert t.bot.send_media_group.await_count==1 and t.bot.send_message.await_count==0
    run(check())
