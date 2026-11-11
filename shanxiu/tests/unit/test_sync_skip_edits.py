"""
Regression (prod 2026-10-08 15:10–15:15 UTC+8): edits to OLD channel album posts arrived as
edited_channel_post updates, one item at a time ~5–14s apart, and each was copied to the
target again as a lone photo. Edits must never be re-synced (channel or group).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def _album_msg(mid, chat, caption=None):
    return SimpleNamespace(
        text=None, caption=caption, caption_html=caption, caption_entities=None,
        message_id=mid, chat=chat, chat_id=chat.id,
        photo=[SimpleNamespace(file_id=f'f{mid}')], video=None, document=None, audio=None,
        voice=None, video_note=None, sticker=None, animation=None, poll=None,
        location=None, contact=None, venue=None, forward_origin=None,
        media_group_id='edited-album-1', has_media_spoiler=False, show_caption_above_media=False,
    )


def _setup(flask_app, src, dst, ctype):
    from app import db
    from app.models import BotGroup, SyncGroupMessages
    from app.modules.core import routes
    group = BotGroup(chat_id=str(src), title='src', type=ctype, is_active=True)
    db.session.add(group)
    db.session.commit()
    db.session.add(SyncGroupMessages(
        source_group_id=group.id, target_group_id=str(dst), enabled=True,
        sync_media=True, sync_forwards=True, filter_keywords='[]',
    ))
    db.session.commit()
    routes.global_flask_app = flask_app
    return group.id


def _bot():
    bot = AsyncMock()
    for name in ('copy_message', 'copy_messages', 'send_message', 'send_photo',
                 'send_media_group', 'forward_messages', 'forward_message'):
        setattr(bot, name, AsyncMock(return_value=SimpleNamespace(message_id=1)))
    return bot


def _assert_nothing_sent(bot):
    for name in ('copy_message', 'copy_messages', 'send_message', 'send_photo',
                 'send_media_group', 'forward_messages', 'forward_message'):
        getattr(bot, name).assert_not_awaited()


def test_is_edit_update_helper():
    from app.modules.core import routes
    assert routes._is_edit_update(SimpleNamespace(edited_message=object(), edited_channel_post=None))
    assert routes._is_edit_update(SimpleNamespace(edited_message=None, edited_channel_post=object()))
    assert not routes._is_edit_update(SimpleNamespace(edited_message=None, edited_channel_post=None))
    assert not routes._is_edit_update(SimpleNamespace())


def test_edited_channel_album_item_not_resynced(flask_app, capsys):
    from app.modules.core import routes
    with flask_app.app_context():
        _setup(flask_app, -100631, -100632, 'channel')
        bot = _bot()
        chat = SimpleNamespace(id=-100631, type='channel', username='ch', title='ch')
        routes._SYNC_MEDIA_GROUP_BUFFERS.clear()

        async def _go():
            for mid in (27930, 27932, 27931, 27929):  # prod order
                msg = _album_msg(mid, chat, caption='老师艺名' if mid == 27929 else None)
                update = SimpleNamespace(
                    channel_post=None, edited_channel_post=msg, edited_message=None,
                    effective_message=msg, effective_chat=chat, effective_user=None,
                )
                ctx = SimpleNamespace(bot=bot, application=SimpleNamespace(bot_data={'clone_id': None}),
                                      job_queue=MagicMock())
                await routes.handle_sync_channel_posts(update, ctx)

        asyncio.get_event_loop().run_until_complete(_go())
        assert not routes._SYNC_MEDIA_GROUP_BUFFERS
        _assert_nothing_sent(bot)
        assert '[sync] skip edited post' in capsys.readouterr().out


def test_new_channel_album_still_buffered(flask_app):
    from app.modules.core import routes
    with flask_app.app_context():
        _setup(flask_app, -100633, -100634, 'channel')
        bot = _bot()
        chat = SimpleNamespace(id=-100633, type='channel', username='ch2', title='ch2')
        routes._SYNC_MEDIA_GROUP_BUFFERS.clear()

        async def _go():
            for mid in (1, 2):
                msg = _album_msg(mid, chat)
                update = SimpleNamespace(
                    channel_post=msg, edited_channel_post=None, edited_message=None,
                    effective_message=msg, effective_chat=chat, effective_user=None,
                )
                ctx = SimpleNamespace(bot=bot, application=SimpleNamespace(bot_data={'clone_id': None}),
                                      job_queue=MagicMock())
                await routes.handle_sync_channel_posts(update, ctx)

        asyncio.get_event_loop().run_until_complete(_go())
        bufs = [b for k, b in routes._SYNC_MEDIA_GROUP_BUFFERS.items() if k[1] == '-100633']
        assert len(bufs) == 1 and len(bufs[0]['messages']) == 2
        for b in bufs:
            if b.get('task'):
                b['task'].cancel()
        routes._SYNC_MEDIA_GROUP_BUFFERS.clear()


def test_edited_group_message_not_resynced(flask_app):
    from app.modules.core import routes
    with flask_app.app_context():
        _setup(flask_app, -100635, -100636, 'supergroup')
        bot = _bot()
        chat = SimpleNamespace(id=-100635, type='supergroup', title='g')
        user = SimpleNamespace(id=42, username='a', first_name='A', last_name='', is_bot=False)
        msg = _album_msg(5, chat, caption='edited caption')
        update = SimpleNamespace(
            message=None, edited_message=msg, channel_post=None, edited_channel_post=None,
            effective_message=msg, effective_chat=chat, effective_user=user,
        )
        ctx = SimpleNamespace(bot=bot, application=SimpleNamespace(bot_data={'clone_id': None}))
        routes._SYNC_MEDIA_GROUP_BUFFERS.clear()
        asyncio.get_event_loop().run_until_complete(routes.handle_sync_group_messages(update, ctx))
        assert not routes._SYNC_MEDIA_GROUP_BUFFERS
        _assert_nothing_sent(bot)
