"""Routing of userbot-heard messages into sync: bot-only filter, dedupe, album grouping, delivery."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import userbot_sync as us
from app.services.userbot_sync import UbMessage


def _item(mid=1, **kw):
    base = dict(chat_id=-100555, message_id=mid, text='hi', html='hi', sender_id=777,
                sender_name='Some Bot', sender_username='some_bot', sender_is_bot=True)
    base.update(kw)
    return UbMessage(**base)


def _rule(**kw):
    r = dict(id=1, enabled=True, source_chat_id='-100555', sender_mode='bots', sender_filter='',
             target_chat_ids='["-100900"]', include_sender_prefix=False, sender_prefix_style='newline',
             sync_media=True, filter_keywords='[]', source_group_id=None)
    r.update(kw)
    return r


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestSelection:
    def test_default_bots_only(self):
        assert us.plans_for_item([_rule()], _item())
        assert us.plans_for_item([_rule()], _item(sender_is_bot=False)) == []

    def test_selected_bots(self):
        rule = _rule(sender_mode='selected', sender_filter='@Some_Bot, 42')
        assert us.plans_for_item([rule], _item())
        assert us.plans_for_item([rule], _item(sender_id=42, sender_username='x'))
        assert us.plans_for_item([rule], _item(sender_id=43, sender_username='other_bot')) == []
        assert us.plans_for_item([_rule(sender_mode='selected', sender_filter='')], _item()) == []

    def test_users_only_skips_bots(self):
        rule = _rule(sender_mode='users')
        human = _item(sender_is_bot=False, sender_id=111, sender_username='alice', sender_name='Alice')
        plans = us.plans_for_item([rule], human)
        assert [p['target_chat_id'] for p in plans] == ['-100900']
        assert us.plans_for_item([rule], _item()) == []  # bot skipped
        # still skip targets the main bot already syncs (avoid duplicate human delivery)
        plans = us.plans_for_item([rule], human, main_synced_pairs={('-100555', '-100900')})
        assert plans == []

    def test_all_mode_skips_targets_main_bot_already_syncs(self):
        rule = _rule(sender_mode='all', target_chat_ids='["-100900", "-100901"]')
        human = _item(sender_is_bot=False)
        plans = us.plans_for_item([rule], human, main_synced_pairs={('-100555', '-100900')})
        assert [p['target_chat_id'] for p in plans] == ['-100901']
        # bot messages are never delivered to the main bot → never skipped
        plans = us.plans_for_item([rule], _item(), main_synced_pairs={('-100555', '-100900')})
        assert len(plans) == 2

    def test_own_bot_messages_are_relayed_and_marked(self):
        item = _item(sender_id=777)
        plans = us.plans_for_item([_rule()], item, own_bot_ids={777})
        assert [p['target_chat_id'] for p in plans] == ['-100900']
        assert item.from_own_bot is True

    def test_other_source_disabled_media_and_dedupe(self):
        assert us.plans_for_item([_rule(source_chat_id='-1')], _item()) == []
        assert us.plans_for_item([_rule(enabled=False)], _item()) == []
        assert us.plans_for_item([_rule(sync_media=False)], _item(media_kind='photo')) == []
        plans = us.plans_for_item([_rule(), _rule(id=2, target_chat_ids='["-100900", "-100555"]')], _item())
        assert [p['target_chat_id'] for p in plans] == ['-100900']  # deduped; source never a target

    def test_prefix_built_from_sender(self):
        plans = us.plans_for_item([_rule(include_sender_prefix=True)], _item())
        assert plans[0]['sender_prefix'] == '<a href="https://t.me/some_bot">Some Bot</a>\n'

    def test_recent_seen(self):
        seen = us.RecentSeen(ttl=60)
        assert seen.check_and_add((1, 2)) is True
        assert seen.check_and_add((1, 2)) is False
        assert seen.check_and_add((1, 3)) is True


class TestTelethonConversion:
    def test_entities_and_bot_sender(self):
        from telethon.tl.types import MessageEntityBold, MessageEntityTextUrl
        from app.userbot.manager import to_ub_message
        msg = SimpleNamespace(id=10, message='Hello world', grouped_id=99,
                              entities=[MessageEntityBold(0, 5), MessageEntityTextUrl(6, 5, 'https://x.y')],
                              photo=object(), file=SimpleNamespace(name=None, size=100), fwd_from=None)
        sender = SimpleNamespace(id=5, first_name='Pic', last_name='Bot', username='pic_bot', bot=True)
        item = to_ub_message(msg, -100123, sender, 'Src')
        assert item.html == '<b>Hello</b> <a href="https://x.y">world</a>'
        assert item.media_kind == 'photo' and item.grouped_id == '99'
        assert item.sender_is_bot and item.sender_name == 'Pic Bot' and item.chat_id == -100123

    def test_channel_sender_and_text(self):
        from app.userbot.manager import to_ub_message
        msg = SimpleNamespace(id=11, message='<x>', grouped_id=None, entities=None, file=None, fwd_from=None)
        chan = SimpleNamespace(id=12345, title='Chan', username=None)
        item = to_ub_message(msg, -100123, chan)
        assert item.media_kind is None and item.html == '&lt;x&gt;'
        assert item.sender_id == -10012345 and not item.sender_is_bot


class TestDelivery:
    def test_copy_without_prefix(self):
        bot = AsyncMock()
        bot.copy_message.return_value = SimpleNamespace(message_id=5)
        mid, method = _run(us.deliver_single(bot, _item(media_kind='photo'), {'target_chat_id': '-100900'}))
        assert (mid, method) == (5, 'copy')
        assert bot.copy_message.await_args.kwargs['from_chat_id'] == -100555

    def test_text_with_prefix_uses_send_message(self):
        bot = AsyncMock()
        bot.send_message.return_value = SimpleNamespace(message_id=6)
        plan = {'target_chat_id': '-100900', 'sender_prefix': '<a href="tg://user?id=7">B</a>\n', 'prefix_style': 'newline'}
        mid, method = _run(us.deliver_single(bot, _item(html='<b>hi</b>'), plan))
        assert method == 'send_message'
        assert bot.send_message.await_args.kwargs['text'] == '<a href="tg://user?id=7">B</a>\n<b>hi</b>'
        bot.copy_message.assert_not_awaited()

    def test_copy_failure_falls_back_to_userbot_download(self):
        bot = AsyncMock()
        bot.copy_message.side_effect = Exception('chat not found')
        bot.send_photo.return_value = SimpleNamespace(message_id=8)
        download = AsyncMock(return_value=(b'\x89PNG', 'a.jpg'))
        mid, method = _run(us.deliver_single(bot, _item(media_kind='photo', text='cap', html='cap'),
                                             {'target_chat_id': '-100900'}, download))
        assert (mid, method) == (8, 'reupload')
        download.assert_awaited_once_with(-100555, 1)
        assert bot.send_photo.await_args.kwargs['caption'] == 'cap'

    def test_album_reupload_single_media_group(self):
        bot = AsyncMock()
        bot.copy_messages.side_effect = Exception('no access')
        bot.send_media_group.return_value = [SimpleNamespace(message_id=20)]
        download = AsyncMock(return_value=(b'img', 'x.jpg'))
        items = [_item(3, media_kind='photo', text='', html=''), _item(2, media_kind='photo', text='cap', html='cap')]
        mid, method = _run(us.deliver_album(bot, items, {'target_chat_id': '-100900'}, download))
        assert method == 'reupload_album' and mid == 20
        media = bot.send_media_group.await_args.kwargs['media']
        assert len(media) == 2 and media[0].caption == 'cap'


class TestAlbumGroupingAndLogs:
    def test_album_parts_buffered_into_one_copy_messages(self, flask_app):
        from app.models import SyncMessageLog
        from app.modules.core import routes
        bot = AsyncMock()
        bot.copy_messages.return_value = [SimpleNamespace(message_id=30), SimpleNamespace(message_id=31)]
        plans = us.plans_for_item([_rule(target_chat_ids='["-100931"]')], _item(media_kind='photo'))

        async def go():
            orig = routes._SYNC_MEDIA_GROUP_FLUSH_DELAY
            routes._SYNC_MEDIA_GROUP_FLUSH_DELAY = 0.05
            try:
                for mid in (41, 40):
                    await us.process_item(bot, flask_app, _item(mid, media_kind='photo', grouped_id='g1',
                                                                text='cap' if mid == 40 else ''), plans)
                await asyncio.sleep(0.3)
            finally:
                routes._SYNC_MEDIA_GROUP_FLUSH_DELAY = orig

        _run(go())
        bot.copy_messages.assert_awaited_once()
        assert bot.copy_messages.await_args.kwargs['message_ids'] == [40, 41]
        with flask_app.app_context():
            logs = SyncMessageLog.query.filter_by(target_group_id='-100931').all()
            assert len(logs) == 1 and logs[0].via == 'userbot' and logs[0].status == 'success'
            assert logs[0].message_type == 'media_group' and logs[0].content_preview == 'cap'

    def test_single_text_logged_and_keyword_filtered(self, flask_app):
        from app.models import SyncMessageLog
        bot = AsyncMock()
        bot.copy_message.return_value = SimpleNamespace(message_id=50)
        plans = us.plans_for_item([_rule(target_chat_ids='["-100932"]', filter_keywords='["广告"]')], _item())
        _run(us.process_item(bot, flask_app, _item(60, text='正常消息', html='正常消息'), plans))
        _run(us.process_item(bot, flask_app, _item(61, text='这是广告', html='这是广告'), plans))
        assert bot.copy_message.await_count == 1
        with flask_app.app_context():
            st = sorted(l.status for l in SyncMessageLog.query.filter_by(target_group_id='-100932').all())
            assert st == ['filtered', 'success']


class TestButtons:
    def test_telethon_url_and_callback_rows(self):
        from telethon.tl.types import (
            InlineButtonTypeCallback, InlineButtonTypeUrl, KeyboardInlineButton,
            KeyboardInlineButtonRow, ReplyInlineMarkup,
        )
        from app.userbot.manager import telethon_reply_markup_to_rows, to_ub_message
        markup = ReplyInlineMarkup(rows=[
            KeyboardInlineButtonRow(buttons=[
                KeyboardInlineButton(text='去看看', type=InlineButtonTypeUrl('https://t.me/x')),
                KeyboardInlineButton(text='点我', type=InlineButtonTypeCallback(b'act:1')),
            ]),
        ])
        rows = telethon_reply_markup_to_rows(markup)
        assert rows == [[
            {'text': '去看看', 'url': 'https://t.me/x'},
            {'text': '点我', 'callback_data': 'act:1'},
        ]]
        msg = SimpleNamespace(id=3, message='hi', grouped_id=None, entities=None, file=None,
                              fwd_from=None, reply_markup=markup, photo=object())
        sender = SimpleNamespace(id=9, first_name='B', last_name=None, username='b_bot', bot=True)
        item = to_ub_message(msg, -1001, sender)
        assert item.buttons == rows and item.media_kind == 'photo'

    def test_legacy_url_button_fake(self):
        from app.userbot.manager import telethon_reply_markup_to_rows
        row = SimpleNamespace(buttons=[SimpleNamespace(text='L', url='https://ex.am', type=None, data=None)])
        # type=None path uses getattr url
        btn = SimpleNamespace(text='L', url='https://ex.am')
        rows = telethon_reply_markup_to_rows(SimpleNamespace(rows=[SimpleNamespace(buttons=[btn])]))
        assert rows == [[{'text': 'L', 'url': 'https://ex.am'}]]

    def test_buttons_to_markup(self):
        from telegram import InlineKeyboardButton
        markup = us.buttons_to_markup([[
            {'text': 'A', 'url': 'https://a.example'},
            {'text': 'B', 'callback_data': 'cb'},
        ]])
        assert markup is not None
        assert isinstance(markup.inline_keyboard[0][0], InlineKeyboardButton)
        assert markup.inline_keyboard[0][0].url == 'https://a.example'
        assert markup.inline_keyboard[0][1].callback_data == 'cb'

    def test_reupload_passes_reply_markup(self):
        bot = AsyncMock()
        bot.copy_message.side_effect = Exception('Message to copy not found')
        bot.send_photo.return_value = SimpleNamespace(message_id=88)
        download = AsyncMock(return_value=(b'\xff', 'a.jpg'))
        item = _item(media_kind='photo', text='cap', html='cap',
                     buttons=[[{'text': '开', 'url': 'https://open.example'}]])
        mid, method = _run(us.deliver_single(bot, item, {'target_chat_id': '-100900'}, download))
        assert (mid, method) == (88, 'reupload')
        kw = bot.send_photo.await_args.kwargs
        assert kw['reply_markup'] is not None
        assert kw['reply_markup'].inline_keyboard[0][0].url == 'https://open.example'

    def test_copy_passes_reply_markup(self):
        bot = AsyncMock()
        bot.copy_message.return_value = SimpleNamespace(message_id=5)
        item = _item(media_kind='photo', buttons=[[{'text': 'X', 'url': 'https://x.example'}]])
        mid, method = _run(us.deliver_single(bot, item, {'target_chat_id': '-100900'}))
        assert method == 'copy'
        assert bot.copy_message.await_args.kwargs['reply_markup'].inline_keyboard[0][0].url == 'https://x.example'

    def test_album_reattaches_buttons_after_reupload(self):
        bot = AsyncMock()
        bot.copy_messages.side_effect = Exception('no access')
        bot.send_media_group.return_value = [
            SimpleNamespace(message_id=20), SimpleNamespace(message_id=21),
        ]
        bot.edit_message_reply_markup.return_value = True
        download = AsyncMock(return_value=(b'img', 'x.jpg'))
        items = [
            _item(2, media_kind='photo', text='cap', html='cap',
                  buttons=[[{'text': '一', 'url': 'https://one.example'}]]),
            _item(3, media_kind='photo', text='', html='', buttons=None),
        ]
        mid, method = _run(us.deliver_album(bot, items, {'target_chat_id': '-100900'}, download))
        assert method == 'reupload_album' and mid == 20
        bot.edit_message_reply_markup.assert_awaited()
        kw = bot.edit_message_reply_markup.await_args.kwargs
        assert kw['message_id'] == 20
        assert kw['reply_markup'].inline_keyboard[0][0].url == 'https://one.example'



class TestUsersPrefix:
    def test_users_mode_prefix_links_user(self):
        rule = _rule(sender_mode='users', include_sender_prefix=True)
        human = _item(sender_is_bot=False, sender_id=111, sender_name='Alice <A>', sender_username=None)
        plans = us.plans_for_item([rule], human)
        assert plans[0]['sender_prefix'] == '<a href="tg://user?id=111">Alice &lt;A&gt;</a>\n'
        # with a username → t.me link (clickable for everyone)
        named = _item(sender_is_bot=False, sender_id=111, sender_name='Alice', sender_username='alice')
        assert us.plans_for_item([rule], named)[0]['sender_prefix'] == '<a href="https://t.me/alice">Alice</a>\n'

    def test_channel_or_anonymous_sender_not_tg_user_link(self):
        rule = _rule(sender_mode='users', include_sender_prefix=True)
        chan = _item(sender_is_bot=False, sender_id=-1001234, sender_name='Chan', sender_username='chan_pub')
        assert us.plans_for_item([rule], chan)[0]['sender_prefix'] == '<a href="https://t.me/chan_pub">Chan</a>\n'
        anon = _item(sender_is_bot=False, sender_id=-1009, sender_name='Grp', sender_username=None)
        assert us.plans_for_item([rule], anon)[0]['sender_prefix'] == 'Grp\n'
        nobody = _item(sender_is_bot=False, sender_id=None, sender_name='', sender_username=None)
        assert us.plans_for_item([rule], nobody)[0]['sender_prefix'] == '未知\n'

    def test_entity_error_retries_plain_with_name(self, flask_app):
        bot = AsyncMock()
        calls = []

        async def send_message(**kw):
            calls.append(kw['text'])
            if len(calls) == 1:
                raise Exception('Entity_text_invalid')
            return SimpleNamespace(message_id=9)

        bot.send_message.side_effect = send_message
        rule = _rule(sender_mode='users', include_sender_prefix=True, target_chat_ids='["-100940"]')
        human = _item(sender_is_bot=False, sender_id=111, sender_name='Bob', text='a<b', html='<b>a&lt;b</b>')
        plans = us.plans_for_item([rule], human)
        _run(us.deliver_to_plans(bot, flask_app, human, plans))
        assert len(calls) == 2
        assert calls[1] == 'Bob\n<b>a&lt;b</b>'  # only the name link dropped; body formatting kept

    def test_entity_error_twice_falls_back_to_plain_body(self, flask_app):
        bot = AsyncMock()
        calls = []

        async def send_message(**kw):
            calls.append(kw['text'])
            if len(calls) <= 2:
                raise Exception('Entity_text_invalid')
            return SimpleNamespace(message_id=9)

        bot.send_message.side_effect = send_message
        rule = _rule(sender_mode='users', include_sender_prefix=True, target_chat_ids='["-100941"]')
        human = _item(sender_is_bot=False, sender_id=111, sender_name='Bob', text='a<b', html='<b>a&lt;b</b>')
        _run(us.deliver_to_plans(bot, flask_app, human, us.plans_for_item([rule], human)))
        assert calls[-1] == 'Bob\na&lt;b' and len(calls) == 3

    def test_users_page_has_prefix_hint(self, client):
        with client.session_transaction() as s:
            s['logged_in'] = True
            s.pop('clone_id', None)
        body = client.get('/core/userbot').get_data(as_text=True)
        assert 'ubUsersPrefixHint' in body and 'ubPrefixTouched' in body
