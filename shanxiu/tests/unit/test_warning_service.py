"""
tests/unit/test_warning_service.py
----------------------------------
Unit tests for warning format helpers, escalation stub, and DB record/count.
"""

import pytest
from app.services.warning_service import (
    format_command_warn_reply,
    format_keyword_warn_reply,
    resolve_escalation,
    record_warning,
    count_warnings,
)


class TestFormatHelpers:
    def test_command_reply_includes_count(self):
        text = format_command_warn_reply('Alice', '刷屏', 3)
        assert 'Alice' in text
        assert '刷屏' in text
        assert '累计警告: 3 次' in text
        assert '⚠️' in text

    def test_command_reply_defaults(self):
        text = format_command_warn_reply('', '', 1)
        assert '该用户' in text
        assert '违反群规' in text
        assert '累计警告: 1 次' in text

    def test_keyword_reply_includes_count(self):
        text = format_keyword_warn_reply(2)
        assert '禁止关键词' in text
        assert '2 次' in text


class TestResolveEscalation:
    def test_no_auto_punish_without_settings(self):
        """No UI/settings → never escalate (production-safe default)."""
        for n in (0, 1, 3, 5, 10, 100):
            action, label = resolve_escalation(n)
            assert action is None
            assert label is None


@pytest.mark.usefixtures('db')
class TestRecordAndCount:
    def _make_group(self, db, suffix=''):
        import uuid
        from app.models import BotGroup
        g = BotGroup(
            chat_id=f'-warn-{uuid.uuid4().hex[:12]}{suffix}',
            title='test-warn-group',
            type='supergroup',
            is_active=True,
        )
        db.session.add(g)
        db.session.commit()
        return g

    def test_record_increments_count(self, db):
        g = self._make_group(db)
        c1 = record_warning(g.id, 42, admin_id=7, reason='spam', source='command')
        assert c1 == 1
        c2 = record_warning(g.id, 42, admin_id=7, reason='again', source='command')
        assert c2 == 2
        assert count_warnings(g.id, 42) == 2
        assert count_warnings(g.id, 99) == 0

    def test_keyword_source_and_null_admin(self, db):
        from app.models import GroupWarning
        g = self._make_group(db)
        c = record_warning(g.id, 55, admin_id=None, reason='关键词过滤', source='keyword')
        assert c == 1
        row = GroupWarning.query.filter_by(group_id=g.id, user_id=55).first()
        assert row is not None
        assert row.source == 'keyword'
        assert row.admin_id is None
        assert row.reason == '关键词过滤'

    def test_counts_are_per_group(self, db):
        g1 = self._make_group(db)
        from app.models import BotGroup
        g2 = BotGroup(chat_id=f'-warn2-{__import__("uuid").uuid4().hex[:12]}', title='other', type='supergroup', is_active=True)
        db.session.add(g2)
        db.session.commit()
        record_warning(g1.id, 1, reason='a')
        record_warning(g1.id, 1, reason='b')
        record_warning(g2.id, 1, reason='c')
        assert count_warnings(g1.id, 1) == 2
        assert count_warnings(g2.id, 1) == 1
