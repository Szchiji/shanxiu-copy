"""Sender header styles for sync: newline (name on its own line) and forward (native)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.sync_service import (
    album_prefix_index,
    combine_prefix_html,
    html_visible_len,
    normalize_prefix_style,
    prefix_fits,
    utf16_len,
)

PREFIX = '<a href="tg://user?id=42">Alice</a>\n'


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _msg(mid=7, text=None, caption=None, photo=False, sticker=False, mgid=None, **extra):
    base = dict(
        message_id=mid, text=text, caption=caption, media_group_id=mgid,
        text_html=None, entities=None, caption_html=None, caption_entities=None,
        photo=[SimpleNamespace(file_id=f'p{mid}')] if photo else None,
        video=None, document=None, audio=None, voice=None, video_note=None,
        sticker=SimpleNamespace(file_id='s') if sticker else None,
        animation=None, poll=None, location=None, contact=None, venue=None,
        forward_origin=None, chat_id=-100555,
    )
    base.update(extra)
    return SimpleNamespace(**base)


class TestHelpers:
    def test_normalize(self):
        assert normalize_prefix_style(None) == 'newline'
        assert normalize_prefix_style('FORWARD') == 'forward'
        assert normalize_prefix_style('weird') == 'newline'

    def test_lengths(self):
        assert utf16_len('😀a') == 3
        assert html_visible_len(PREFIX) == len('Alice\n')
        assert html_visible_len('<b>a&amp;b</b>') == 3
        assert prefix_fits(PREFIX, 'x' * (1024 - 6), 1024) is True
        assert prefix_fits(PREFIX, 'x' * (1024 - 5), 1024) is False

    def test_combine(self):
        assert combine_prefix_html(PREFIX, '<b>hi</b>') == PREFIX + '<b>hi</b>'
        assert combine_prefix_html(PREFIX, '') == '<a href="tg://user?id=42">Alice</a>'

    def test_album_prefix_index(self):
        assert album_prefix_index([_msg(1), _msg(2, caption='c'), _msg(3, caption='d')]) == 1
        assert album_prefix_index([_msg(1), _msg(2)]) == 0


class TestSingleDeliveryNewline:
    def test_text_html_preserved_under_name_line(self):
        from app.modules.core import routes
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
        m = _msg(text='hi there', text_html='<b>hi</b> there')
        sent, mtype = _run(routes._deliver_sync_copy(bot, '-1T', m, PREFIX, True, from_chat_id=-100555))
        assert mtype == 'text'
        kw = bot.send_message.await_args.kwargs
        assert kw['text'] == PREFIX + '<b>hi</b> there' and kw['parse_mode'] == 'HTML'
        bot.copy_message.assert_not_awaited()

    def test_long_text_header_separate_then_exact_copy(self):
        from app.modules.core import routes
        bot = AsyncMock()
        m = _msg(text='x' * 4095)
        _run(routes._deliver_sync_copy(bot, '-1T', m, PREFIX, True, from_chat_id=-100555))
        assert bot.send_message.await_args.kwargs['text'] == '<a href="tg://user?id=42">Alice</a>'
        bot.copy_message.assert_awaited_once()
        assert 'caption' not in bot.copy_message.await_args.kwargs

    def test_photo_caption_via_copy_override(self):
        from app.modules.core import routes
        bot = AsyncMock()
        m = _msg(photo=True, caption='cap', caption_html='<i>cap</i>')
        _run(routes._deliver_sync_copy(bot, '-1T', m, PREFIX, True, from_chat_id=-100555))
        kw = bot.copy_message.await_args.kwargs
        assert kw['caption'] == PREFIX + '<i>cap</i>' and kw['parse_mode'] == 'HTML'
        bot.send_message.assert_not_awaited()

    def test_photo_without_caption_gets_name_only(self):
        from app.modules.core import routes
        bot = AsyncMock()
        _run(routes._deliver_sync_copy(bot, '-1T', _msg(photo=True), PREFIX, True, from_chat_id=-100555))
        assert bot.copy_message.await_args.kwargs['caption'] == '<a href="tg://user?id=42">Alice</a>'

    def test_long_caption_header_separate(self):
        from app.modules.core import routes
        bot = AsyncMock()
        m = _msg(photo=True, caption='y' * 1020)
        _run(routes._deliver_sync_copy(bot, '-1T', m, PREFIX, True, from_chat_id=-100555))
        bot.send_message.assert_awaited_once()
        assert 'caption' not in bot.copy_message.await_args.kwargs

    def test_sticker_header_then_copy(self):
        from app.modules.core import routes
        bot = AsyncMock()
        _run(routes._deliver_sync_copy(bot, '-1T', _msg(sticker=True), PREFIX, True, from_chat_id=-100555))
        bot.send_message.assert_awaited_once()
        bot.copy_message.assert_awaited_once()


class TestForwardStyle:
    def test_single_forward(self):
        from app.modules.core import routes
        bot = AsyncMock()
        bot.forward_message = AsyncMock(return_value=SimpleNamespace(message_id=9))
        sent, _ = _run(routes._deliver_sync_copy(
            bot, '-1T', _msg(text='hi'), PREFIX, True, from_chat_id=-100555, prefix_style='forward'))
        assert sent.message_id == 9
        bot.forward_message.assert_awaited_once()
        bot.send_message.assert_not_awaited()
        bot.copy_message.assert_not_awaited()

    def test_forward_failure_falls_back_to_newline(self):
        from app.modules.core import routes
        bot = AsyncMock()
        bot.forward_message = AsyncMock(side_effect=RuntimeError('protected'))
        _run(routes._deliver_sync_copy(
            bot, '-1T', _msg(text='hi'), PREFIX, True, from_chat_id=-100555, prefix_style='forward'))
        assert bot.send_message.await_args.kwargs['text'] == PREFIX + 'hi'

    def test_no_prefix_ignores_forward_style(self):
        from app.modules.core import routes
        bot = AsyncMock()
        _run(routes._deliver_sync_copy(
            bot, '-1T', _msg(text='hi'), '', True, from_chat_id=-100555, prefix_style='forward'))
        bot.forward_message.assert_not_awaited()
        bot.copy_message.assert_awaited_once()

    def test_album_forward_messages(self):
        from app.modules.core import routes
        bot = AsyncMock()
        bot.forward_messages = AsyncMock(return_value=(SimpleNamespace(message_id=50),))
        msgs = [_msg(2, photo=True, mgid='g'), _msg(1, photo=True, mgid='g', caption='c')]
        sent, mtype = _run(routes._deliver_sync_media_group(bot, '-1T', msgs, PREFIX, -100555, 'forward'))
        assert mtype == 'media_group' and sent.message_id == 50
        assert bot.forward_messages.await_args.kwargs['message_ids'] == [1, 2]
        bot.send_media_group.assert_not_awaited()


class TestAlbumNewline:
    def test_header_on_first_item_when_no_caption(self):
        from app.modules.core import routes
        bot = AsyncMock()
        bot.send_media_group = AsyncMock(return_value=[SimpleNamespace(message_id=1)])
        msgs = [_msg(1, photo=True, mgid='g'), _msg(2, photo=True, mgid='g')]
        _run(routes._deliver_sync_media_group(bot, '-1T', msgs, PREFIX, -100555))
        media = bot.send_media_group.await_args.kwargs['media']
        assert media[0].caption == '<a href="tg://user?id=42">Alice</a>'
        assert media[1].caption is None

    def test_keeps_other_captions(self):
        from app.modules.core import routes
        bot = AsyncMock()
        bot.send_media_group = AsyncMock(return_value=[SimpleNamespace(message_id=1)])
        msgs = [_msg(1, photo=True, mgid='g', caption='a'), _msg(2, photo=True, mgid='g', caption='b')]
        _run(routes._deliver_sync_media_group(bot, '-1T', msgs, PREFIX, -100555))
        media = bot.send_media_group.await_args.kwargs['media']
        assert media[0].caption == PREFIX + 'a'
        assert media[1].caption == 'b'

    def test_long_album_caption_header_separate_then_copy(self):
        from app.modules.core import routes
        bot = AsyncMock()
        bot.copy_messages = AsyncMock(return_value=(SimpleNamespace(message_id=5),))
        msgs = [_msg(1, photo=True, mgid='g', caption='z' * 1020), _msg(2, photo=True, mgid='g')]
        _run(routes._deliver_sync_media_group(bot, '-1T', msgs, PREFIX, -100555))
        bot.send_message.assert_awaited_once()
        bot.copy_messages.assert_awaited_once()
        bot.send_media_group.assert_not_awaited()


class TestSaveStyle:
    def test_save_and_add_target_persist_style(self, flask_app, client):
        from app import db
        from app.models import BotGroup, SyncGroupMessages

        with flask_app.app_context():
            g = BotGroup(chat_id='-100611', title='g', type='supergroup', is_active=True)
            db.session.add(g)
            db.session.commit()
            gid = g.id
            db.session.add(SyncGroupMessages(source_group_id=gid, target_group_id='-100612', enabled=True))
            db.session.commit()

        with client.session_transaction() as sess:
            sess['logged_in'] = True

        r = client.post('/core/api/save_sync_group_messages', json={
            'group_id': gid, 'enabled': True, 'include_sender_prefix': True,
            'sender_prefix_style': 'forward', 'filter_keywords': '[]',
        })
        assert (r.get_json() or {}).get('status') == 'ok'
        with flask_app.app_context():
            row = SyncGroupMessages.query.filter_by(source_group_id=gid).first()
            assert row.include_sender_prefix is True and row.sender_prefix_style == 'forward'

        # Saving without the field keeps the stored style
        client.post('/core/api/save_sync_group_messages', json={
            'group_id': gid, 'enabled': True, 'include_sender_prefix': True, 'filter_keywords': '[]',
        })
        with flask_app.app_context():
            assert SyncGroupMessages.query.filter_by(source_group_id=gid).first().sender_prefix_style == 'forward'

    def test_handler_uses_forward_style(self, flask_app):
        from app import db
        from app.models import BotGroup, SyncGroupMessages
        from app.modules.core import routes

        with flask_app.app_context():
            g = BotGroup(chat_id='-100621', title='g', type='supergroup', is_active=True)
            db.session.add(g)
            db.session.commit()
            db.session.add(SyncGroupMessages(
                source_group_id=g.id, target_group_id='-100622', enabled=True, sync_media=True,
                sync_forwards=True, include_sender_prefix=True, sender_prefix_style='forward',
                filter_keywords='[]',
            ))
            db.session.commit()

        routes.global_flask_app = flask_app
        bot = AsyncMock()
        bot.forward_message = AsyncMock(return_value=SimpleNamespace(message_id=77))
        user = SimpleNamespace(id=42, username='a', first_name='Alice', last_name='', is_bot=False)
        chat = SimpleNamespace(id=-100621, type='supergroup')
        m = _msg(mid=3, text='hello')
        update = SimpleNamespace(effective_message=m, effective_chat=chat, effective_user=user, channel_post=None)
        context = SimpleNamespace(bot=bot, application=SimpleNamespace(bot_data={'clone_id': None}))
        _run(routes.handle_sync_group_messages(update, context))
        bot.forward_message.assert_awaited_once()
        assert bot.forward_message.await_args.kwargs['message_id'] == 3

    def test_migration_lists_column(self):
        from pathlib import Path
        assert 'sender_prefix_style' in Path('migrate_database.py').read_text()
