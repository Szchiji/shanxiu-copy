"""Real isolated PG transactions/locks; all external delivery mocked. No live edits/sends."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime,timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select,delete,text
from sqlalchemy.exc import IntegrityError

PG=os.environ.get('YYCJ_TEST_PG')
pytestmark=pytest.mark.skipif(not PG,reason='YYCJ_TEST_PG not set')


@asynccontextmanager
async def setup(monkeypatch,status='active',expired=False):
    # Explicit test-only URI; never fall back to production credentials.
    assert PG and ('127.0.0.1:55441/' in PG) and 'yycj_edit_test' in PG
    os.environ['DATABASE_URL']=PG
    from bot import db
    import bot.main
    from bot.models import Lamp,User,Post,ListingEditOperation as Op
    from bot.services import listing_edits as svc,listing_notify,city_scope,edit_delivery
    await db.connect_db()
    lid='EU'+uuid.uuid4().hex
    owner=81000000+int(uuid.uuid4().hex[:6],16)
    until=datetime.utcnow()+timedelta(days=-1 if expired else 10)
    channel=AsyncMock(return_value=edit_delivery.outcome('synced',album='https://t.me/test/900'))
    notify=AsyncMock(return_value=edit_delivery.outcome('sent'))
    monkeypatch.setattr(edit_delivery,'channel',channel);monkeypatch.setattr(edit_delivery,'notify',notify)
    monkeypatch.setattr(listing_notify,'notify_admins_new_post',AsyncMock())
    monkeypatch.setattr(city_scope,'scope_for',AsyncMock(return_value=None))
    async with db.session_scope() as s:
        s.add(User(user_id=owner,role='teacher'))
        s.add(Lamp(lamp_id=lid,user_id=owner,city='深圳',title='原文字',status=status,expires_at=until,
                   media=[{'type':'image','url':'old-photo'}],photos=['old-photo']))
    async def read():
        async with db.session_scope() as s:return (await s.execute(select(Lamp).where(Lamp.lamp_id==lid))).scalar_one()
    body={'city':'深圳','title':'新文字','tags':[],'description':'新介绍','price_text':'100','extras':{},'hours':None,'media':[]}
    try:yield svc,lid,owner,until,body,read,channel,notify
    finally:
        async with db.session_scope() as s:
            await s.execute(delete(Post).where(Post.lamp_data['edit_lamp_id'].astext==lid))
            await s.execute(delete(Op).where(Op.lamp_id==lid))
            await s.execute(delete(Lamp).where(Lamp.lamp_id==lid))
            await s.execute(delete(User).where(User.user_id==owner))
        await db.close_db()


def key():return 'op-'+uuid.uuid4().hex


def test_active_text_immediate_no_media_review_no_expiry_change(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            token=key();r=await svc.submit(lid,owner,body,token)
            assert r['text_saved'] and r['review_status']=='none' and r['action']=='text'
            lamp=await read();assert lamp.title=='新文字' and lamp.media[0]['url']=='old-photo'
            assert lamp.expires_at==until and lamp.status=='active'
            assert await svc.submit(lid,owner,body,token)==r
            assert channel.await_count==1 and notify.await_count==0
    asyncio.run(run())


def test_mixed_edit_review_preserves_old_media_and_newer_text_double_approve(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            r=await svc.submit(lid,owner,{**body,'media':[{'type':'video','url':'new-video'}]},key())
            lamp=await read();assert lamp.title=='新文字' and lamp.media[0]['url']=='old-photo' and lamp.status=='active'
            await svc.submit(lid,owner,{**body,'title':'后来的文字'},key())
            await svc.review(r['post_id'],True)
            await svc.review(r['post_id'],True)
            lamp=await read();assert lamp.title=='后来的文字' and lamp.media[0]['url']=='new-video'
            assert lamp.expires_at==until and lamp.status=='active'
            assert [x.args[1] for x in channel.await_args_list]==['text','text','repost']
            assert channel.await_args_list[-1].args[0]['title']=='后来的文字'
            assert notify.await_count==1
    asyncio.run(run())


def test_reject_keeps_old_media_saved_text_and_rejection_notification(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            r=await svc.submit(lid,owner,{**body,'media':[{'type':'image','url':'new'}]},key())
            await svc.review(r['post_id'],False);await svc.review(r['post_id'],False)
            lamp=await read();assert lamp.title=='新文字' and lamp.media[0]['url']=='old-photo' and lamp.expires_at==until
            assert notify.await_count==1 and notify.call_args.kwargs['rejected'] is True
            assert channel.await_count==1
    asyncio.run(run())


@pytest.mark.parametrize('status,expired',[('hidden',False),('hidden',True),('pending',False),('gray',False),('rejected',False),('active',True)])
def test_nonactive_full_review_never_resurrects_or_extends(monkeypatch,status,expired):
    async def run():
        async with setup(monkeypatch,status,expired) as (svc,lid,owner,until,body,read,channel,notify):
            r=await svc.submit(lid,owner,{**body,'media':[{'type':'image','url':'new'}]},key())
            assert not r['text_saved'];assert (await read()).title=='原文字'
            await svc.review(r['post_id'],True)
            lamp=await read();assert lamp.title=='新文字' and lamp.status==status and lamp.expires_at==until
            channel.assert_not_awaited();assert notify.await_count==1
            assert notify.call_args.args[0]['_edit_listing_active'] is False
    asyncio.run(run())


def test_admin_media_is_privileged_scope_checked_and_invalidates_old_request(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            from bot.services import city_scope
            from fastapi import HTTPException
            r=await svc.submit(lid,owner,{**body,'media':[{'type':'image','url':'teacher-new'}]},key())
            monkeypatch.setattr(city_scope,'scope_for',AsyncMock(return_value={'广州'}))
            with pytest.raises(HTTPException):await svc.submit(lid,1,body,key(),admin=True)
            monkeypatch.setattr(city_scope,'scope_for',AsyncMock(return_value={'深圳'}))
            admin=await svc.submit(lid,1,{**body,'media':[{'type':'image','url':'admin-new'}]},key(),admin=True)
            assert admin['media_status']=='approved_admin' and admin['superseded_pending']
            with pytest.raises(svc.EditConflict):await svc.review(r['post_id'],True)
            lamp=await read();assert lamp.media[0]['url']=='admin-new' and lamp.expires_at==until
            assert channel.await_count==2 and notify.await_count==1
    asyncio.run(run())


def test_one_pending_and_concurrent_double_approval(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            proposal={**body,'media':[{'type':'image','url':'new'}]}
            submissions=await asyncio.gather(svc.submit(lid,owner,proposal,key()),svc.submit(lid,owner,proposal,key()),return_exceptions=True)
            successful=[r for r in submissions if isinstance(r,dict)]
            assert len(successful)==1
            r=successful[0]
            approvals=await asyncio.gather(svc.review(r['post_id'],True),svc.review(r['post_id'],True),return_exceptions=True)
            assert any(isinstance(a,dict) for a in approvals)
            assert [x.args[1] for x in channel.await_args_list].count('repost')==1 and notify.await_count==1
            assert (await read()).media_revision==1
    asyncio.run(run())


def test_approval_after_status_change_is_stale_and_transaction_failure_rolls_back(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            from bot.db import session_scope
            from bot.models import Lamp,Post
            r=await svc.submit(lid,owner,{**body,'media':[{'type':'image','url':'new'}]},key())
            original=svc.apply_media
            def fail(lamp,media):original(lamp,media);raise RuntimeError('simulated crash inside review transaction')
            monkeypatch.setattr(svc,'apply_media',fail)
            with pytest.raises(RuntimeError):await svc.review(r['post_id'],True)
            lamp=await read();assert lamp.media[0]['url']=='old-photo'
            async with session_scope() as s:assert (await s.execute(select(Post).where(Post.post_id==r['post_id']))).scalar_one().status=='pending'
            monkeypatch.setattr(svc,'apply_media',original)
            async with session_scope() as s:
                lamp=(await s.execute(select(Lamp).where(Lamp.lamp_id==lid))).scalar_one();lamp.status='hidden'
            with pytest.raises(svc.EditConflict):await svc.review(r['post_id'],True)
            assert (await read()).status=='hidden' and (await read()).media[0]['url']=='old-photo'
            assert notify.await_count==0
    asyncio.run(run())


def test_retry_only_failed_notification_not_data_post_or_expiry(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            from bot.services.edit_delivery import outcome
            r=await svc.submit(lid,owner,{**body,'media':[{'type':'image','url':'new'}]},key())
            notify.return_value=outcome('failed',True)
            done=await svc.review(r['post_id'],True);op=done['operation'];revision=(await read()).edit_revision
            assert op['notification_retryable']
            notify.return_value=outcome('sent')
            await svc.retry(op['operation_id'],'notification',owner)
            with pytest.raises(svc.EditConflict):await svc.retry(op['operation_id'],'notification',owner)
            lamp=await read();assert lamp.edit_revision==revision and lamp.expires_at==until
            assert channel.await_count==2 and notify.await_count==2
    asyncio.run(run())


def test_channel_not_configured_retry_only_channel_unknown_never_resends(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            from bot.services.edit_delivery import outcome
            channel.return_value=outcome('not_configured',True)
            r=await svc.submit(lid,owner,body,key());revision=(await read()).edit_revision
            assert r['channel_status']=='not_configured'
            channel.return_value=outcome('synced',album='https://t.me/test/9')
            await svc.retry(r['operation_id'],'channel',owner)
            assert (await read()).edit_revision==revision and (await read()).expires_at==until
            channel.return_value=outcome('unknown')
            r=await svc.submit(lid,1,{**body,'media':[{'type':'image','url':'new'}]},key(),admin=True)
            with pytest.raises(svc.EditConflict):await svc.retry(r['operation_id'],'channel',owner)
            notify.assert_not_awaited()
    asyncio.run(run())


def test_interruption_after_post_ack_never_duplicates_on_replay(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            original=svc.save_state
            async def fail(key,st):
                if st.get('channel_status')=='synced':raise RuntimeError('crash after external ack')
                await original(key,st)
            monkeypatch.setattr(svc,'save_state',fail)
            token=key();payload={**body,'media':[{'type':'image','url':'new'}]}
            with pytest.raises(RuntimeError):await svc.submit(lid,1,payload,token,admin=True)
            monkeypatch.setattr(svc,'save_state',original)
            result=await svc.submit(lid,1,payload,token,admin=True)
            assert result['channel_status']=='sending' and not result['channel_retryable']
            with pytest.raises(svc.EditConflict):await svc.retry(token,'channel',owner)
            assert channel.await_count==1 and notify.await_count==0 and (await read()).expires_at==until
    asyncio.run(run())


def test_pending_backlog_survives_new_text_and_admin_nonactive_is_save_only(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            first=await svc.submit(lid,owner,{**body,'media':[{'type':'image','url':'new'}]},key())
            last=await svc.submit(lid,owner,{**body,'title':'newest'},key())
            items=await svc.latest([lid]);assert items[lid]['operation_id']==last['operation_id']
            assert items[lid]['outstanding'][0]['post_id']==first['post_id']
        async with setup(monkeypatch,'hidden') as (svc,lid,owner,until,body,read,channel,notify):
            result=await svc.submit(lid,1,{**body,'media':[{'type':'image','url':'new'}]},key(),admin=True)
            lamp=await read();assert result['text_saved'] and lamp.media[0]['url']=='new' and lamp.status=='hidden' and lamp.expires_at==until
            channel.assert_not_awaited();notify.assert_not_awaited()
    asyncio.run(run())


def test_local_api_auth_scopes_retry_and_no_upload_is_keep_current(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            import httpx
            import bot.main as m
            from bot.config import get_settings
            from bot.api.telegram_webapp import issue_access_token
            from bot.services import city_scope
            from fastapi import HTTPException
            m._ready=True
            headers=lambda uid:{'Authorization':'Bearer '+issue_access_token(uid,get_settings().bot_token)}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app),base_url='http://local.test') as c:
                assert (await c.get(f'/api/me/listings/{lid}/edit-state')).status_code==401
                assert (await c.get(f'/api/me/listings/{lid}/edit-state',headers=headers(owner+1))).status_code==404
                body2={**body,'operation_id':key()}
                response=await c.post(f'/api/me/listings/{lid}/edit',headers=headers(owner),json=body2)
                assert response.status_code==200,response.text
                assert (await c.post(f'/api/me/listings/{lid}/edit',headers=headers(owner+1),json=body2)).status_code==404
                channel.assert_awaited_once()
                # A later reload/GET is read-only; operation ID replay does not re-send.
                assert (await c.post(f'/api/me/listings/{lid}/edit',headers=headers(owner),json=body2)).status_code==200
                assert (await c.get('/api/me/listings',headers=headers(owner))).status_code==200
                assert (await c.get(f'/api/me/listings/{lid}/edit-state',headers=headers(owner))).json()['operation']['text_saved']
                channel.assert_awaited_once()
                monkeypatch.setattr(city_scope,'scope_for',AsyncMock(return_value={'广州'}))
                assert (await c.get(f'/api/admin/listings/{lid}/edit-state',headers=headers(1))).status_code==403
                assert (await c.post(f'/api/admin/listings/{lid}/proxy-edit',headers=headers(1),json={**body,'operation_id':key()})).status_code==403
                monkeypatch.setattr(city_scope,'scope_for',AsyncMock(return_value={'深圳'}))
                proposal=await svc.submit(lid,owner,{**body,'media':[{'type':'image','url':'new'}]},key())
                # Guard is repeated under the actual review lock, not only before loading the route.
                monkeypatch.setattr(city_scope,'scope_for',AsyncMock(return_value={'广州'}))
                with pytest.raises(HTTPException):await svc.review(proposal['post_id'],True,actor_id=1)
                assert (await read()).media[0]['url']=='old-photo'
    asyncio.run(run())


def test_approve_reject_race_commits_only_one_decision(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            r=await svc.submit(lid,owner,{**body,'media':[{'type':'image','url':'new'}]},key())
            decisions=await asyncio.gather(svc.review(r['post_id'],True),svc.review(r['post_id'],False),return_exceptions=True)
            lamp=await read();final=await svc.state(r['operation_id'])
            assert final['review_status'] in ('approved','rejected') and lamp.expires_at==until
            assert lamp.media[0]['url']==('new' if final['review_status']=='approved' else 'old-photo')
            assert notify.await_count==1
            assert [x.args[1] for x in channel.await_args_list].count('repost')==int(final['review_status']=='approved')
    asyncio.run(run())


def test_database_unique_pending_constraint_not_only_python_lock(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            from bot.db import session_scope
            from bot.models import Post
            r=await svc.submit(lid,owner,{**body,'media':[{'type':'image','url':'new'}]},key())
            with pytest.raises(IntegrityError):
                async with session_scope() as s:s.add(Post(post_id=str(uuid.uuid4()),user_id=owner,status='pending',lamp_data={'edit_lamp_id':lid,'edit_workflow_v2':'media'}))
            assert (await svc.state(r['operation_id']))['review_status']=='pending'
    asyncio.run(run())


def test_expired_proposal_stales_if_renewed_and_legacy_approval_is_blocked(monkeypatch):
    async def run():
        async with setup(monkeypatch,'active',True) as (svc,lid,owner,until,body,read,channel,notify):
            from bot.db import session_scope
            from bot.models import Lamp,Post
            r=await svc.submit(lid,owner,{**body,'media':[{'type':'image','url':'new'}]},key())
            async with session_scope() as s:
                lamp=(await s.execute(select(Lamp).where(Lamp.lamp_id==lid))).scalar_one();lamp.expires_at=datetime.utcnow()+timedelta(days=30)
            with pytest.raises(svc.EditConflict):await svc.review(r['post_id'],True)
            assert (await read()).title=='原文字'
            old=str(uuid.uuid4())
            async with session_scope() as s:s.add(Post(post_id=old,user_id=owner,status='pending',lamp_data={'edit_lamp_id':lid,'title':'旧快照'}))
            with pytest.raises(svc.EditConflict,match='旧版'):await svc.review(old,True)
            assert await svc.review(old,False) is None
            channel.assert_not_awaited();notify.assert_not_awaited()
    asyncio.run(run())


def test_real_delivery_adapter_tracks_new_references_in_pg(monkeypatch):
    async def run():
        from bot.services.edit_delivery import channel as real_channel
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            import bot.main as main
            from types import SimpleNamespace
            from bot.services import edit_delivery,broadcast,extras_store
            fake=SimpleNamespace(send_photo=AsyncMock(return_value=SimpleNamespace(message_id=777)),edit_message_caption=AsyncMock())
            monkeypatch.setattr(main,'bot',fake)
            monkeypatch.setattr(broadcast,'_caption',AsyncMock(return_value=('@currentcity','normal caption with deep links','testbot',None)))
            monkeypatch.setattr(edit_delivery,'channel',real_channel)
            await extras_store.remember_broadcast(lid,'@oldcity',111,card_mid=112)
            await extras_store.save_extras(lid,{'_open_date':'2026-11-10','微信':'old'})
            r=await svc.submit(lid,owner,{**body,'extras':{'微信':'new'},'media':[{'type':'image','url':'new-file'}]},key())
            assert await extras_store.load_broadcast(lid)==('@oldcity','111')
            await svc.review(r['post_id'],True)
            assert await extras_store.load_broadcast(lid)==('@currentcity','777')
            ex=await extras_store.load_extras(lid);assert ex['_open_date']=='2026-11-10' and ex['微信']=='new' and ex.get('_bc_card_mid') in (None,'')
            assert (await read()).expires_at==until
            fake.send_photo.assert_awaited_once();assert notify.call_args.args[1]=='https://t.me/currentcity/777'
    asyncio.run(run())


def test_cancellation_releases_pg_lock_but_durable_sending_prohibits_resend(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc,lid,owner,until,body,read,channel,notify):
            started=asyncio.Event()
            async def stall(*args):started.set();await asyncio.Event().wait()
            channel.side_effect=stall
            token=key();task=asyncio.create_task(svc.submit(lid,owner,body,token))
            await asyncio.wait_for(started.wait(),2);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            state=await svc.submit(lid,owner,body,token)
            assert state['channel_status']=='sending' and not state['channel_retryable']
            assert channel.await_count==1
            with pytest.raises(svc.EditConflict):await svc.retry(token,'channel',owner)
    asyncio.run(run())
