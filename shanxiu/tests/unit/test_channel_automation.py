"""
tests/unit/test_channel_automation.py
-------------------------------------
Channel discussion automation helpers + wiring checks.
"""

from pathlib import Path

import pytest

from app.services.channel_automation import (
    message_filter_text,
    resolve_channel_automation_group_id,
    should_delete_as_promote,
)


class TestResolveAutomationGroupId:
    def test_prefers_channel_group(self):
        assert resolve_channel_automation_group_id(10, 20) == 10

    def test_falls_back_to_discussion(self):
        assert resolve_channel_automation_group_id(None, 20) == 20


class TestShouldDeleteAsPromote:
    def test_promote_off(self):
        assert should_delete_as_promote(False, False) is False
        assert should_delete_as_promote(False, True) is False

    def test_promote_skips_automatic_forward(self):
        assert should_delete_as_promote(True, True) is False

    def test_promote_deletes_non_linked(self):
        assert should_delete_as_promote(True, False) is True


class TestMessageFilterText:
    def test_prefers_text(self):
        class M:
            text = 'hello'
            caption = 'cap'
        assert message_filter_text(M()) == 'hello'

    def test_falls_back_to_caption(self):
        class M:
            text = None
            caption = 'photo cap'
        assert message_filter_text(M()) == 'photo cap'

    def test_none(self):
        assert message_filter_text(None) is None
        class M:
            text = None
            caption = None
        assert message_filter_text(M()) is None


class TestChannelPinWiring:
    def test_handle_channel_pin_uses_resolver(self):
        src = Path('app/modules/core/routes.py').read_text()
        assert 'resolve_channel_automation_group_id' in src
        assert 'should_delete_as_promote' in src
        # Must not early-return solely because OtherSettings is missing
        assert 'if not settings:\n                return' not in src.split('async def handle_channel_pin')[1].split('async def cmd_lottery_draw')[0]
        assert '_maybe_send_channel_auto_reply' in src
        assert 'await _maybe_send_channel_auto_reply' in src


class TestMediaPathSecurityWiring:
    def test_on_non_text_runs_keyword_and_subscription(self):
        src = Path('app/modules/core/routes.py').read_text()
        block = src.split('async def on_non_text_message')[1].split('async def on_message')[0]
        assert 'check_keyword_filter' in block
        assert 'check_forced_subscription' in block

    def test_keyword_filter_uses_caption_helper(self):
        src = Path('app/modules/core/routes.py').read_text()
        block = src.split('async def check_keyword_filter')[1].split('async def update_user_activity')[0]
        assert 'message_filter_text' in block
        assert 'select_filter_to_enforce(filter_text' in block


class TestChannelForwardRulePersistsOnChannelGroup:
    def test_save_rule_on_channel_group(self, flask_app, client):
        from app import db
        from app.models import BotGroup, ChannelForwardRule

        with flask_app.app_context():
            ch = BotGroup(chat_id='-100111', title='ch', type='channel', is_active=True)
            disc = BotGroup(chat_id='-100222', title='disc', type='supergroup', is_active=True)
            db.session.add_all([ch, disc])
            db.session.commit()
            ch_id, disc_id = ch.id, disc.id

        with client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['login_user_id'] = 1
            sess['login_user_name'] = 'tester'

        resp = client.post(
            '/core/api/save_channel_forward_rule',
            json={
                'group_id': ch_id,
                'rule_name': 'r1',
                'source_keywords': 'promo',
                'target_chat_id': '-100333',
                'forward_mode': 'copy',
                'is_active': True,
            },
        )
        data = resp.get_json() or {}
        assert data.get('status') == 'ok', data

        with flask_app.app_context():
            assert ChannelForwardRule.query.filter_by(group_id=ch_id).count() == 1
            assert ChannelForwardRule.query.filter_by(group_id=disc_id).count() == 0
            # Resolver maps channel → automation gid used at runtime
            assert resolve_channel_automation_group_id(ch_id, disc_id) == ch_id
