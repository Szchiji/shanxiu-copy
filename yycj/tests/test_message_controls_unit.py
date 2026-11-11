import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from bot.services.message_buttons import clean,resolve,approval_items,keyboard
from bot.services.home_feed import merge_ops,ops_for_city


def test_legacy_welcome_order_two_columns_and_extended_per_context_limits():
    legacy=[{'type':'webapp','label':str(i),'target':'home'} for i in range(6)]
    rows=resolve(legacy,'welcome',base='https://app.test/app')
    assert [[x['label'] for x in r] for r in rows]==[['0','1'],['2','3'],['4','5']]
    assert len(clean(legacy*4,'welcome'))==12 and len(clean(legacy*4,'approval'))==16


def test_rows_order_disabled_and_safe_targets():
    items=[{'type':'url','label':'A','target':'t.me/testbot','new_row':True},
           {'type':'webapp','label':'B','target':'me','new_row':False,'enabled':False},
           {'type':'url','label':'C','target':'https://example.test/','new_row':False},
           {'type':'webapp','label':'D','target':'home','new_row':True}]
    rows=resolve(items,'welcome',base='https://app.test/app')
    assert [[b['label'] for b in row] for row in rows]==[['A','C'],['D']]
    assert [[b['label'] for b in row] for row in resolve(list(reversed(items)),'welcome',base='https://app.test/app')]==[['D','C'],['A']]
    assert len(resolve(items,'welcome',base='https://app.test/app',private=False))==1
    for target in ['javascript:alert(1)','data:text/html,hi','https://user:password@example.test','https://ex ample.test','http://example.test']:
        assert clean([{'type':'url','label':'bad','target':target}])==[]
    assert clean([{'type':'callback','label':'bad','target':'admin:delete:all'}])==[]
    assert clean([{'type':'webapp','label':'bad','target':'https://evil.test'}])==[]


def test_default_approval_buttons_custom_empty_and_inheritance():
    site=merge_ops({})
    rows=resolve(approval_items(site),'approval',detail_webapp='https://app.test/go.html?lamp=1',album='https://t.me/test/9')
    assert [[b['label'] for b in row] for row in rows]==[['查看我的资料'],['查看频道帖']]
    assert len(resolve(approval_items(site),'approval',detail_webapp='https://app.test/go.html?lamp=1'))==1
    custom={'mode':'custom','items':[]}
    site=merge_ops({'approval_buttons':custom,'city_overrides':{'深圳':{'approval_notify_enabled':False},'广州':{'approval_buttons':{'mode':'default'}}}})
    assert approval_items(site)==[] and ops_for_city(site,'深圳')['approval_notify_enabled'] is False
    assert len(approval_items(ops_for_city(site,'广州')))==2
    assert ops_for_city(merge_ops({'city_overrides':{'深圳':{'link_label_detail':''}}}),'深圳')['link_label_detail']==''
    assert keyboard([]) is None


def test_notification_disabled_skips_before_identity_or_bot_send(monkeypatch):
    import bot.main as main
    from bot.services import listing_notify,home_service,bot_info,execution_log
    site=merge_ops({'city_overrides':{'深圳':{'approval_notify_enabled':False}}})
    monkeypatch.setattr(home_service,'get_or_create_settings',AsyncMock(return_value=site))
    identity=AsyncMock();send=AsyncMock();finish=AsyncMock()
    monkeypatch.setattr(bot_info,'get_bot_identity',identity);monkeypatch.setattr(main,'bot',SimpleNamespace(send_message=send))
    monkeypatch.setattr(execution_log,'begin',AsyncMock(return_value='record'));monkeypatch.setattr(execution_log,'finish',finish)
    assert asyncio.run(listing_notify.notify_publisher_approved(77,'teacher','https://t.me/test/9','深圳','lamp1',raise_errors=True)) is None
    send.assert_not_awaited();identity.assert_not_awaited();finish.assert_awaited_once_with('record','skipped','disabled')


def test_welcome_overall_switch_does_not_send_or_remove_admin_entry(monkeypatch):
    from bot.handlers import start
    from bot.services import execution_log
    monkeypatch.setattr(start.users,'ensure_user',AsyncMock())
    monkeypatch.setattr(start,'_force_join',AsyncMock(return_value=False))
    monkeypatch.setattr(start.home_service,'get_or_create_settings',AsyncMock(return_value=merge_ops({'bot_welcome_enabled':False})))
    monkeypatch.setattr(start,'get_settings',lambda:SimpleNamespace(normalized_webapp_url='https://app.test/app',is_admin=lambda _:False))
    monkeypatch.setattr(execution_log,'begin',AsyncMock(return_value='record'));finish=AsyncMock();monkeypatch.setattr(execution_log,'finish',finish)
    message=SimpleNamespace(from_user=SimpleNamespace(id=77,username='test',full_name='Test'),text='/start',message_id=123,chat=SimpleNamespace(type='private'),answer=AsyncMock())
    import inspect
    handler=getattr(start,'cmd_start',None) or getattr(start,'start',None)
    assert handler is not None,[n for n,v in vars(start).items() if inspect.iscoroutinefunction(v)]
    asyncio.run(handler(message,None))
    message.answer.assert_not_awaited();finish.assert_awaited_once_with('record','skipped','disabled')


def test_log_secrets_are_never_retained():
    from bot.services.execution_log import safe,reason
    for value in ['123456789:TESTTOKENxxxxxxxxxxxxxxxxxxxxxxxxxxx','Bearer.abc','initData:private','password','session-secret','https://example.test/?token=abc']:
        assert safe(value) is None
    assert safe('operation-12345678')=='operation-12345678'
    assert reason(TimeoutError('Bearer TOKEN SECRET'))=='delivery_unknown'
