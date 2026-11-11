"""Apply Telegram nickname tags: skip other admins / departed users, no retry spam."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

BOT_TOKEN = '100001:unit-test-token'
CHAT_ID = -1001332469029
ADMIN_TG_ID = 8868591406
MEMBER_TG_ID = 555000111
LEFT_TG_ID = 101414477
BOT_ID = 100001


@pytest.fixture()
def tag_env(flask_app, monkeypatch):
    from app import db
    from app.models import BotGroup, GroupUser
    from app.modules.core import routes

    monkeypatch.setattr(routes, 'global_flask_app', flask_app)
    routes._bot_manage_tags_cache.clear()

    with flask_app.app_context():
        group = BotGroup(
            chat_id=str(CHAT_ID),
            title='深圳.茶餐厅',
            type='supergroup',
            is_active=True,
            config='{"auto_member_tag": true, "default_member_tag": "认证"}',
        )
        db.session.add(group)
        db.session.commit()
        yield SimpleNamespace(app=flask_app, db=db, routes=routes, group=group, GroupUser=GroupUser)
        routes._bot_manage_tags_cache.clear()
        db.session.rollback()


def _add_user(env, tg_id, tags='["上榜老师"]', applied=None, skip=None):
    user = env.GroupUser(
        group_id=env.group.id,
        tg_id=tg_id,
        member_tags=tags,
        applied_telegram_tag=applied,
        telegram_tag_skip=skip,
    )
    env.db.session.add(user)
    env.db.session.commit()
    return user


def _apply(env, user, force=False, token=BOT_TOKEN):
    return env.routes.apply_telegram_member_tag_for_user(
        env.group, user, token=token, force=force
    )


def _reload(env, user):
    env.db.session.refresh(user)
    return user


def _bot_admin_ok(_chat_id, user_id, _token):
    if int(user_id) == BOT_ID:
        return {'status': 'administrator', 'can_manage_tags': True}, None
    raise AssertionError(f'unexpected getChatMember user_id={user_id}')


def test_skip_admin_does_not_call_set_and_remembers(tag_env, capsys):
    env = tag_env
    user = _add_user(env, ADMIN_TG_ID)

    def get_member(_chat_id, user_id, _token):
        if int(user_id) == BOT_ID:
            return {'status': 'administrator', 'can_manage_tags': True}, None
        assert int(user_id) == ADMIN_TG_ID
        return {'status': 'administrator'}, None

    with patch.object(env.routes, '_telegram_get_chat_member', side_effect=get_member) as get_cm, \
            patch.object(env.routes, '_set_chat_member_tag') as set_tag:
        ok = _apply(env, user)

    assert ok is False
    set_tag.assert_not_called()
    assert get_cm.called
    user = _reload(env, user)
    assert user.telegram_tag_skip == 'admin'
    assert user.applied_telegram_tag is None
    out = capsys.readouterr().out
    assert f'[MemberTag] skip admin user={ADMIN_TG_ID}' in out
    assert '❌' not in out
    assert 'Traceback' not in out


def test_remembered_admin_is_not_retried(tag_env):
    env = tag_env
    user = _add_user(env, ADMIN_TG_ID, skip='admin')

    with patch.object(env.routes, '_telegram_get_chat_member') as get_cm, \
            patch.object(env.routes, '_set_chat_member_tag') as set_tag:
        ok = _apply(env, user, force=False)

    assert ok is True
    get_cm.assert_not_called()
    set_tag.assert_not_called()


def test_force_rechecks_admin_and_still_skips_api(tag_env, capsys):
    env = tag_env
    user = _add_user(env, ADMIN_TG_ID, skip='admin')

    def get_member(_chat_id, user_id, _token):
        if int(user_id) == BOT_ID:
            return {'status': 'administrator', 'can_manage_tags': True}, None
        return {'status': 'creator'}, None

    with patch.object(env.routes, '_telegram_get_chat_member', side_effect=get_member), \
            patch.object(env.routes, '_set_chat_member_tag') as set_tag:
        ok = _apply(env, user, force=True)

    assert ok is False
    set_tag.assert_not_called()
    assert _reload(env, user).telegram_tag_skip == 'admin'
    assert f'[MemberTag] skip admin user={ADMIN_TG_ID}' in capsys.readouterr().out


def test_regular_member_is_tagged_as_before(tag_env):
    env = tag_env
    user = _add_user(env, MEMBER_TG_ID, tags='["靠谱认证老师"]')

    def get_member(_chat_id, user_id, _token):
        if int(user_id) == BOT_ID:
            return {'status': 'administrator', 'can_manage_tags': True}, None
        assert int(user_id) == MEMBER_TG_ID
        return {'status': 'member'}, None

    with patch.object(env.routes, '_telegram_get_chat_member', side_effect=get_member), \
            patch.object(
                env.routes, '_set_chat_member_tag', return_value=(True, None)
            ) as set_tag:
        ok = _apply(env, user)

    assert ok is True
    set_tag.assert_called_once()
    args, kwargs = set_tag.call_args
    assert args[0] == CHAT_ID
    assert args[1] == MEMBER_TG_ID
    assert args[2] == '靠谱认证老师'
    user = _reload(env, user)
    assert user.applied_telegram_tag == '靠谱认证老师'
    assert user.telegram_tag_skip is None


def test_already_applied_tag_skips_api(tag_env):
    env = tag_env
    user = _add_user(env, MEMBER_TG_ID, applied='上榜老师')

    with patch.object(env.routes, '_telegram_get_chat_member') as get_cm, \
            patch.object(env.routes, '_set_chat_member_tag') as set_tag:
        ok = _apply(env, user)

    assert ok is True
    get_cm.assert_not_called()
    set_tag.assert_not_called()


def test_chat_creator_required_fallback_remembers_admin(tag_env, capsys):
    env = tag_env
    user = _add_user(env, ADMIN_TG_ID)

    def get_member(_chat_id, user_id, _token):
        if int(user_id) == BOT_ID:
            return {'status': 'administrator', 'can_manage_tags': True}, None
        # Membership lookup missed the admin status; API still refuses.
        return {'status': 'member'}, None

    with patch.object(env.routes, '_telegram_get_chat_member', side_effect=get_member), \
            patch.object(
                env.routes,
                '_set_chat_member_tag',
                return_value=(False, 'Bad Request: CHAT_CREATOR_REQUIRED'),
            ) as set_tag:
        ok = _apply(env, user)

    assert ok is False
    set_tag.assert_called_once()
    user = _reload(env, user)
    assert user.telegram_tag_skip == 'admin'
    assert user.applied_telegram_tag is None
    out = capsys.readouterr().out
    assert f'[MemberTag] skip admin user={ADMIN_TG_ID}' in out
    assert '❌' not in out


def test_left_user_is_skipped_and_remembered(tag_env, capsys):
    env = tag_env
    user = _add_user(env, LEFT_TG_ID)

    def get_member(_chat_id, user_id, _token):
        if int(user_id) == BOT_ID:
            return {'status': 'administrator', 'can_manage_tags': True}, None
        return {'status': 'left'}, None

    with patch.object(env.routes, '_telegram_get_chat_member', side_effect=get_member), \
            patch.object(env.routes, '_set_chat_member_tag') as set_tag:
        ok = _apply(env, user)

    assert ok is False
    set_tag.assert_not_called()
    assert _reload(env, user).telegram_tag_skip == 'left'
    out = capsys.readouterr().out
    assert f'[MemberTag] skip left user={LEFT_TG_ID}' in out
    assert '❌' not in out


def test_participant_id_invalid_is_remembered_as_left(tag_env):
    env = tag_env
    user = _add_user(env, LEFT_TG_ID)

    def get_member(_chat_id, user_id, _token):
        if int(user_id) == BOT_ID:
            return {'status': 'administrator', 'can_manage_tags': True}, None
        return None, 'Bad Request: PARTICIPANT_ID_INVALID'

    with patch.object(env.routes, '_telegram_get_chat_member', side_effect=get_member), \
            patch.object(env.routes, '_set_chat_member_tag') as set_tag:
        ok = _apply(env, user)

    assert ok is False
    set_tag.assert_not_called()
    assert _reload(env, user).telegram_tag_skip == 'left'


def test_remembered_left_user_is_tagged_after_returning(tag_env):
    env = tag_env
    user = _add_user(env, LEFT_TG_ID, skip='left')

    def get_member(_chat_id, user_id, _token):
        if int(user_id) == BOT_ID:
            return {'status': 'administrator', 'can_manage_tags': True}, None
        return {'status': 'member'}, None

    with patch.object(env.routes, '_telegram_get_chat_member', side_effect=get_member), \
            patch.object(
                env.routes, '_set_chat_member_tag', return_value=(True, None)
            ) as set_tag:
        ok = _apply(env, user)

    assert ok is True
    set_tag.assert_called_once()
    user = _reload(env, user)
    assert user.applied_telegram_tag == '上榜老师'
    assert user.telegram_tag_skip is None


def test_remembered_left_user_stays_quiet_if_still_gone(tag_env, capsys):
    env = tag_env
    user = _add_user(env, LEFT_TG_ID, skip='left')

    def get_member(_chat_id, user_id, _token):
        if int(user_id) == BOT_ID:
            return {'status': 'administrator', 'can_manage_tags': True}, None
        return None, 'Bad Request: PARTICIPANT_ID_INVALID'

    with patch.object(env.routes, '_telegram_get_chat_member', side_effect=get_member), \
            patch.object(env.routes, '_set_chat_member_tag') as set_tag:
        ok = _apply(env, user)

    assert ok is False
    set_tag.assert_not_called()
    out = capsys.readouterr().out
    assert 'skip left' not in out
    assert '❌' not in out


def test_bot_lacks_can_manage_tags_logs_once_and_does_not_skip_users(tag_env, capsys):
    env = tag_env
    user_a = _add_user(env, MEMBER_TG_ID)
    user_b = _add_user(env, MEMBER_TG_ID + 1, tags='["靠谱认证老师"]')

    def get_member(_chat_id, user_id, _token):
        if int(user_id) == BOT_ID:
            return {'status': 'administrator', 'can_manage_tags': False}, None
        raise AssertionError('should not look up members when bot cannot manage tags')

    with patch.object(env.routes, '_telegram_get_chat_member', side_effect=get_member), \
            patch.object(env.routes, '_set_chat_member_tag') as set_tag:
        assert _apply(env, user_a) is False
        assert _apply(env, user_b) is False

    set_tag.assert_not_called()
    assert _reload(env, user_a).telegram_tag_skip is None
    assert _reload(env, user_b).telegram_tag_skip is None
    logs = [line for line in capsys.readouterr().out.splitlines() if 'can_manage_tags' in line]
    assert len(logs) == 1
    assert f'chat={CHAT_ID}' in logs[0]


def test_set_chat_member_tag_rights_error_logs_once(tag_env, capsys):
    env = tag_env
    user = _add_user(env, MEMBER_TG_ID)

    def get_member(_chat_id, user_id, _token):
        if int(user_id) == BOT_ID:
            # Permission lookup inconclusive; the set call reports the real error.
            return None, 'timeout'
        return {'status': 'member'}, None

    with patch.object(env.routes, '_telegram_get_chat_member', side_effect=get_member), \
            patch.object(
                env.routes,
                '_set_chat_member_tag',
                return_value=(False, 'Bad Request: not enough rights to manage tags'),
            ):
        assert _apply(env, user) is False
        # Second attempt should be cached as lacking permission and stay quiet.
        assert _apply(env, user) is False

    user = _reload(env, user)
    assert user.telegram_tag_skip is None
    out = capsys.readouterr().out
    assert out.count('bot lacks can_manage_tags') == 1
    assert '❌' not in out


def test_bot_user_id_from_token_and_error_match():
    from app.modules.core.routes import (
        _bot_user_id_from_token,
        _telegram_error_matches,
    )

    assert _bot_user_id_from_token('123:secret') == 123
    assert _bot_user_id_from_token(None) is None
    assert _bot_user_id_from_token('not-a-token') is None
    assert _telegram_error_matches('Bad Request: CHAT_CREATOR_REQUIRED', 'CHAT_CREATOR_REQUIRED')
    assert not _telegram_error_matches('ok', 'CHAT_CREATOR_REQUIRED')


def test_set_chat_member_tag_does_not_print_expected_admin_error(tag_env, capsys, monkeypatch):
    env = tag_env

    class _Resp:
        ok = False
        status_code = 400
        text = '{"ok":false,"description":"Bad Request: CHAT_CREATOR_REQUIRED"}'

        def json(self):
            return {'ok': False, 'description': 'Bad Request: CHAT_CREATOR_REQUIRED'}

    monkeypatch.setattr(env.routes.requests, 'post', lambda *a, **k: _Resp())
    ok, err = env.routes._set_chat_member_tag(CHAT_ID, ADMIN_TG_ID, '上榜老师', token=BOT_TOKEN)
    assert ok is False
    assert 'CHAT_CREATOR_REQUIRED' in err
    assert '❌' not in capsys.readouterr().out
    # Public wrapper still returns a bool.
    assert env.routes.do_set_member_tag(CHAT_ID, ADMIN_TG_ID, '上榜老师', token=BOT_TOKEN) is False
