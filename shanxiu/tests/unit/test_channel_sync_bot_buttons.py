"""Channel sync: other bots' posts keep media + inline buttons; only own bot is skipped."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestSkipOwnBotOnly:  # helper kept; channel sync now uses the precise loop guard
    def test_should_skip_own_bot_sender(self):
        from app.services.sync_service import should_skip_bot_sender, should_skip_own_bot_sender

        other = SimpleNamespace(id=111, is_bot=True)
        human = SimpleNamespace(id=222, is_bot=False)
        own = SimpleNamespace(id=999, is_bot=True)

        # Group helper still skips every bot
        assert should_skip_bot_sender(other) is True
        assert should_skip_bot_sender(human) is False

        # Channel helper only skips our bot id
        assert should_skip_own_bot_sender(other, 999) is False
        assert should_skip_own_bot_sender(own, 999) is True
        assert should_skip_own_bot_sender(human, 999) is False
        assert should_skip_own_bot_sender(None, 999) is False
        assert should_skip_own_bot_sender(own, None) is False


class TestDeliverSyncCopyKeepsButtons:
    def test_copy_message_passes_reply_markup(self):
        from app.modules.core import routes

        markup = InlineKeyboardMarkup([[InlineKeyboardButton('去看看', url='https://t.me/example')]])
        bot = AsyncMock()
        bot.copy_message = AsyncMock(return_value=SimpleNamespace(message_id=10))

        photo = [SimpleNamespace(file_id='ph1')]
        msg = SimpleNamespace(
            text=None, caption='cap', caption_html='cap', caption_entities=None,
            message_id=7, photo=photo, video=None, document=None, audio=None,
            voice=None, video_note=None, sticker=None, animation=None, poll=None,
            location=None, contact=None, venue=None, media_group_id=None,
            reply_markup=markup,
        )
        sent, mtype = _run(routes._deliver_sync_copy(
            bot, '-1002', msg, '', True, from_chat_id=-1001,
        ))
        assert mtype == 'photo'
        assert sent.message_id == 10
        kw = bot.copy_message.await_args.kwargs
        assert kw['reply_markup'] is markup
        assert kw['message_id'] == 7

    def test_rebuild_photo_passes_reply_markup(self):
        from app.modules.core import routes

        markup = InlineKeyboardMarkup([[InlineKeyboardButton('链接', url='https://example.com')]])
        bot = AsyncMock()
        bot.copy_message = AsyncMock(side_effect=Exception('copy denied'))
        bot.send_photo = AsyncMock(return_value=SimpleNamespace(message_id=11))

        photo = [SimpleNamespace(file_id='ph2')]
        msg = SimpleNamespace(
            text=None, caption='hi', caption_html='hi', caption_entities=None,
            message_id=8, photo=photo, video=None, document=None, audio=None,
            voice=None, video_note=None, sticker=None, animation=None, poll=None,
            location=None, contact=None, venue=None, media_group_id=None,
            reply_markup=markup,
        )
        sent, mtype = _run(routes._deliver_sync_copy(
            bot, '-1002', msg, '', True, from_chat_id=-1001,
        ))
        assert mtype == 'photo'
        assert sent.message_id == 11
        assert bot.send_photo.await_args.kwargs['reply_markup'] is markup
        assert bot.send_photo.await_args.kwargs['photo'] == 'ph2'


class TestChannelSyncOtherBotMediaButtons:
    def test_other_bot_channel_post_with_photo_and_buttons_syncs(self, flask_app):
        from app import db
        from app.models import BotGroup, SyncGroupMessages, SyncMessageLog
        from app.modules.core import routes

        markup = InlineKeyboardMarkup([[InlineKeyboardButton('领取', url='https://t.me/shop')]])

        with flask_app.app_context():
            group = BotGroup(chat_id='-1008801', title='src-ch', type='channel', is_active=True)
            db.session.add(group)
            db.session.commit()
            gid = group.id
            db.session.add(SyncGroupMessages(
                source_group_id=gid,
                target_group_id='-1008802',
                enabled=True,
                sync_media=True,
                sync_forwards=True,
                filter_keywords='[]',
            ))
            db.session.commit()

            routes.global_flask_app = flask_app

            sent = SimpleNamespace(message_id=88)
            bot = AsyncMock()
            bot.id = 555001  # our bot
            bot.copy_message = AsyncMock(return_value=sent)
            bot.send_message = AsyncMock()

            # Other bot authored the channel post (signed)
            other_bot = SimpleNamespace(id=777888, is_bot=True, username='other_bot')
            chat = SimpleNamespace(id=-1008801, type='channel', username='srcch')
            photo = [SimpleNamespace(file_id='FILE_OTHER')]
            msg = SimpleNamespace(
                text=None,
                caption='其他机器人发的图',
                caption_html='其他机器人发的图',
                caption_entities=None,
                message_id=42,
                photo=photo,
                video=None, document=None, audio=None, voice=None,
                video_note=None, sticker=None, animation=None, poll=None,
                location=None, contact=None, venue=None,
                forward_origin=None,
                media_group_id=None,
                reply_markup=markup,
            )
            update = SimpleNamespace(
                channel_post=msg,
                edited_channel_post=None,
                edited_message=None,
                effective_message=msg,
                effective_chat=chat,
                effective_user=other_bot,
            )
            context = SimpleNamespace(
                bot=bot,
                application=SimpleNamespace(bot_data={'clone_id': None}),
                job_queue=MagicMock(),
            )

            _run(routes.handle_sync_channel_posts(update, context))

            bot.copy_message.assert_awaited()
            kw = bot.copy_message.await_args.kwargs
            assert kw['message_id'] == 42
            assert kw['chat_id'] == '-1008802'
            assert kw['reply_markup'] is markup
            logs = SyncMessageLog.query.filter_by(source_group_id=gid, status='success').all()
            assert len(logs) == 1
            assert logs[0].message_type == 'photo'

    def test_own_bot_channel_post_synced_but_sync_output_skipped(self, flask_app):
        from app import db
        from app.models import BotGroup, SyncGroupMessages, SyncMessageLog
        from app.modules.core import routes

        with flask_app.app_context():
            group = BotGroup(chat_id='-1008811', title='src-ch2', type='channel', is_active=True)
            db.session.add(group)
            db.session.commit()
            gid = group.id
            db.session.add(SyncGroupMessages(
                source_group_id=gid,
                target_group_id='-1008812',
                enabled=True,
                sync_media=True,
                sync_forwards=True,
                filter_keywords='[]',
            ))
            db.session.commit()

            routes.global_flask_app = flask_app

            bot = AsyncMock()
            bot.id = 555001
            bot.copy_message = AsyncMock(return_value=SimpleNamespace(message_id=301))

            own = SimpleNamespace(id=555001, is_bot=True, username='our_bot')
            chat = SimpleNamespace(id=-1008811, type='channel', username='srcch2')
            msg = SimpleNamespace(
                text='我们自己发的',
                caption=None,
                message_id=9,
                photo=None, video=None, document=None, audio=None, voice=None,
                video_note=None, sticker=None, animation=None, poll=None,
                location=None, contact=None, venue=None,
                forward_origin=None,
                media_group_id=None,
                reply_markup=None,
            )
            update = SimpleNamespace(
                channel_post=msg,
                edited_channel_post=None,
                edited_message=None,
                effective_message=msg,
                effective_chat=chat,
                effective_user=own,
            )
            context = SimpleNamespace(
                bot=bot,
                application=SimpleNamespace(bot_data={'clone_id': None}),
                job_queue=MagicMock(),
            )

            from app.services import sync_loop_guard as g
            g.reset()

            # 1) A normal post by OUR bot (e.g. scheduled message) is synced now.
            _run(routes.handle_sync_channel_posts(update, context))
            bot.copy_message.assert_awaited_once()
            assert SyncMessageLog.query.filter_by(source_group_id=gid, status='success').count() == 1
            assert g.is_sync_output_memory(-1008812, 301)

            # 2) A post that is itself a sync output in this channel is NOT re-synced (no loop).
            g.mark_sync_output(-1008811, 10)
            msg.message_id = 10
            _run(routes.handle_sync_channel_posts(update, context))
            bot.copy_message.assert_awaited_once()
            g.reset()




class TestAlbumReattachButtons:
    def test_copy_messages_album_reattaches_markup(self):
        from app.modules.core import routes

        markup = InlineKeyboardMarkup([[InlineKeyboardButton('相册按钮', url='https://t.me/x')]])
        bot = AsyncMock()
        copied = [SimpleNamespace(message_id=501), SimpleNamespace(message_id=502)]
        bot.copy_messages = AsyncMock(return_value=copied)
        bot.edit_message_reply_markup = AsyncMock()

        m1 = SimpleNamespace(
            message_id=1, caption=None, caption_html=None, caption_entities=None,
            photo=[SimpleNamespace(file_id='a')], video=None, document=None, audio=None,
            reply_markup=None, media_group_id='mg1',
        )
        m2 = SimpleNamespace(
            message_id=2, caption='with btn', caption_html='with btn', caption_entities=None,
            photo=[SimpleNamespace(file_id='b')], video=None, document=None, audio=None,
            reply_markup=markup, media_group_id='mg1',
        )
        sent, mtype = _run(routes._deliver_sync_media_group(
            bot, '-1009', [m1, m2], '', -1008,
        ))
        assert mtype == 'media_group'
        assert sent.message_id == 501
        bot.copy_messages.assert_awaited()
        bot.edit_message_reply_markup.assert_awaited()
        edit_kw = bot.edit_message_reply_markup.await_args.kwargs
        assert edit_kw['message_id'] == 502
        assert edit_kw['reply_markup'] is markup
