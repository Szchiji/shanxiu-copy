"""Channel forward rules: album (media_group) parts are buffered and sent as one group."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.channel_automation import album_filter_text, sorted_album_message_ids


def _album_part(mid, mgid='mg-1', caption=None):
    return SimpleNamespace(
        message_id=mid, media_group_id=mgid, text=None, caption=caption,
        photo=[SimpleNamespace(file_id=f'f{mid}')], video=None,
        sender_chat=SimpleNamespace(id=-100577, type='channel'),
        is_automatic_forward=True, forward_from_message_id=mid,
        delete=AsyncMock(),
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestAlbumHelpers:
    def test_album_filter_text_uses_first_caption(self):
        parts = [_album_part(3), _album_part(1, caption='promo sale'), _album_part(2, caption='x')]
        assert album_filter_text(parts) == 'promo sale'
        assert album_filter_text([_album_part(1)]) is None
        assert album_filter_text([]) is None

    def test_sorted_ids_dedup(self):
        parts = [_album_part(5), _album_part(3), _album_part(5)]
        assert sorted_album_message_ids(parts) == [3, 5]


class TestFlushForwardAlbum:
    def setup_method(self):
        from app.modules.core import routes
        routes._FORWARD_MEDIA_GROUP_BUFFERS.clear()

    def _fill(self, routes, bot, rules, parts):
        async def _go():
            for m in parts:
                routes._enqueue_forward_media_group_item(
                    bot=bot, clone_id=None, chat_id=-100222, msg=m, rules=rules,
                )
        _run(_go())
        return routes._forward_album_buffer_key(None, -100222, parts[0].media_group_id)

    def test_copy_mode_sends_one_copy_messages_call(self):
        from app.modules.core import routes
        bot = AsyncMock()
        rules = [{'rule_name': 'r', 'source_keywords': '', 'target_chat_id': '-100333',
                  'target_thread_id': 9, 'forward_mode': 'copy'}]
        key = self._fill(routes, bot, rules, [_album_part(11), _album_part(10, caption='c'), _album_part(12)])
        bot.copy_messages.assert_not_awaited()  # still debouncing
        _run(routes._flush_forward_media_group(key))
        bot.copy_messages.assert_awaited_once()
        kw = bot.copy_messages.await_args.kwargs
        assert kw['message_ids'] == [10, 11, 12]
        assert kw['chat_id'] == '-100333' and kw['from_chat_id'] == -100222
        assert kw['message_thread_id'] == 9
        bot.copy_message.assert_not_awaited()
        assert key not in routes._FORWARD_MEDIA_GROUP_BUFFERS

    def test_forward_mode_uses_forward_messages(self):
        from app.modules.core import routes
        bot = AsyncMock()
        rules = [{'rule_name': 'r', 'source_keywords': None, 'target_chat_id': '-1004',
                  'target_thread_id': None, 'forward_mode': 'forward'}]
        key = self._fill(routes, bot, rules, [_album_part(1), _album_part(2)])
        _run(routes._flush_forward_media_group(key))
        bot.forward_messages.assert_awaited_once()
        assert 'message_thread_id' not in bot.forward_messages.await_args.kwargs

    def test_keyword_rule_matches_on_album_caption_only(self):
        from app.modules.core import routes
        bot = AsyncMock()
        rules = [
            {'rule_name': 'hit', 'source_keywords': 'promo', 'target_chat_id': '-1001',
             'target_thread_id': None, 'forward_mode': 'copy'},
            {'rule_name': 'miss', 'source_keywords': 'nothing', 'target_chat_id': '-1002',
             'target_thread_id': None, 'forward_mode': 'copy'},
        ]
        key = self._fill(routes, bot, rules, [_album_part(1, caption='big PROMO'), _album_part(2), _album_part(3)])
        _run(routes._flush_forward_media_group(key))
        assert bot.copy_messages.await_count == 1
        assert bot.copy_messages.await_args.kwargs['chat_id'] == '-1001'
        assert bot.copy_messages.await_args.kwargs['message_ids'] == [1, 2, 3]

    def test_blocked_album_is_dropped(self):
        from app.modules.core import routes
        bot = AsyncMock()
        rules = [{'rule_name': 'r', 'source_keywords': '', 'target_chat_id': '-1001',
                  'target_thread_id': None, 'forward_mode': 'copy'}]
        key = self._fill(routes, bot, rules, [_album_part(2), _album_part(3)])
        async def _block():
            routes._enqueue_forward_media_group_item(
                bot=bot, clone_id=None, chat_id=-100222, msg=_album_part(1, caption='bad'),
                rules=rules, blocked=True,
            )
        _run(_block())
        _run(routes._flush_forward_media_group(key))
        bot.copy_messages.assert_not_awaited()

    def test_batch_failure_falls_back_per_message(self):
        from app.modules.core import routes
        bot = AsyncMock()
        bot.copy_messages = AsyncMock(side_effect=RuntimeError('old api'))
        rules = [{'rule_name': 'r', 'source_keywords': '', 'target_chat_id': '-1001',
                  'target_thread_id': None, 'forward_mode': 'copy'}]
        key = self._fill(routes, bot, rules, [_album_part(2), _album_part(1)])
        _run(routes._flush_forward_media_group(key))
        assert [c.kwargs['message_id'] for c in bot.copy_message.await_args_list] == [1, 2]


class TestHandleChannelPinAlbum:
    def test_album_parts_buffered_not_copied_individually(self, flask_app):
        from app import db
        from app.models import BotGroup, ChannelForwardRule
        from app.modules.core import routes

        with flask_app.app_context():
            ch = BotGroup(chat_id='-100577', title='ch', type='channel', is_active=True)
            disc = BotGroup(chat_id='-100578', title='disc', type='supergroup', is_active=True)
            db.session.add_all([ch, disc])
            db.session.commit()
            db.session.add(ChannelForwardRule(
                group_id=ch.id, rule_name='all', source_keywords='', target_chat_id='-100999',
                forward_mode='copy', is_active=True,
            ))
            db.session.commit()

        routes.global_flask_app = flask_app
        routes._FORWARD_MEDIA_GROUP_BUFFERS.clear()
        bot = AsyncMock()
        chat = SimpleNamespace(id=-100578, type='supergroup')
        context = SimpleNamespace(bot=bot, application=SimpleNamespace(bot_data={'clone_id': None}))

        async def _feed():
            for m in (_album_part(21, 'alb', caption='hi'), _album_part(22, 'alb')):
                update = SimpleNamespace(message=m, effective_message=m, effective_chat=chat)
                await routes.handle_channel_pin(update, context)
        _run(_feed())

        bot.copy_message.assert_not_awaited()
        key = routes._forward_album_buffer_key(None, -100578, 'alb')
        assert len(routes._FORWARD_MEDIA_GROUP_BUFFERS[key]['messages']) == 2
        _run(routes._flush_forward_media_group(key))
        bot.copy_messages.assert_awaited_once()
        assert bot.copy_messages.await_args.kwargs['message_ids'] == [21, 22]

    def test_single_message_still_copied_immediately(self, flask_app):
        from app import db
        from app.models import BotGroup, ChannelForwardRule
        from app.modules.core import routes

        with flask_app.app_context():
            ch = BotGroup(chat_id='-100587', title='ch', type='channel', is_active=True)
            disc = BotGroup(chat_id='-100588', title='disc', type='supergroup', is_active=True)
            db.session.add_all([ch, disc])
            db.session.commit()
            db.session.add(ChannelForwardRule(
                group_id=ch.id, rule_name='all', source_keywords='', target_chat_id='-100999',
                forward_mode='copy', is_active=True,
            ))
            db.session.commit()

        routes.global_flask_app = flask_app
        bot = AsyncMock()
        chat = SimpleNamespace(id=-100588, type='supergroup')
        m = _album_part(5, mgid=None, caption='solo')
        m.sender_chat = SimpleNamespace(id=-100587, type='channel')
        context = SimpleNamespace(bot=bot, application=SimpleNamespace(bot_data={'clone_id': None}))
        _run(routes.handle_channel_pin(SimpleNamespace(message=m, effective_message=m, effective_chat=chat), context))
        bot.copy_message.assert_awaited_once()
        assert bot.copy_message.await_args.kwargs['message_id'] == 5


class TestAlbumWindowAndLateParts:
    """Regression (prod 14:18 UTC+8): album parts arrived ~4.5s apart; 1.5s window split one off."""

    def test_default_window_covers_observed_gap(self):
        from app.modules.core import routes
        assert routes._FORWARD_MEDIA_GROUP_FLUSH_DELAY >= 5
        assert routes._SYNC_MEDIA_GROUP_FLUSH_DELAY >= 5

    def test_env_override(self, monkeypatch):
        from app.modules.core import routes
        monkeypatch.setenv('ALBUM_FLUSH_DELAY_SECONDS', '9')
        assert routes._album_flush_delay_default() == 9.0
        monkeypatch.setenv('ALBUM_FLUSH_DELAY_SECONDS', 'junk')
        assert routes._album_flush_delay_default() == 6.0

    def test_parts_4_5s_apart_stay_one_album(self):
        """Simulate prod timing: 1 part, 4.5s gap, 4 more parts → one copy_messages with 5 ids."""
        from app.modules.core import routes
        routes._FORWARD_MEDIA_GROUP_BUFFERS.clear()
        bot = AsyncMock()
        rules = [{'rule_name': 'r', 'source_keywords': '', 'target_chat_id': '-1001',
                  'target_thread_id': None, 'forward_mode': 'copy'}]

        async def _go():
            # shrink time: scale both window and gap by 1/10, keeping the same ratio as prod
            # (gap 4.5s vs window 6s)
            orig = routes._FORWARD_MEDIA_GROUP_FLUSH_DELAY
            routes._FORWARD_MEDIA_GROUP_FLUSH_DELAY = orig / 10
            try:
                routes._enqueue_forward_media_group_item(
                    bot=bot, clone_id=None, chat_id=-100222, msg=_album_part(7369, 'late'), rules=rules)
                await asyncio.sleep(0.45)
                for mid in (7370, 7371, 7372, 7373):
                    routes._enqueue_forward_media_group_item(
                        bot=bot, clone_id=None, chat_id=-100222, msg=_album_part(mid, 'late'), rules=rules)
                await asyncio.sleep(orig / 10 + 0.3)
            finally:
                routes._FORWARD_MEDIA_GROUP_FLUSH_DELAY = orig

        _run(_go())
        bot.copy_messages.assert_awaited_once()
        assert bot.copy_messages.await_args.kwargs['message_ids'] == [7369, 7370, 7371, 7372, 7373]
        bot.copy_message.assert_not_awaited()

    def test_full_album_flushes_immediately(self):
        from app.modules.core import routes
        routes._FORWARD_MEDIA_GROUP_BUFFERS.clear()
        bot = AsyncMock()
        rules = [{'rule_name': 'r', 'source_keywords': '', 'target_chat_id': '-1001',
                  'target_thread_id': None, 'forward_mode': 'copy'}]

        async def _go():
            for mid in range(1, 11):
                routes._enqueue_forward_media_group_item(
                    bot=bot, clone_id=None, chat_id=-100223, msg=_album_part(mid, 'full'), rules=rules)
            await asyncio.sleep(0.05)

        _run(_go())
        bot.copy_messages.assert_awaited_once()
        assert len(bot.copy_messages.await_args.kwargs['message_ids']) == 10

    def test_late_part_is_logged(self, capsys):
        from app.modules.core import routes
        routes._FORWARD_MEDIA_GROUP_BUFFERS.clear()
        routes._RECENT_ALBUM_FLUSHES.clear()
        bot = AsyncMock()
        rules = [{'rule_name': 'r', 'source_keywords': '', 'target_chat_id': '-1001',
                  'target_thread_id': None, 'forward_mode': 'copy'}]

        async def _go():
            routes._enqueue_forward_media_group_item(
                bot=bot, clone_id=None, chat_id=-100224, msg=_album_part(1, 'lp'), rules=rules)
            key = routes._forward_album_buffer_key(None, -100224, 'lp')
            await routes._flush_forward_media_group(key)
            routes._enqueue_forward_media_group_item(
                bot=bot, clone_id=None, chat_id=-100224, msg=_album_part(2, 'lp'), rules=rules)

        _run(_go())
        out = capsys.readouterr().out
        assert 'kind=forward' in out and 'flush items=1' in out
        assert 'LATE part msg=2' in out
        routes._FORWARD_MEDIA_GROUP_BUFFERS.clear()
