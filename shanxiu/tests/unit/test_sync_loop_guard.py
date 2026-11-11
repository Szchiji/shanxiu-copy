"""Precise anti-loop: own-bot messages sync, only sync/forward outputs are skipped."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import sync_loop_guard as g
from app.services import userbot_sync as us
from app.services.userbot_sync import UbMessage


@pytest.fixture(autouse=True)
def _clean():
    g.reset()
    yield
    g.reset()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _item(chat=-100555, mid=1, **kw):
    base = dict(chat_id=chat, message_id=mid, text='hi', html='hi', sender_id=777,
                sender_name='Our Bot', sender_username='our_bot', sender_is_bot=True)
    base.update(kw)
    return UbMessage(**base)


def _rule(src, targets, rid=1):
    return dict(id=rid, enabled=True, source_chat_id=str(src), sender_mode='bots', sender_filter='',
                target_chat_ids=str([str(t) for t in targets]).replace("'", '"'),
                include_sender_prefix=False, sender_prefix_style='newline', sync_media=True,
                filter_keywords='[]', source_group_id=None)


class TestRegistry:
    def test_mark_and_check_various_results(self):
        g.mark_sync_output(-1001, SimpleNamespace(message_id=5))
        g.mark_sync_output('-1001', [SimpleNamespace(message_id=6), SimpleNamespace(message_id=7)])
        g.mark_sync_output(-1002, 9)
        assert g.is_sync_output_memory(-1001, 5)
        assert g.is_sync_output_memory(-1001, 7)
        assert g.is_sync_output_memory('-1002', 9)
        assert not g.is_sync_output_memory(-1001, 9)  # per chat
        assert not g.is_sync_output_memory(-1002, 5)

    def test_tracking_bot_registers_every_send(self):
        bot = AsyncMock()
        bot.send_message.return_value = SimpleNamespace(message_id=11)
        bot.copy_messages.return_value = (SimpleNamespace(message_id=12), SimpleNamespace(message_id=13))
        tb = g.track_bot(bot)
        assert g.track_bot(tb) is tb
        _run(tb.send_message(chat_id=-100900, text='header'))
        _run(tb.copy_messages(chat_id=-100900, from_chat_id=-1, message_ids=[1, 2]))
        for mid in (11, 12, 13):
            assert g.is_sync_output_memory(-100900, mid)
        bot.send_message.assert_awaited_once()  # underlying call untouched

    def test_non_send_methods_pass_through(self):
        bot = AsyncMock()
        bot.edit_message_reply_markup.return_value = SimpleNamespace(message_id=99)
        _run(g.track_bot(bot).edit_message_reply_markup(chat_id=-1, message_id=99))
        assert not g.is_sync_output_memory(-1, 99)


class TestUserbotLoop:
    def test_own_bot_normal_message_is_synced(self):
        bot = AsyncMock()
        bot.copy_message.return_value = SimpleNamespace(message_id=50)
        item = _item(mid=10)
        plans = us.plans_for_item([_rule(-100555, [-100900])], item, own_bot_ids={777})
        assert plans and item.from_own_bot
        _run(us.process_item(bot, None, item, plans))
        bot.copy_message.assert_awaited_once()
        assert g.is_sync_output_memory(-100900, 50)  # output recorded

    def test_bidirectional_no_echo(self):
        """A<->B: other bot posts in A -> our copy in B is NOT synced back to A."""
        rules = [_rule(-100555, [-100900], 1), _rule(-100900, [-100555], 2)]
        bot = AsyncMock()
        bot.copy_message.return_value = SimpleNamespace(message_id=70)
        src = _item(chat=-100555, mid=1, sender_id=4242, sender_username='other_bot')
        _run(us.process_item(bot, None, src, us.plans_for_item(rules, src, own_bot_ids={777})))
        assert bot.copy_message.await_count == 1
        # userbot now hears our output in B (author = our bot)
        echo = _item(chat=-100900, mid=70)
        plans = us.plans_for_item(rules, echo, own_bot_ids={777})
        assert plans  # rule matches…
        _run(us.process_item(bot, None, echo, plans))
        assert bot.copy_message.await_count == 1  # …but the guard stops the echo

    def test_late_registration_caught_by_recheck(self):
        """Userbot hears the output before the send response registers it."""
        bot = AsyncMock()
        echo = _item(chat=-100900, mid=81)
        plans = us.plans_for_item([_rule(-100900, [-100555])], echo, own_bot_ids={777})

        async def go():
            async def register_later():
                await asyncio.sleep(0.05)
                g.mark_sync_output(-100900, 81)
            asyncio.ensure_future(register_later())
            await us.process_item(bot, None, echo, plans)
        _run(go())
        bot.copy_message.assert_not_awaited()

    def test_header_and_reupload_outputs_registered(self):
        bot = AsyncMock()
        bot.send_message.return_value = SimpleNamespace(message_id=90)
        bot.copy_message.side_effect = Exception('Message to copy not found')
        bot.send_photo.return_value = SimpleNamespace(message_id=91)
        download = AsyncMock(return_value=(b'x', 'a.jpg'))
        long_prefix = '<a href="tg://user?id=1">' + 'N' * 1100 + '</a>\n'
        item = _item(mid=3, media_kind='photo', text='cap', html='cap')
        _run(us.deliver_single(bot, item, {'target_chat_id': '-100900', 'sender_prefix': long_prefix,
                                           'prefix_style': 'newline'}, download))
        assert g.is_sync_output_memory(-100900, 90)  # header line
        assert g.is_sync_output_memory(-100900, 91)  # re-uploaded photo


class TestChannelAndAlbum:
    def test_channel_sync_copy_registers_output(self):
        from app.modules.core import routes
        bot = AsyncMock()
        bot.copy_message.return_value = SimpleNamespace(message_id=33)
        msg = SimpleNamespace(
            text='hello', text_html='hello', caption=None, caption_html=None, entities=None,
            caption_entities=None, message_id=7, photo=None, video=None, document=None, audio=None,
            voice=None, video_note=None, sticker=None, animation=None, poll=None, location=None,
            contact=None, venue=None, media_group_id=None, reply_markup=None,
        )
        _run(routes._deliver_sync_copy(bot, '-1002', msg, '', True, from_chat_id=-1001))
        assert g.is_sync_output_memory(-1002, 33)

    def test_filter_out_album_parts(self):
        g.mark_sync_output(-1002, [SimpleNamespace(message_id=1), SimpleNamespace(message_id=2)])
        parts = [SimpleNamespace(message_id=1), SimpleNamespace(message_id=2), SimpleNamespace(message_id=3)]
        kept = g.filter_out_sync_outputs(-1002, parts)
        assert [m.message_id for m in kept] == [3]
