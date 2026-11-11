"""小号登录 admin routes: auth, encrypted storage, login steps (manager mocked), rules CRUD."""

import json

import pytest


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s['logged_in'] = True
        s.pop('clone_id', None)
    return client


@pytest.fixture(autouse=True)
def _clean(flask_app):
    from app import db
    from app.models import UserbotAccount, UserbotSyncRule
    with flask_app.app_context():
        UserbotSyncRule.query.delete()
        UserbotAccount.query.delete()
        db.session.commit()
    yield


def test_requires_login(client):
    assert client.get('/core/api/userbot/status').status_code == 401
    assert client.post('/core/api/userbot/apikey/manual', json={}).status_code == 401
    r = client.get('/core/userbot')
    assert r.status_code == 302 and r.headers['Location'].endswith('/core')


def test_clone_admin_denied(client):
    with client.session_transaction() as s:
        s['logged_in'] = True
        s['clone_id'] = 3
    assert client.get('/core/api/userbot/status').status_code == 403
    assert client.get('/core/userbot').status_code == 302


def test_page_renders_for_admin(admin):
    r = admin.get('/core/userbot')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert '小号登录' in body and '第一步' in body and '/core/userbot' in body


def test_manual_api_saved_encrypted(admin, flask_app):
    from app.models import UserbotAccount
    from app.userbot.crypto import decrypt_secret
    bad = admin.post('/core/api/userbot/apikey/manual', json={'api_id': 'x', 'api_hash': 'y'}).get_json()
    assert bad['status'] == 'error'
    h = 'ABCDEF0123456789abcdef0123456789'
    ok = admin.post('/core/api/userbot/apikey/manual', json={'api_id': '1234567', 'api_hash': h}).get_json()
    assert ok['status'] == 'ok'
    with flask_app.app_context():
        acct = UserbotAccount.query.first()
        assert acct.api_id == 1234567 and h.lower() not in acct.api_hash_enc
        assert decrypt_secret(acct.api_hash_enc) == h.lower()
    st = admin.get('/core/api/userbot/status').get_json()['data']
    assert st['has_api'] and not st['logged_in'] and 'api_hash' not in json.dumps(st)


def test_login_requires_api_first(admin):
    r = admin.post('/core/api/userbot/login/send_code', json={'phone': '+8613800000000'}).get_json()
    assert r['status'] == 'error' and '第一步' in r['msg']


def test_login_steps_with_2fa(admin, monkeypatch):
    from app.userbot import web
    admin.post('/core/api/userbot/apikey/manual', json={'api_id': '111', 'api_hash': 'a' * 32})
    calls = {}

    def fake_send(api_id, api_hash, phone):
        calls['send'] = (api_id, api_hash, phone)
        return 'flow1'

    monkeypatch.setattr(web.userbot_manager, 'login_send_code', fake_send)
    monkeypatch.setattr(web.userbot_manager, 'login_verify_code', lambda fid, code: None)
    monkeypatch.setattr(web.userbot_manager, 'login_verify_password',
                        lambda fid, pw: {'id': 9, 'username': 'u'} if (fid, pw) == ('flow1', 'pw') else None)
    r = admin.post('/core/api/userbot/login/send_code', json={'phone': '+86 138 0000 0000'}).get_json()
    assert r['status'] == 'ok' and calls['send'] == (111, 'a' * 32, '+8613800000000')
    r = admin.post('/core/api/userbot/login/verify_code', json={'code': '12345'}).get_json()
    assert r['status'] == 'ok' and r['need_password'] is True
    r = admin.post('/core/api/userbot/login/verify_password', json={'password': 'pw'}).get_json()
    assert r['status'] == 'ok'


def test_login_error_message_passthrough(admin, monkeypatch):
    from app.userbot import web
    from app.userbot.manager import UserbotError
    admin.post('/core/api/userbot/apikey/manual', json={'api_id': '111', 'api_hash': 'b' * 32})

    def boom(*a):
        raise UserbotError('验证码错误。')

    with admin.session_transaction() as s:
        s['userbot_login_flow'] = 'f'
    monkeypatch.setattr(web.userbot_manager, 'login_verify_code', boom)
    r = admin.post('/core/api/userbot/login/verify_code', json={'code': '1'}).get_json()
    assert r == {'status': 'error', 'msg': '验证码错误。'}


def test_apikey_auto_flow_errors_are_friendly(admin, monkeypatch):
    from app.userbot import web
    from app.userbot.my_telegram import MyTelegramError

    def boom(phone):
        raise MyTelegramError('my.telegram.org 拒绝创建应用（返回 ERROR）。你也可以手动…')

    monkeypatch.setattr(web.userbot_manager, 'mt_send_code', boom)
    r = admin.post('/core/api/userbot/apikey/send_code', json={'phone': '+8613800000000'}).get_json()
    assert r['status'] == 'error' and '手动' in r['msg']
    r = admin.post('/core/api/userbot/apikey/send_code', json={'phone': '12'}).get_json()
    assert r['status'] == 'error' and '区号' in r['msg']


def test_rules_crud(admin):
    r = admin.post('/core/api/userbot/rules/save', json={'source_chat_id': 'abc', 'target_chat_ids': ['-1001']}).get_json()
    assert r['status'] == 'error'
    r = admin.post('/core/api/userbot/rules/save', json={'source_chat_id': '-100555', 'target_chat_ids': []}).get_json()
    assert r['status'] == 'error'
    r = admin.post('/core/api/userbot/rules/save', json={
        'source_chat_id': '-100555', 'source_title': 'Src', 'target_chat_ids': ['-100900', '-100555', 'x', '-100900'],
        'sender_mode': 'selected', 'sender_filter': '@a_bot', 'include_sender_prefix': True,
        'sender_prefix_style': 'forward', 'filter_keywords': '广告, spam',
    }).get_json()
    assert r['status'] == 'ok'
    rule = r['data']
    assert rule['target_chat_ids'] == ['-100900'] and rule['filter_keywords'] == ['广告', 'spam']
    assert rule['sender_mode'] == 'selected' and rule['sender_prefix_style'] == 'forward'
    t = admin.post(f"/core/api/userbot/rules/{rule['id']}/toggle").get_json()
    assert t['enabled'] is False
    assert len(admin.get('/core/api/userbot/rules').get_json()['data']) == 1
    admin.post(f"/core/api/userbot/rules/{rule['id']}/delete")
    assert admin.get('/core/api/userbot/rules').get_json()['data'] == []


def test_rules_users_mode(admin):
    r = admin.post('/core/api/userbot/rules/save', json={
        'source_chat_id': '-100555', 'target_chat_ids': ['-100900'],
        'sender_mode': 'users',
    }).get_json()
    assert r['status'] == 'ok'
    assert r['data']['sender_mode'] == 'users'
    body = admin.get('/core/userbot').get_data(as_text=True)
    assert '只监控用户消息' in body and 'ubModeUsers' in body


def test_start_from_env_noop_without_session(flask_app, capsys):
    from app.userbot.manager import start_userbot_from_env, userbot_manager
    start_userbot_from_env(flask_app)
    assert 'no saved session' in capsys.readouterr().out
    assert userbot_manager.runtime_task is None
