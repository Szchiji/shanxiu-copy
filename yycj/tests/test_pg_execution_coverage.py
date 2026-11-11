"""New log adapters use REAL local PG and fake Telegram. They observe, never add resend paths."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime,timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
PG=os.environ.get('YYCJ_TEST_PG')
pytestmark=pytest.mark.skipif(not PG,reason='YYCJ_TEST_PG not set')

class FakeBot:
    def __init__(self,error=None):self.calls=[];self.error=error
    async def send_message(self,*args,**kw):
        self.calls.append(('send',args,kw))
        if self.error:raise self.error
        return NS(message_id=300+len(self.calls))
    async def edit_message_text(self,*args,**kw):
        self.calls.append(('edit',args,kw))
        if self.error:raise self.error
        return NS(message_id=kw.get('message_id',300))
    async def delete_message(self,*args,**kw):self.calls.append(('delete',args,kw));return True

@asynccontextmanager
async def env(monkeypatch,feature,*,enabled=True,channel='@coverage_test',error=None):
    assert PG and '127.0.0.1:55441/yycj_edit_test' in PG
    os.environ['DATABASE_URL']=PG
    from bot import db
    import bot.main as main
    from bot.models import User,Lamp,Review,ExecutionRecord
    from bot.models_extra import Coupon
    from bot.services import home_service,bot_info,admin_acl,tg_retry
    db._engine=None;await db.connect_db();await db._ensure_columns(db._engine)
    fake=FakeBot(error);monkeypatch.setattr(main,'bot',fake)
    monkeypatch.setattr(bot_info,'get_bot_identity',AsyncMock(return_value={'username':'CoverageTestBot'}))
    monkeypatch.setattr(tg_retry,'TG_BACKOFF',(0,0,0))
    await admin_acl.save([9411,9412,9413])
    await home_service.update_settings(enabled_cities=['深圳','广州'],ops_config={
        'operator_cities':{'9411':['深圳'],'9412':['广州']},'city_overrides':{},
        'broadcast_channel':channel,'broadcast_coupons':enabled if feature=='coupon_push' else True,
        'broadcast_reviews':enabled if feature=='featured_review' else True,
        'channel_directory':enabled if feature=='directory' else True,
        'coupon_channel':'','review_channel':'','directory_channel':'',
    })
    lid,cid,rid=[str(uuid.uuid4()) for _ in range(3)]
    async with db.session_scope() as s:
        s.add(User(user_id=9400,role='teacher'));s.add(User(user_id=9401,role='guest'))
        s.add(Lamp(lamp_id=lid,user_id=9400,title='仅本地测试',city='深圳',district='南山',status='active'))
        s.add(Coupon(coupon_id=cid,batch_id=cid,user_id=9400,lamp_id=lid,city='深圳',text='测试优惠',ends_at=datetime.utcnow()+timedelta(days=1),status='active'))
        s.add(Review(review_id=rid,lamp_id=lid,guest_id=9401,stars=5,text='测试评价',status='approved'))
    async def call():
        if feature=='coupon_push':
            from bot.services.coupon_push import publish
            return await publish(cid)
        if feature=='featured_review':
            from bot.services.broadcast import broadcast_review
            return await broadcast_review({'review_id':rid,'stars':5,'text':'测试评价','photos':[]},{'lamp_id':lid,'city':'深圳','district':'南山','title':'测试资料'})
        from bot.services.channel_directory import _refresh_city
        return await _refresh_city('深圳')
    async def rows():
        async with db.session_scope() as s:return list((await s.execute(select(ExecutionRecord).where(ExecutionRecord.feature==feature).order_by(ExecutionRecord.started_at))).scalars())
    try:yield fake,call,rows,(lid,cid,rid),db
    finally:await db.close_db()


@pytest.mark.parametrize('feature',['coupon_push','featured_review','directory'])
@pytest.mark.parametrize('enabled,channel,status,code',[(False,'@coverage_test','skipped','disabled'),(True,'','not_configured','not_configured')])
def test_disabled_and_no_channel_are_true_no_send_states(monkeypatch,feature,enabled,channel,status,code):
    async def run():
        async with env(monkeypatch,feature,enabled=enabled,channel=channel) as(fake,call,rows,ids,db):
            await call();r=await rows()
            assert len(r)==1 and r[0].status==status and r[0].reason_code==code
            assert r[0].source and r[0].config_revision is not None and r[0].finished_at>=r[0].started_at
            assert fake.calls==[]
    asyncio.run(run())


@pytest.mark.parametrize('feature',['coupon_push','featured_review','directory'])
@pytest.mark.parametrize('error_kind,status',[('forbidden','failed'),('uncertain','unknown')])
def test_delivery_failure_sanitized_and_no_generic_retry(monkeypatch,feature,error_kind,status):
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import GetMe
    error=TelegramForbiddenError(method=GetMe(),message='SECRET password=hidden initData=hidden') if error_kind=='forbidden' else RuntimeError('SECRET 123456789:TESTTOKENxxxxxxxxxxxxxxxxxxxxxxxxxxx session=password')
    async def run():
        async with env(monkeypatch,feature,error=error) as(fake,call,rows,ids,db):
            await call();r=await rows()
            assert len(fake.calls)==1  # This unknown is non-transient in existing directory tg_call.
            assert len(r)==2 and all(x.status==status for x in r)
            from bot.api.routes_execution import public_rows,execution_retry
            from fastapi import HTTPException
            public=await public_rows(r)
            assert not any(x['retryable'] for x in public)
            assert all('SECRET' not in str(x) and 'TESTTOKEN' not in str(x) and 'initData' not in str(x) for x in public)
            for record in r:
                with pytest.raises(HTTPException) as exc:await execution_retry(record.record_id,admin_id=1)
                assert exc.value.status_code==409
            assert len(fake.calls)==1
    asyncio.run(run())


@pytest.mark.parametrize('feature',['coupon_push','featured_review','directory'])
def test_success_tracks_refs_and_existing_update_dedupe_unchanged(monkeypatch,feature):
    async def run():
        async with env(monkeypatch,feature) as(fake,call,rows,ids,db):
            await call();r=await rows()
            assert r[0].status=='sent' and r[1].status=='sent' and r[1].target_ref.startswith('message:')
            assert len({x.operation_id for x in r})==1
            assert all(x.city=='深圳' and x.finished_at for x in r)
            assert all(not x[2].get('reply_markup') for x in fake.calls)  # Channel remains pure-text links.
            if feature=='coupon_push':
                await call();assert len(fake.calls)==1
                assert (await rows())[-1].status=='skipped'
            elif feature=='directory':
                await call();assert [x[0] for x in fake.calls]==['send','edit']
                assert (await rows())[-1].status=='synced'
            else:
                from bot.models import Review
                async with db.session_scope() as s:
                    row=(await s.execute(select(Review).where(Review.review_id==ids[2]))).scalar_one()
                    assert row.post_mid==301 and row.post_chat=='@coverage_test'
                # Review approval caller owns dedupe; no new retry/re-approval is invoked here.
    asyncio.run(run())


@pytest.mark.parametrize('feature',['coupon_push','featured_review','directory'])
def test_ack_then_reference_commit_failure_is_unknown_no_retry(monkeypatch,feature):
    async def run():
        async with env(monkeypatch,feature) as(fake,call,rows,ids,db):
            from bot.models import Review
            from bot.models_extra import Coupon
            from bot.models_extra import ChannelDirectory
            if feature=='coupon_push':
                from bot.services import coupon_push as module
                original=module.session_scope
                @asynccontextmanager
                async def failure():
                    async with original() as s:
                        yield s
                        if any(isinstance(o,Coupon) for o in s.dirty):raise RuntimeError('injected commit interruption')
                monkeypatch.setattr(module,'session_scope',failure)
            elif feature=='directory':
                from bot.services import channel_directory as module
                original=module.session_scope
                @asynccontextmanager
                async def failure():
                    async with original() as s:
                        yield s
                        if any(isinstance(o,ChannelDirectory) for o in list(s.dirty)+list(s.new)):raise RuntimeError('injected commit interruption')
                monkeypatch.setattr(module,'session_scope',failure)
            else:
                original=db.session_scope
                @asynccontextmanager
                async def failure():
                    async with original() as s:
                        yield s
                        if any(isinstance(o,Review) and o.post_mid for o in s.identity_map.values()):raise RuntimeError('injected ref interruption')
                monkeypatch.setattr(db,'session_scope',failure)
            if feature=='coupon_push':
                with pytest.raises(RuntimeError):await call()
            else:await call()
            r=await rows();assert len(fake.calls)==1 and r[0].status=='unknown'
            from bot.api.routes_execution import public_rows
            assert not any(x['retryable'] for x in await public_rows(r))
    asyncio.run(run())


def test_manual_and_scheduled_sources_and_log_city_isolation(monkeypatch):
    async def run():
        async with env(monkeypatch,'directory') as(fake,call,rows,ids,db):
            from bot.services import channel_directory as cd,execution_log
            monkeypatch.setattr(cd,'_schedule_retry',lambda cities:None)
            await cd.job_refresh_all()
            r=await rows();assert all(x.source=='scheduled' for x in r)
            # Guangzhou has no listings => a persisted skipped attempt; no fake send.
            assert any(x.city=='广州' and x.status=='skipped' for x in r)
            await execution_log.event('welcome','send','skipped','disabled',source='business')
            from bot.api.routes_execution import executions,execution_detail
            from fastapi import HTTPException
            args=dict(city=None,feature=None,status=None,listing_id=None,operation_id=None,since=None,until=None,limit=100,offset=0)
            sz=await executions(**args,admin_id=9411)
            assert sz['items'] and all(x['city']=='深圳' and not x['retryable'] for x in sz['items'])
            gz_record=next(x for x in r if x.city=='广州')
            with pytest.raises(HTTPException) as exc:await execution_detail(gz_record.record_id,admin_id=9411)
            assert exc.value.status_code==404
            all_operator=await executions(**args,admin_id=9413)
            assert all(x['city'] for x in all_operator['items'])
            super_view=await executions(**args,admin_id=1)
            assert any(x['city'] is None for x in super_view['items'])
    asyncio.run(run())


def test_coupon_scheduled_close_and_label_sync_are_edits_only(monkeypatch):
    async def run():
        async with env(monkeypatch,'coupon_push') as(fake,call,rows,ids,db):
            from bot.services import coupon_push as cp
            from bot.models_extra import Coupon
            await call();assert await cp.sync_edit(ids[1])=='updated'
            async with db.session_scope() as s:
                row=(await s.execute(select(Coupon).where(Coupon.coupon_id==ids[1]))).scalar_one();row.ends_at=datetime.utcnow()-timedelta(seconds=1)
            assert await cp.job_close_due()==1
            assert [x[0] for x in fake.calls]==['send','edit','edit']
            r=await rows();assert any(x.source=='scheduled' and x.step=='close' for x in r)
            assert any(x.source=='label_sync' for x in r)
            assert await cp.job_close_due()==0 and len(fake.calls)==3
    asyncio.run(run())


def test_diagnostic_store_failure_never_introduces_send_or_business_failure(monkeypatch):
    async def run():
        async with env(monkeypatch,'coupon_push') as(fake,call,rows,ids,db):
            from bot.services import execution_log
            monkeypatch.setattr(execution_log,'begin',AsyncMock(return_value=None))
            assert await call()==301
            assert len(fake.calls)==1
            assert await rows()==[]
            await call();assert len(fake.calls)==1  # Ordinary persisted post reference still dedupes.
    asyncio.run(run())
