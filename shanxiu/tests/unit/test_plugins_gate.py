"""
tests/unit/test_plugins_gate.py
-------------------------------
Unit tests for plugin registry defaults / explicit on-off behavior.
"""

import pytest


@pytest.fixture
def app_ctx(flask_app):
    with flask_app.app_context():
        yield flask_app


class TestIsPluginEnabledDefaultOn:
    def test_no_row_defaults_true(self, app_ctx):
        from app.plugins import is_plugin_enabled
        # group_id 99999 has no settings rows
        assert is_plugin_enabled(99999, 'points') is True
        assert is_plugin_enabled(99999, 'lottery') is True
        assert is_plugin_enabled(99999, 'spam') is True

    def test_sync_defaults_false_when_no_row(self, app_ctx):
        from app.plugins import get_plugin_default_enabled, is_plugin_enabled
        assert get_plugin_default_enabled('sync') is False
        assert is_plugin_enabled(99999, 'sync') is False

    def test_explicit_disable(self, app_ctx):
        from app import db
        from app.models import BotGroup, GroupPluginSettings
        from app.plugins import is_plugin_enabled

        group = BotGroup(chat_id='-1001', title='t', type='supergroup', is_active=True)
        db.session.add(group)
        db.session.commit()

        GroupPluginSettings.set_enabled(db.session, group.id, 'points', False)
        assert is_plugin_enabled(group.id, 'points') is False
        # Other plugins still default on
        assert is_plugin_enabled(group.id, 'lottery') is True

    def test_explicit_enable(self, app_ctx):
        from app import db
        from app.models import BotGroup, GroupPluginSettings
        from app.plugins import is_plugin_enabled

        group = BotGroup(chat_id='-1002', title='t2', type='supergroup', is_active=True)
        db.session.add(group)
        db.session.commit()

        GroupPluginSettings.set_enabled(db.session, group.id, 'sync', True)
        assert is_plugin_enabled(group.id, 'sync') is True

    def test_for_chat_missing_group_defaults_true(self, app_ctx):
        from app.plugins import is_plugin_enabled_for_chat
        assert is_plugin_enabled_for_chat('-999', None, 'spam') is True
