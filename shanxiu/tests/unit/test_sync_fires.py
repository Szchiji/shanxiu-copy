"""
tests/unit/test_sync_fires.py
-----------------------------
Sync must fire from SyncGroupMessages.enabled alone (no dual plugin gate),
and save/toggle keep plugin rows aligned. Logs page filters by source_group_id.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestSaveMirrorsPluginForGroups:
    def test_save_group_sync_enables_plugin(self, flask_app, client):
        from app import db
        from app.models import BotGroup, SyncGroupMessages
        from app.plugins import is_plugin_enabled

        with flask_app.app_context():
            group = BotGroup(chat_id='-100701', title='g', type='supergroup', is_active=True)
            db.session.add(group)
            db.session.commit()
            gid = group.id
            assert is_plugin_enabled(gid, 'sync') is False

        with client.session_transaction() as sess:
            sess['logged_in'] = True

        resp = client.post(
            '/core/api/save_sync_group_messages',
            json={
                'group_id': gid,
                'target_group_id': '-100702',
                'enabled': True,
                'sync_media': True,
                'sync_forwards': True,
                'filter_keywords': '[]',
            },
        )
        assert (resp.get_json() or {}).get('status') == 'ok'

        with flask_app.app_context():
            assert is_plugin_enabled(gid, 'sync') is True
            row = SyncGroupMessages.query.filter_by(source_group_id=gid).first()
            assert row is not None and row.enabled is True

    def test_save_disable_turns_plugin_off(self, flask_app, client):
        from app import db
        from app.models import BotGroup, GroupPluginSettings, SyncGroupMessages
        from app.plugins import is_plugin_enabled

        with flask_app.app_context():
            group = BotGroup(chat_id='-100703', title='g2', type='supergroup', is_active=True)
            db.session.add(group)
            db.session.commit()
            gid = group.id
            GroupPluginSettings.set_enabled(db.session, gid, 'sync', True)

        with client.session_transaction() as sess:
            sess['logged_in'] = True

        resp = client.post(
            '/core/api/save_sync_group_messages',
            json={
                'group_id': gid,
                'target_group_id': '-100704',
                'enabled': False,
                'sync_media': True,
                'sync_forwards': True,
                'filter_keywords': '[]',
            },
        )
        assert (resp.get_json() or {}).get('status') == 'ok'

        with flask_app.app_context():
            assert is_plugin_enabled(gid, 'sync') is False
            row = SyncGroupMessages.query.filter_by(source_group_id=gid).first()
            assert row is not None and row.enabled is False


class TestTogglePluginMirrorsSyncSettings:
    def test_toggle_sync_off_disables_settings(self, flask_app, client):
        from app import db
        from app.models import BotGroup, GroupPluginSettings, SyncGroupMessages

        with flask_app.app_context():
            group = BotGroup(chat_id='-100705', title='g3', type='supergroup', is_active=True)
            db.session.add(group)
            db.session.commit()
            gid = group.id
            db.session.add(SyncGroupMessages(
                source_group_id=gid, target_group_id='-100706', enabled=True
            ))
            GroupPluginSettings.set_enabled(db.session, gid, 'sync', True)

        with client.session_transaction() as sess:
            sess['logged_in'] = True

        resp = client.post(
            '/core/api/toggle_plugin',
            json={'group_id': gid, 'plugin_name': 'sync', 'enabled': False},
        )
        data = resp.get_json() or {}
        assert data.get('success') is True

        with flask_app.app_context():
            row = SyncGroupMessages.query.filter_by(source_group_id=gid).first()
            assert row.enabled is False
            assert GroupPluginSettings.is_enabled(gid, 'sync') is False


class TestHandlerUsesSettingsOnly:
    def test_group_sync_writes_log_even_if_plugin_off(self, flask_app):
        """Regression: dual gate left settings.enabled=True but plugin False → no logs."""
        from app import db
        from app.models import BotGroup, SyncGroupMessages, SyncMessageLog
        from app.modules.core import routes

        with flask_app.app_context():
            group = BotGroup(chat_id='-100801', title='src', type='supergroup', is_active=True)
            db.session.add(group)
            db.session.commit()
            gid = group.id
            # Plugin stays default-off; settings enabled — must still sync
            db.session.add(SyncGroupMessages(
                source_group_id=gid,
                target_group_id='-100802',
                enabled=True,
                sync_media=True,
                sync_forwards=True,
                filter_keywords='[]',
            ))
            db.session.commit()

            routes.global_flask_app = flask_app

            sent = SimpleNamespace(message_id=99)
            bot = AsyncMock()
            bot.copy_message = AsyncMock(return_value=sent)
            bot.send_message = AsyncMock(return_value=sent)

            user = SimpleNamespace(id=42, username='alice', first_name='Alice', last_name='', is_bot=False)
            chat = SimpleNamespace(id=-100801, type='supergroup')
            msg = SimpleNamespace(
                text='hello sync',
                caption=None,
                message_id=7,
                photo=None, video=None, document=None, audio=None, voice=None,
                video_note=None, sticker=None, animation=None, poll=None,
                location=None, contact=None, venue=None,
                forward_origin=None,
                media_group_id=None,
            )
            update = SimpleNamespace(
                effective_message=msg,
                effective_chat=chat,
                effective_user=user,
                channel_post=None,
            )
            context = SimpleNamespace(
                bot=bot,
                application=SimpleNamespace(bot_data={'clone_id': None}),
            )

            asyncio.get_event_loop().run_until_complete(
                routes.handle_sync_group_messages(update, context)
            )

            # No prefix → copy_message keeps original formatting
            bot.copy_message.assert_awaited()
            assert bot.copy_message.await_args.kwargs['message_id'] == 7
            bot.send_message.assert_not_awaited()
            logs = SyncMessageLog.query.filter_by(source_group_id=gid).all()
            assert len(logs) == 1
            assert logs[0].status == 'success'
            assert logs[0].content_preview == 'hello sync'

    def test_group_sync_prefix_when_include_sender_prefix(self, flask_app):
        from app import db
        from app.models import BotGroup, SyncGroupMessages
        from app.modules.core import routes

        with flask_app.app_context():
            group = BotGroup(chat_id='-100811', title='src', type='supergroup', is_active=True)
            db.session.add(group)
            db.session.commit()
            gid = group.id
            db.session.add(SyncGroupMessages(
                source_group_id=gid,
                target_group_id='-100812',
                enabled=True,
                sync_media=True,
                sync_forwards=True,
                include_sender_prefix=True,
                filter_keywords='[]',
            ))
            db.session.commit()

            routes.global_flask_app = flask_app

            sent = SimpleNamespace(message_id=100)
            bot = AsyncMock()
            bot.copy_message = AsyncMock(side_effect=AssertionError('should not copy when prefix on'))
            bot.send_message = AsyncMock(return_value=sent)

            user = SimpleNamespace(id=42, username='alice', first_name='Alice', last_name='', is_bot=False)
            chat = SimpleNamespace(id=-100811, type='supergroup')
            msg = SimpleNamespace(
                text='hello sync',
                caption=None,
                message_id=8,
                photo=None, video=None, document=None, audio=None, voice=None,
                video_note=None, sticker=None, animation=None, poll=None,
                location=None, contact=None, venue=None,
                forward_origin=None,
                media_group_id=None,
                text_html=None,
                entities=None,
            )
            update = SimpleNamespace(
                effective_message=msg,
                effective_chat=chat,
                effective_user=user,
                channel_post=None,
            )
            context = SimpleNamespace(
                bot=bot,
                application=SimpleNamespace(bot_data={'clone_id': None}),
            )

            asyncio.get_event_loop().run_until_complete(
                routes.handle_sync_group_messages(update, context)
            )

            bot.send_message.assert_awaited()
            kwargs = bot.send_message.await_args.kwargs
            assert kwargs['parse_mode'] == 'HTML'
            assert kwargs['text'] == '<a href="tg://user?id=42">Alice</a>\nhello sync'

    def test_channel_sync_writes_log_when_enabled(self, flask_app):
        from app import db
        from app.models import BotGroup, SyncGroupMessages, SyncMessageLog
        from app.modules.core import routes

        with flask_app.app_context():
            group = BotGroup(chat_id='-100901', title='ch', type='channel', is_active=True)
            db.session.add(group)
            db.session.commit()
            gid = group.id
            db.session.add(SyncGroupMessages(
                source_group_id=gid,
                target_group_id='-100902',
                enabled=True,
                sync_media=True,
                sync_forwards=True,
                filter_keywords='[]',
            ))
            db.session.commit()

            routes.global_flask_app = flask_app

            sent = SimpleNamespace(message_id=55)
            bot = AsyncMock()
            bot.copy_message = AsyncMock(return_value=sent)
            bot.send_message = AsyncMock(return_value=sent)

            chat = SimpleNamespace(id=-100901, type='channel', username='mych')
            msg = SimpleNamespace(
                text='channel post',
                caption=None,
                message_id=3,
                photo=None, video=None, document=None, audio=None, voice=None,
                video_note=None, sticker=None, animation=None, poll=None,
                location=None, contact=None, venue=None,
                forward_origin=None,
                media_group_id=None,
            )
            update = SimpleNamespace(
                channel_post=msg,
                effective_message=msg,
                effective_chat=chat,
                effective_user=None,
            )
            context = SimpleNamespace(
                bot=bot,
                application=SimpleNamespace(bot_data={'clone_id': None}),
                job_queue=MagicMock(),
            )

            asyncio.get_event_loop().run_until_complete(
                routes.handle_sync_channel_posts(update, context)
            )

            bot.copy_message.assert_awaited()
            assert bot.copy_message.await_args.kwargs['message_id'] == 3
            bot.send_message.assert_not_awaited()
            logs = SyncMessageLog.query.filter_by(source_group_id=gid).all()
            assert len(logs) == 1
            assert logs[0].status == 'success'


class TestSyncLogsPageQuery:
    def test_logs_page_lists_by_source_group_id(self, flask_app, client):
        from app import db
        from app.models import BotGroup, SyncMessageLog
        from app.utils import get_beijing_now

        with flask_app.app_context():
            g1 = BotGroup(chat_id='-100911', title='a', type='supergroup', is_active=True)
            g2 = BotGroup(chat_id='-100912', title='b', type='supergroup', is_active=True)
            db.session.add_all([g1, g2])
            db.session.commit()
            db.session.add(SyncMessageLog(
                source_group_id=g1.id,
                target_group_id='-100999',
                status='success',
                content_preview='only-g1',
                synced_at=get_beijing_now(),
            ))
            db.session.add(SyncMessageLog(
                source_group_id=g2.id,
                target_group_id='-100998',
                status='success',
                content_preview='only-g2',
                synced_at=get_beijing_now(),
            ))
            db.session.commit()
            gid = g1.id

        with client.session_transaction() as sess:
            sess['logged_in'] = True

        resp = client.get(f'/core/group/{gid}/sync_message_logs')
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'only-g1' in body
        assert 'only-g2' not in body


class TestWebhookAllowsChannelPost:
    def test_run_bot_sets_allowed_updates(self):
        src = open('app/modules/core/routes.py').read()
        assert 'allowed_updates=_all_allowed' in src
        assert 'Update.ALL_TYPES' in src
        assert "channel_post" in src or 'ALL_TYPES' in src
