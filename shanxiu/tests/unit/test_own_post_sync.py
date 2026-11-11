"""Own-bot channel posts: Telegram never echoes them as updates → send hook feeds channel sync."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import own_post_sync as ops
from app.services.sync_loop_guard import IN_SYNC_DELIVERY, mark_sync_output, reset, track_bot


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ops._bots.clear()
    ops.invalidate()
    reset()
    monkeypatch.setattr(ops, 'SETTLE_DELAY', 0)
    yield
    ops._bots.clear()


class TestShouldHandle:
    def test_only_registered_bot_and_own_sync_channels(self, monkeypatch):
        bot, other = object(), object()
        ops._bots[id(bot)] = None
        monkeypatch.setattr(ops, 'own_sync_sources', lambda clone_id, flask_app=None: frozenset({'-100555'}))
        assert ops.should_handle(bot, 'send_message', (), {'chat_id': -100555}) == (None, '-100555')
        assert ops.should_handle(bot, 'send_message', ('-100555',), {}) == (None, '-100555')
        assert ops.should_handle(other, 'send_message', (), {'chat_id': -100555}) is None
        assert ops.should_handle(bot, 'send_message', (), {'chat_id': -100999}) is None  # not a source
        assert ops.should_handle(bot, 'send_message', (), {'chat_id': 12345}) is None    # private chat

    def test_sync_delivery_is_ignored(self, monkeypatch):
        bot = object()
        ops._bots[id(bot)] = None
        monkeypatch.setattr(ops, 'own_sync_sources', lambda clone_id, flask_app=None: frozenset({'-100555'}))
        tok = IN_SYNC_DELIVERY.set(True)
        try:
            assert ops.should_handle(bot, 'send_message', (), {'chat_id': -100555}) is None
        finally:
            IN_SYNC_DELIVERY.reset(tok)

    def test_tracking_bot_sets_flag_during_send(self):
        seen = {}

        class B:
            async def send_message(self, **kw):
                seen['flag'] = IN_SYNC_DELIVERY.get()
                return SimpleNamespace(message_id=1)

        _run(track_bot(B()).send_message(chat_id=-1001, text='x'))
        assert seen['flag'] is True and IN_SYNC_DELIVERY.get() is False


class TestWrapper:
    def test_wrapper_schedules_process_with_result(self, monkeypatch):
        calls = []

        async def fake_process(bot, clone_id, chat_id, method, kwargs, result):
            calls.append((clone_id, chat_id, method, result))

        monkeypatch.setattr(ops, '_process', fake_process)
        monkeypatch.setattr(ops, 'own_sync_sources', lambda clone_id, flask_app=None: frozenset({'-100555'}))

        async def orig(self, *a, **kw):
            return 'MSG'

        wrapped = ops._wrap('send_photo', orig)
        bot = SimpleNamespace()
        ops._bots[id(bot)] = 7

        async def go():
            r = await wrapped(bot, chat_id=-100555, photo='x')
            await asyncio.sleep(0)
            return r

        assert _run(go()) == 'MSG'
        assert calls == [(7, '-100555', 'send_photo', 'MSG')]

    def test_install_patches_extbot_once(self):
        from telegram.ext import ExtBot
        ops.install()
        ops.install()
        assert getattr(ExtBot.send_message, '__wrapped__', None) is not None
        assert getattr(ExtBot.send_message.__wrapped__, '__wrapped__', None) is None
        assert getattr(ExtBot.send_media_group, '__wrapped__', None) is not None


class TestCoreAndToggle:
    def _setup(self, flask_app, own_flags):
        from app import db
        from app.models import BotGroup, SyncGroupMessages
        with flask_app.app_context():
            g = BotGroup(chat_id='-1007770001', title='src', type='channel', is_active=True)
            db.session.add(g)
            db.session.commit()
            for i, flag in enumerate(own_flags):
                db.session.add(SyncGroupMessages(source_group_id=g.id, target_group_id=f'-10088800{i}',
                                                 enabled=True, sync_own_bot_posts=flag))
            db.session.commit()
            return g.id

    def test_sources_respect_toggle(self, flask_app):
        self._setup(flask_app, [False])
        assert '-1007770001' not in ops.own_sync_sources(None, flask_app)
        from app import db
        from app.models import SyncGroupMessages
        with flask_app.app_context():
            SyncGroupMessages.query.filter_by(target_group_id='-100888000').first().sync_own_bot_posts = True
            db.session.commit()
        ops.invalidate()
        assert '-1007770001' in ops.own_sync_sources(None, flask_app)

    def test_own_post_synced_only_to_enabled_targets_and_skips_sync_outputs(self, flask_app, monkeypatch):
        from app.modules.core import routes
        self._setup(flask_app, [True, False])
        monkeypatch.setattr(routes, 'global_flask_app', flask_app)
        delivered = []

        async def fake_deliver(bot, target, msg, prefix, sync_media, from_chat_id=None, prefix_style='newline'):
            delivered.append((target, msg.message_id))
            return SimpleNamespace(message_id=99), 'text'

        side = AsyncMock()
        monkeypatch.setattr(routes, '_deliver_sync_copy', fake_deliver)
        monkeypatch.setattr(routes, '_maybe_send_channel_auto_reply', side)
        monkeypatch.setattr(routes, '_maybe_attach_channel_post_buttons', side)
        chat = SimpleNamespace(id=-1007770001, type='channel', title='src', username=None)
        msg = SimpleNamespace(message_id=5, chat=chat, text='定时帖', caption=None, media_group_id=None,
                              forward_origin=None, from_user=None, photo=None, video=None, document=None,
                              audio=None, animation=None)
        mark_sync_output(-1007770001, 6)  # a sync output in the same channel
        msg_out = SimpleNamespace(**{**msg.__dict__, 'message_id': 6})
        _run(ops._process(object(), None, '-1007770001', 'send_message', {}, [msg, msg_out]))
        assert delivered == [('-100888000', 5)]
        side.assert_not_awaited()  # auto-reply / auto-buttons not run for own posts


def test_channel_page_has_toggle(flask_app, client):
    from app import db
    from app.models import BotGroup
    with flask_app.app_context():
        g = BotGroup(chat_id='-1007770002', title='c2', type='channel', is_active=True)
        db.session.add(g)
        db.session.commit()
        gid = g.id
    with client.session_transaction() as s:
        s['logged_in'] = True
        s['login_user_id'] = 1
        s['login_user_name'] = 't'
    page = client.get(f'/core/group/{gid}/sync_channel_messages')
    assert page.status_code == 200, page.status_code
    body = page.get_data(as_text=True)
    assert 'id="sync_own_bot_posts"' in body and '同步主机器人发的帖子' in body
    r = client.post('/core/api/save_sync_group_messages', json={
        'group_id': gid, 'target_group_id': '-100999777', 'enabled': True, 'sync_own_bot_posts': False})
    assert r.get_json()['status'] == 'ok'
    from app.models import SyncGroupMessages
    with flask_app.app_context():
        assert SyncGroupMessages.query.filter_by(source_group_id=gid).first().sync_own_bot_posts is False


def test_supergroup_sources_are_ignored(flask_app):
    """Bug 2026-10-11: group -100… (type=group) was treated as a channel → own scheduled
    messages synced by the hook AND by the userbot rule = two copies in the target."""
    from app import db
    from app.models import BotGroup, SyncGroupMessages
    with flask_app.app_context():
        g = BotGroup(chat_id='-1007770009', title='grp', type='supergroup', is_active=True)
        db.session.add(g)
        db.session.commit()
        db.session.add(SyncGroupMessages(source_group_id=g.id, target_group_id='-100888999',
                                         enabled=True, sync_own_bot_posts=True))
        db.session.commit()
    ops.invalidate()
    assert '-1007770009' not in ops.own_sync_sources(None, flask_app)


def test_process_skips_non_channel_messages(monkeypatch):
    from app.modules.core import routes
    core = AsyncMock()
    monkeypatch.setattr(routes, '_sync_channel_post_core', core)
    grp = SimpleNamespace(id=-1007770009, type='supergroup')
    _run(ops._process(object(), None, '-1007770009', 'send_message', {},
                      SimpleNamespace(message_id=3, chat=grp)))
    core.assert_not_awaited()
