"""Real isolated PostgreSQL + ASGI authorization/patch tests. No production access."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
import pytest

PG=os.environ.get('YYCJ_TEST_PG')
pytestmark=pytest.mark.skipif(not PG,reason='YYCJ_TEST_PG not set')

@asynccontextmanager
async def environment(monkeypatch):
    assert PG and '127.0.0.1:55441/yycj_edit_test' in PG
    os.environ['DATABASE_URL']=PG
    import httpx
    from bot import db
    import bot.main as main
    from bot.services import admin_acl,home_service,label_sync
    from bot.api.telegram_webapp import issue_access_token
    from bot.config import get_settings
    db._engine=None
    await db.connect_db();await db._ensure_columns(db._engine)
    monkeypatch.setattr(main,'bot',AsyncMock())
    monkeypatch.setattr(label_sync,'schedule_after_save',AsyncMock())
    await admin_acl.save([9201,9202,9203])
    await home_service.update_settings(enabled_cities=['深圳','广州'],announcement_text='全站公告',announcement_enabled=True,ops_config={
        'operator_cities':{'9201':['深圳'],'9202':['广州']},
        'city_overrides':{'深圳':{'community_enabled':False,'link_label_reviews':''}},
        'city_announcements':{'深圳':{'text':'本城公告','enabled':False}},
        'bot_welcome_text':'保留欢迎正文','popup_announcement_enabled':False,
        'community_enabled':True,'link_label_reviews':'全站评价','broadcast_channel':'@workspace_test',
    })
    headers=lambda uid:{'Authorization':'Bearer '+issue_access_token(uid,get_settings().bot_token)}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://isolated-test') as client:
            yield client,headers,home_service
    finally:await db.close_db()


def run(coro):return asyncio.run(coro)


def test_workspace_scope_and_metadata_cover_all_existing_controls(monkeypatch):
    async def check():
        async with environment(monkeypatch) as(c,h,home):
            from bot.api.routes_workspace import schema
            from bot.api.routes_admin_ops import ExtraOpsBody
            assert set(ExtraOpsBody.model_fields)<=set(schema()),set(ExtraOpsBody.model_fields)-set(schema())
            assert {v['section'] for v in schema().values()}<={'welcome','approval','community','popup','announcement','cards','rules','channel','system','teacher'}
            assert (await c.get('/api/admin/workspace/config')).status_code==401
            assert (await c.get('/api/admin/workspace/config',headers=h(9201))).status_code==403
            assert (await c.get('/api/admin/workspace/config?city=广州',headers=h(9201))).status_code==403
            d=(await c.get('/api/admin/workspace/config?city=深圳',headers=h(9201))).json()
            fields={f['key']:f for f in d['fields']}
            assert d['can_edit'] and not d['can_edit_super']
            assert fields['community_enabled']['effective'] is False
            assert fields['link_label_reviews']['effective']==''
            assert fields['broadcast_channel']['protected'] and fields['broadcast_channel']['value'] is None
            assert 'bot_welcome_text' not in fields
            assert fields['announcement_enabled']['inherit_supported'] is False
            d=(await c.get('/api/admin/workspace/config?city=深圳',headers=h(9203))).json()
            assert not d['can_edit'] and not any(f['editable'] for f in d['fields'])
            # Legacy unassigned operator can read city operations, never gets global config authority.
            assert (await c.post('/api/admin/workspace/config',json={'city':'深圳','section':'community','changes':{}},headers=h(9203))).status_code==403
            assert (await c.post('/api/admin/workspace/config',json={'section':'community','changes':{}},headers=h(9203))).status_code==403
    run(check())


def test_workspace_patch_only_changed_scope_and_no_hidden_reset(monkeypatch):
    async def check():
        async with environment(monkeypatch) as(c,h,home):
            before=await home.get_or_create_settings()
            r=await c.post('/api/admin/workspace/config',headers=h(1),json={'section':'community','changes':{'community_backup_url':'https://example.invalid/backup'}})
            assert r.status_code==200,r.text
            after=await home.get_or_create_settings()
            assert after['bot_welcome_text']==before['bot_welcome_text']
            assert after['popup_announcement_enabled']==before['popup_announcement_enabled']
            assert after['city_overrides']==before['city_overrides']
            assert after['broadcast_channel']==before['broadcast_channel']
            assert after['community_backup_url']=='https://example.invalid/backup'
            r=await c.post('/api/admin/workspace/config',headers=h(9201),json={'city':'深圳','section':'community','changes':{'community_enabled':True}})
            assert r.status_code==200,r.text
            state=await home.get_or_create_settings()
            assert state['city_overrides']['深圳']['community_enabled'] is True
            assert state['city_overrides']['深圳']['link_label_reviews']==''
            assert state['community_backup_url']==after['community_backup_url']
            # Explicit blank label hides. NULL deliberately restores inheritance.
            for value in ('',None):
                r=await c.post('/api/admin/workspace/config',headers=h(9201),json={'city':'深圳','section':'channel','changes':{'link_label_reviews':value}})
                assert r.status_code==200,r.text
                f={x['key']:x for x in r.json()['config']['fields']}['link_label_reviews']
                assert f['effective']==('' if value=='' else '全站评价')
    run(check())


@pytest.mark.parametrize('uid,city,section,changes,status',[
    (9201,'深圳','channel',{'broadcast_channel':'@other'},403),
    (9201,'广州','community',{'community_enabled':False},403),
    (1,None,'community',{'popup_announcement_text':'wrong page'},400),
    (1,None,'community',{'community_enabled':'false'},400),
    (1,None,'community',{'community_discussion_url':'javascript:alert(1)'},400),
    (1,None,'rules',{'listing_days':True},400),
    (1,None,'community',{'community_enabled':None},400),
    (9201,'深圳','announcement',{'announcement_enabled':None},400),
])
def test_workspace_invalid_or_unauthorized_patch_is_atomic(monkeypatch,uid,city,section,changes,status):
    async def check():
        async with environment(monkeypatch) as(c,h,home):
            before=await home.get_or_create_settings()
            r=await c.post('/api/admin/workspace/config',headers=h(uid),json={'city':city,'section':section,'changes':changes})
            assert r.status_code==status,r.text
            after=await home.get_or_create_settings()
            assert after==before
    run(check())


def test_city_announcement_partial_save_preserves_independent_switch(monkeypatch):
    async def check():
        async with environment(monkeypatch) as(c,h,home):
            r=await c.post('/api/admin/workspace/config',headers=h(9201),json={'city':'深圳','section':'announcement','changes':{'announcement_text':None}})
            assert r.status_code==200,r.text
            assert '深圳' not in (await home.get_or_create_settings())['city_announcements']
            f={x['key']:x for x in r.json()['config']['fields']}
            assert f['announcement_text']['effective']=='全站公告' and f['announcement_enabled']['effective'] is False
            assert r.json()['config']['announcement_effective']=={'text':'全站公告','enabled':True,'source':'global'}
    run(check())


def test_review_detail_guards_pending_city_and_current_listing_city(monkeypatch):
    async def check():
        async with environment(monkeypatch) as(c,h,home):
            from bot.db import session_scope
            from bot.models import User,Lamp,Post
            lid=str(uuid.uuid4());pid=str(uuid.uuid4())
            async with session_scope() as s:
                s.add(User(user_id=9300,role='teacher'))
                s.add(Lamp(lamp_id=lid,user_id=9300,title='当前最新文字',description='后来保存的文字',city='广州',status='hidden',media=[{'type':'image','url':'old'}]))
                s.add(Post(post_id=pid,user_id=9300,status='pending',lamp_data={'city':'深圳','title':'旧快照','edit_lamp_id':lid,'edit_workflow_v2':'media','media':[{'type':'image','url':'pending'}]}))
            assert (await c.get('/api/admin/workspace/posts/'+pid,headers=h(9201))).status_code==403
            r=await c.get('/api/admin/workspace/posts/'+pid,headers=h(1))
            assert r.status_code==200,r.text
            assert r.json()['current']['title']=='当前最新文字' and r.json()['current']['status']=='hidden'
            assert r.json()['pending']['media'][0]['url']=='pending'
            assert (await c.get('/api/admin/workspace/posts/'+pid)).status_code==401
    run(check())


def test_parallel_city_and_announcement_patches_do_not_erase_other_fields(monkeypatch):
    async def check():
        async with environment(monkeypatch) as(c,h,home):
            payloads=[
              {'city':'深圳','section':'community','changes':{'community_backup_url':'https://example.invalid/sz'}},
              {'city':'广州','section':'community','changes':{'community_backup_url':'https://example.invalid/gz'}},
              {'city':'深圳','section':'announcement','changes':{'announcement_text':'并发新正文'}},
              {'city':'深圳','section':'announcement','changes':{'announcement_enabled':True}},
            ]
            responses=await asyncio.gather(*(c.post('/api/admin/workspace/config',headers=h(1),json=p) for p in payloads))
            assert all(r.status_code==200 for r in responses),[r.text for r in responses]
            site=await home.get_or_create_settings()
            assert site['city_overrides']['深圳']['community_backup_url'].endswith('/sz')
            assert site['city_overrides']['广州']['community_backup_url'].endswith('/gz')
            assert site['city_overrides']['深圳']['link_label_reviews']==''
            assert site['city_announcements']['深圳']=={'text':'并发新正文','enabled':True}
            assert site['bot_welcome_text']=='保留欢迎正文'
    run(check())


def test_all_true_admin_entrypoints_use_new_shell_without_fixture_routes(monkeypatch):
    async def check():
        async with environment(monkeypatch) as(c,h,home):
            for url in ['/app/admin.html','/app/console.html','/app/desk.html']:
                r=await c.get(url);assert r.status_code==200
                assert 'workspaceNav' in r.text and 'workspace.js?v=20261112a' in r.text
                assert 'workspace_fixture' not in r.text and 'OFFLINE_FIXTURE' not in r.text
                assert 'no-store' in r.headers['cache-control']
            r=await c.get('/app/desk-legacy.html');assert r.status_code==200
            assert 'admin-city.js' in r.text and 'workspace.js' not in r.text
            assert (await c.get('/app/tests/workspace_fixture.cjs')).status_code==404
            assert (await c.get('/api/admin/workspace/config')).status_code==401
    run(check())


def test_global_channel_save_reuses_feature_validation(monkeypatch):
    async def check():
        async with environment(monkeypatch) as(c,h,home):
            from bot.services import channel_features
            validate=AsyncMock(return_value={'broadcast_channel':'@normalized_test'})
            monkeypatch.setattr(channel_features,'validate',validate)
            r=await c.post('/api/admin/workspace/config',headers=h(1),json={'section':'channel','changes':{'broadcast_channel':'@normalized_test'}})
            assert r.status_code==200,r.text
            validate.assert_awaited_once_with({'broadcast_channel':'@normalized_test'},city=None)
            assert (await home.get_or_create_settings())['broadcast_channel']=='@normalized_test'
    run(check())
