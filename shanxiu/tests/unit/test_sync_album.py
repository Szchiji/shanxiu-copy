"""Album / media_group sync: buffer parts and flush as one sendMediaGroup / copy_messages."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _photo_msg(message_id, media_group_id, caption=None, file_id='ph'):
    return SimpleNamespace(
        text=None,
        caption=caption,
        caption_html=None,
        caption_entities=None,
        message_id=message_id,
        media_group_id=media_group_id,
        photo=[SimpleNamespace(file_id=file_id)],
        video=None, document=None, audio=None, voice=None,
        video_note=None, sticker=None, animation=None, poll=None,
        location=None, contact=None, venue=None,
        forward_origin=None,
    )


class TestDeliverSyncMediaGroup:
    def test_copy_messages_when_no_prefix(self):
        from app.modules.core import routes

        async def _run():
            bot = AsyncMock()
            bot.copy_messages = AsyncMock(return_value=[SimpleNamespace(message_id=501)])
            msgs = [_photo_msg(1, 'mg1'), _photo_msg(2, 'mg1', caption='cap')]
            sent, mtype = await routes._deliver_sync_media_group(
                bot, '-100T', msgs, '', from_chat_id=-100555
            )
            assert mtype == 'media_group'
            assert sent.message_id == 501
            bot.copy_messages.assert_awaited()
            bot.send_media_group.assert_not_awaited()

        asyncio.get_event_loop().run_until_complete(_run())

    def test_send_media_group_with_html_prefix(self):
        from app.modules.core import routes

        async def _run():
            bot = AsyncMock()
            bot.send_media_group = AsyncMock(
                return_value=[SimpleNamespace(message_id=601), SimpleNamespace(message_id=602)]
            )
            msgs = [
                _photo_msg(1, 'mg2', file_id='a'),
                _photo_msg(2, 'mg2', caption='hello', file_id='b'),
            ]
            prefix = '<a href="tg://user?id=1">U</a>\n'
            sent, mtype = await routes._deliver_sync_media_group(
                bot, '-100T', msgs, prefix, from_chat_id=-100555
            )
            assert mtype == 'media_group'
            assert sent.message_id == 601
            bot.send_media_group.assert_awaited()
            media = bot.send_media_group.await_args.kwargs['media']
            assert len(media) == 2
            # Header goes on the caption-bearing item (index 1), name on its own line
            assert media[0].caption is None
            assert media[1].caption == '<a href="tg://user?id=1">U</a>\nhello'
            assert media[1].parse_mode == 'HTML'

        asyncio.get_event_loop().run_until_complete(_run())


class TestEnqueueAlbumBuffer:
    def test_handler_buffers_album_instead_of_sending_immediately(self, flask_app):
        from app import db
        from app.models import BotGroup, SyncGroupMessages
        from app.modules.core import routes

        with flask_app.app_context():
            group = BotGroup(chat_id='-100851', title='src', type='supergroup', is_active=True)
            db.session.add(group)
            db.session.commit()
            gid = group.id
            db.session.add(SyncGroupMessages(
                source_group_id=gid,
                target_group_id='-100852',
                enabled=True,
                sync_media=True,
                sync_forwards=True,
                filter_keywords='[]',
            ))
            db.session.commit()

            routes.global_flask_app = flask_app
            routes._SYNC_MEDIA_GROUP_BUFFERS.clear()

            bot = AsyncMock()
            bot.copy_messages = AsyncMock(return_value=[SimpleNamespace(message_id=1)])
            bot.send_media_group = AsyncMock(return_value=[SimpleNamespace(message_id=1)])
            bot.copy_message = AsyncMock()
            bot.send_photo = AsyncMock()

            user = SimpleNamespace(id=9, username='u', first_name='U', last_name='', is_bot=False)
            chat = SimpleNamespace(id=-100851, type='supergroup')

            async def _feed():
                for mid, cap in ((10, None), (11, 'album cap')):
                    msg = _photo_msg(mid, 'album-xyz', caption=cap, file_id=f'f{mid}')
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
                    await routes.handle_sync_group_messages(update, context)

            asyncio.get_event_loop().run_until_complete(_feed())

            # Still buffered — not sent yet
            bot.copy_messages.assert_not_awaited()
            bot.send_photo.assert_not_awaited()
            assert len(routes._SYNC_MEDIA_GROUP_BUFFERS) == 1
            key = next(iter(routes._SYNC_MEDIA_GROUP_BUFFERS))
            buf = routes._SYNC_MEDIA_GROUP_BUFFERS[key]
            assert len(buf['messages']) == 2

            # Force flush
            async def _flush():
                await routes._flush_sync_media_group(key)

            asyncio.get_event_loop().run_until_complete(_flush())
            bot.copy_messages.assert_awaited()
            ids = bot.copy_messages.await_args.kwargs['message_ids']
            assert ids == [10, 11]
            assert key not in routes._SYNC_MEDIA_GROUP_BUFFERS
