"""
tests/unit/test_channel_sync.py
-------------------------------
Channel post sync wiring: handlers exist, registration, channel save mirrors plugin.
"""

import inspect
from pathlib import Path

import pytest


class TestChannelSyncHandlersExist:
    def test_handle_sync_channel_posts_is_async(self):
        from app.modules.core import routes
        assert inspect.iscoroutinefunction(routes.handle_sync_channel_posts)
        assert inspect.iscoroutinefunction(routes.handle_sync_group_messages)
        assert inspect.iscoroutinefunction(routes._deliver_sync_copy)

    def test_routes_register_channel_posts_filter(self):
        src = Path('app/modules/core/routes.py').read_text()
        assert src.count('UpdateType.CHANNEL_POSTS') >= 2
        assert 'handle_sync_channel_posts' in src
        assert "chat.type == 'channel'" in src  # on_message early return


class TestChannelSaveMirrorsPlugin:
    def test_save_channel_sync_enables_plugin(self, flask_app, client):
        from app import db
        from app.models import BotGroup, SyncGroupMessages
        from app.plugins import is_plugin_enabled

        with flask_app.app_context():
            group = BotGroup(chat_id='-100888', title='ch', type='channel', is_active=True)
            db.session.add(group)
            db.session.commit()
            gid = group.id
            assert is_plugin_enabled(gid, 'sync') is False

        with client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['login_user_id'] = 1
            sess['login_user_name'] = 'tester'

        resp = client.post(
            '/core/api/save_sync_group_messages',
            json={
                'group_id': gid,
                'target_group_id': '-100999',
                'enabled': True,
                'sync_media': True,
                'sync_forwards': True,
                'filter_keywords': '[]',
            },
        )
        data = resp.get_json() or {}
        assert data.get('status') == 'ok', data

        with flask_app.app_context():
            assert is_plugin_enabled(gid, 'sync') is True
            row = SyncGroupMessages.query.filter_by(source_group_id=gid).first()
            assert row is not None and row.enabled is True
            assert row.target_group_id == '-100999'

    def test_save_group_sync_also_enables_plugin(self, flask_app, client):
        """Group and channel both mirror settings.enabled → sync plugin (single gate)."""
        from app import db
        from app.models import BotGroup, SyncGroupMessages
        from app.plugins import is_plugin_enabled

        with flask_app.app_context():
            group = BotGroup(chat_id='-100777', title='g', type='supergroup', is_active=True)
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
                'target_group_id': '-100666',
                'enabled': True,
                'sync_media': True,
                'sync_forwards': True,
                'filter_keywords': '[]',
            },
        )
        data = resp.get_json() or {}
        assert data.get('status') == 'ok', data

        with flask_app.app_context():
            assert is_plugin_enabled(gid, 'sync') is True
            row = SyncGroupMessages.query.filter_by(source_group_id=gid).first()
            assert row is not None and row.enabled is True
