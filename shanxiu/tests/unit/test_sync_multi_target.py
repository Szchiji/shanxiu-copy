"""
tests/unit/test_sync_multi_target.py
------------------------------------
Multi-target SyncGroupMessages + getChat title cache.
"""

from unittest.mock import patch

import pytest


def _login(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True


class TestFetchChatInfo:
    def test_ok_parses_title(self):
        from app.services.sync_service import fetch_chat_info

        class FakeResp:
            def json(self):
                return {
                    'ok': True,
                    'result': {
                        'id': -100123,
                        'title': '测试群',
                        'type': 'supergroup',
                        'username': 'testgroup',
                    },
                }

        with patch('requests.get', return_value=FakeResp()):
            info = fetch_chat_info('TOKEN', '-100123')
        assert info['ok'] is True
        assert info['title'] == '测试群'
        assert info['id'] == '-100123'

    def test_missing_token(self):
        from app.services.sync_service import fetch_chat_info
        assert fetch_chat_info('', '-100')['ok'] is False

    def test_api_error(self):
        from app.services.sync_service import fetch_chat_info

        class FakeResp:
            def json(self):
                return {'ok': False, 'description': 'chat not found'}

        with patch('requests.get', return_value=FakeResp()):
            info = fetch_chat_info('TOKEN', '-100')
        assert info['ok'] is False
        assert 'chat not found' in info['error']


class TestMultiTargetAPI:
    def test_add_two_targets_and_save_options(self, flask_app, client):
        from app import db
        from app.models import BotGroup, SyncGroupMessages
        from app.plugins import is_plugin_enabled

        with flask_app.app_context():
            g = BotGroup(chat_id='-100501', title='src', type='supergroup', is_active=True)
            db.session.add(g)
            db.session.commit()
            gid = g.id

        _login(client)

        with patch('app.modules.core.routes._fetch_and_set_target_title', return_value=(True, '目标甲')):
            r1 = client.post('/core/api/add_sync_target', json={
                'group_id': gid,
                'target_group_id': '-100601',
                'enabled': True,
                'sync_media': True,
                'sync_forwards': True,
                'filter_keywords': '[]',
            })
        assert (r1.get_json() or {}).get('status') == 'ok', r1.get_json()

        with patch('app.modules.core.routes._fetch_and_set_target_title', return_value=(True, '目标乙')):
            r2 = client.post('/core/api/add_sync_target', json={
                'group_id': gid,
                'target_group_id': '-100602',
                'enabled': True,
            })
        assert (r2.get_json() or {}).get('status') == 'ok', r2.get_json()

        with flask_app.app_context():
            rows = SyncGroupMessages.query.filter_by(source_group_id=gid).all()
            assert len(rows) == 2
            ids = {r.target_group_id for r in rows}
            assert ids == {'-100601', '-100602'}
            assert all(r.enabled for r in rows)
            assert is_plugin_enabled(gid, 'sync') is True

        # Save options without target_group_id applies to all
        resp = client.post('/core/api/save_sync_group_messages', json={
            'group_id': gid,
            'enabled': True,
            'sync_media': False,
            'sync_forwards': True,
            'filter_keywords': '["spam"]',
        })
        assert (resp.get_json() or {}).get('status') == 'ok'
        assert (resp.get_json() or {}).get('targets') == 2

        with flask_app.app_context():
            rows = SyncGroupMessages.query.filter_by(source_group_id=gid).all()
            assert all(r.sync_media is False for r in rows)
            assert all(r.filter_keywords == '["spam"]' for r in rows)

    def test_delete_target(self, flask_app, client):
        from app import db
        from app.models import BotGroup, SyncGroupMessages
        from app.plugins import is_plugin_enabled

        with flask_app.app_context():
            g = BotGroup(chat_id='-100511', title='src', type='supergroup', is_active=True)
            db.session.add(g)
            db.session.commit()
            gid = g.id
            a = SyncGroupMessages(source_group_id=gid, target_group_id='-100701', enabled=True, target_title='A')
            b = SyncGroupMessages(source_group_id=gid, target_group_id='-100702', enabled=True, target_title='B')
            db.session.add_all([a, b])
            db.session.commit()
            aid = a.id

        _login(client)
        resp = client.post('/core/api/delete_sync_target', json={'group_id': gid, 'id': aid})
        assert (resp.get_json() or {}).get('status') == 'ok'
        assert (resp.get_json() or {}).get('targets') == 1

        with flask_app.app_context():
            left = SyncGroupMessages.query.filter_by(source_group_id=gid).all()
            assert len(left) == 1
            assert left[0].target_group_id == '-100702'

        # Delete last → plugin off
        with flask_app.app_context():
            last_id = SyncGroupMessages.query.filter_by(source_group_id=gid).first().id
        resp2 = client.post('/core/api/delete_sync_target', json={'group_id': gid, 'id': last_id})
        assert (resp2.get_json() or {}).get('targets') == 0
        with flask_app.app_context():
            assert is_plugin_enabled(gid, 'sync') is False

    def test_refresh_title(self, flask_app, client):
        from app import db
        from app.models import BotGroup, SyncGroupMessages

        with flask_app.app_context():
            g = BotGroup(chat_id='-100521', title='src', type='channel', is_active=True)
            db.session.add(g)
            db.session.commit()
            gid = g.id
            row = SyncGroupMessages(
                source_group_id=gid, target_group_id='-100801', enabled=True, target_title='旧名'
            )
            db.session.add(row)
            db.session.commit()
            rid = row.id

        _login(client)
        with patch('app.modules.core.routes._fetch_and_set_target_title', return_value=(True, '新名称')) as mock_fetch:
            # Make the mock actually set title on the row
            def _side(group, row, chat_id=None):
                row.target_title = '新名称'
                return True, '新名称'
            mock_fetch.side_effect = _side
            resp = client.post('/core/api/refresh_sync_target_title', json={'group_id': gid, 'id': rid})
        assert (resp.get_json() or {}).get('status') == 'ok'
        assert (resp.get_json() or {}).get('target_title') == '新名称'

        with flask_app.app_context():
            assert SyncGroupMessages.query.get(rid).target_title == '新名称'

    def test_duplicate_target_rejected(self, flask_app, client):
        from app import db
        from app.models import BotGroup, SyncGroupMessages

        with flask_app.app_context():
            g = BotGroup(chat_id='-100531', title='src', type='supergroup', is_active=True)
            db.session.add(g)
            db.session.commit()
            gid = g.id
            db.session.add(SyncGroupMessages(source_group_id=gid, target_group_id='-100901', enabled=True))
            db.session.commit()

        _login(client)
        with patch('app.modules.core.routes._fetch_and_set_target_title', return_value=(True, 'X')):
            resp = client.post('/core/api/add_sync_target', json={
                'group_id': gid, 'target_group_id': '-100901', 'enabled': True,
            })
        assert (resp.get_json() or {}).get('status') == 'error'

    def test_handler_delivers_to_all_enabled_targets(self, flask_app):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app import db
        from app.models import BotGroup, SyncGroupMessages, SyncMessageLog
        from app.modules.core import routes

        with flask_app.app_context():
            g = BotGroup(chat_id='-100541', title='src', type='supergroup', is_active=True)
            db.session.add(g)
            db.session.commit()
            gid = g.id
            db.session.add(SyncGroupMessages(
                source_group_id=gid, target_group_id='-100A', enabled=True, sync_media=True, sync_forwards=True
            ))
            db.session.add(SyncGroupMessages(
                source_group_id=gid, target_group_id='-100B', enabled=True, sync_media=True, sync_forwards=True
            ))
            db.session.add(SyncGroupMessages(
                source_group_id=gid, target_group_id='-100C', enabled=False, sync_media=True, sync_forwards=True
            ))
            db.session.commit()

            routes.global_flask_app = flask_app
            bot = AsyncMock()
            bot.copy_message = AsyncMock(side_effect=lambda **kw: SimpleNamespace(message_id=kw['chat_id']))
            bot.send_message = AsyncMock()

            user = SimpleNamespace(id=1, username='u', first_name='U', last_name='', is_bot=False)
            chat = SimpleNamespace(id=-100541, type='supergroup')
            msg = SimpleNamespace(
                text='hi', caption=None, message_id=1,
                photo=None, video=None, document=None, audio=None, voice=None,
                video_note=None, sticker=None, animation=None, poll=None,
                location=None, contact=None, venue=None, forward_origin=None,
                media_group_id=None,
            )
            update = SimpleNamespace(effective_message=msg, effective_chat=chat, effective_user=user, channel_post=None)
            context = SimpleNamespace(bot=bot, application=SimpleNamespace(bot_data={'clone_id': None}))

            asyncio.get_event_loop().run_until_complete(routes.handle_sync_group_messages(update, context))

            assert bot.copy_message.await_count == 2
            sent_to = {c.kwargs['chat_id'] for c in bot.copy_message.await_args_list}
            assert sent_to == {'-100A', '-100B'}
            logs = SyncMessageLog.query.filter_by(source_group_id=gid, status='success').all()
            assert len(logs) == 2


class TestSyncPagesPassTargets:
    def test_group_page_lists_targets(self, flask_app, client):
        from app import db
        from app.models import BotGroup, SyncGroupMessages

        with flask_app.app_context():
            g = BotGroup(chat_id='-100551', title='src', type='supergroup', is_active=True)
            db.session.add(g)
            db.session.commit()
            gid = g.id
            db.session.add(SyncGroupMessages(
                source_group_id=gid, target_group_id='-100951', enabled=True, target_title='展示名'
            ))
            db.session.commit()

        _login(client)
        resp = client.get(f'/core/group/{gid}/sync_group_messages')
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert '展示名' in body
        assert '-100951' in body
        assert '添加目标' in body

    def test_channel_page_separate(self, flask_app, client):
        from app import db
        from app.models import BotGroup

        with flask_app.app_context():
            g = BotGroup(chat_id='-100561', title='ch', type='channel', is_active=True)
            db.session.add(g)
            db.session.commit()
            gid = g.id

        _login(client)
        resp = client.get(f'/core/group/{gid}/sync_channel_messages')
        assert resp.status_code == 200
        assert '频道帖同步' in resp.get_data(as_text=True)
