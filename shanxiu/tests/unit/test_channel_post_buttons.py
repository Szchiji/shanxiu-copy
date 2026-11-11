"""频道帖自动加按钮：跳过已有按钮、相册锚定、保存 API、接线检查。"""

import asyncio
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.channel_post_buttons import (
    message_has_inline_buttons,
    normalize_button_links,
    pick_album_anchor_message,
    should_attach_channel_post_buttons,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestMessageHasInlineButtons:
    def test_none(self):
        assert message_has_inline_buttons(None) is False
        assert message_has_inline_buttons(SimpleNamespace(reply_markup=None)) is False

    def test_empty_keyboard(self):
        msg = SimpleNamespace(reply_markup=SimpleNamespace(inline_keyboard=[]))
        assert message_has_inline_buttons(msg) is False
        msg2 = SimpleNamespace(reply_markup=SimpleNamespace(inline_keyboard=[[]]))
        assert message_has_inline_buttons(msg2) is False

    def test_has_buttons(self):
        markup = InlineKeyboardMarkup([[InlineKeyboardButton('去', url='https://t.me/x')]])
        assert message_has_inline_buttons(SimpleNamespace(reply_markup=markup)) is True


class TestNormalizeButtonLinks:
    def test_filters_invalid(self):
        raw = [
            {'text': 'A', 'url': 'https://a.com'},
            {'text': '', 'url': 'https://b.com'},
            {'text': 'C', 'url': ''},
            'bad',
        ]
        out = normalize_button_links(raw)
        assert len(out) == 1
        assert out[0]['text'] == 'A'
        assert out[0]['row'] == 0

    def test_json_string(self):
        out = normalize_button_links('[{"text":"X","url":"https://x.com","row":2,"order":1}]')
        assert out == [{'text': 'X', 'url': 'https://x.com', 'row': 2, 'order': 1}]

    def test_assigns_row_by_index(self):
        out = normalize_button_links([
            {'text': '1', 'url': 'https://1.com'},
            {'text': '2', 'url': 'https://2.com'},
        ])
        assert [x['row'] for x in out] == [0, 1]


class TestPickAlbumAnchor:
    def test_prefers_caption(self):
        a = SimpleNamespace(message_id=10, caption=None, text=None)
        b = SimpleNamespace(message_id=11, caption='hello', text=None)
        c = SimpleNamespace(message_id=12, caption=None, text=None)
        assert pick_album_anchor_message([a, b, c]) is b

    def test_lowest_id_without_caption(self):
        a = SimpleNamespace(message_id=20, caption=None, text=None)
        b = SimpleNamespace(message_id=15, caption=None, text=None)
        assert pick_album_anchor_message([a, b]).message_id == 15

    def test_empty(self):
        assert pick_album_anchor_message([]) is None
        assert pick_album_anchor_message(None) is None


class TestShouldAttach:
    def test_disabled(self):
        assert should_attach_channel_post_buttons(False, [{'text': 'a', 'url': 'https://a'}], SimpleNamespace(reply_markup=None)) is False

    def test_no_links(self):
        assert should_attach_channel_post_buttons(True, [], SimpleNamespace(reply_markup=None)) is False

    def test_skip_existing(self):
        markup = InlineKeyboardMarkup([[InlineKeyboardButton('旧', url='https://old')]])
        msg = SimpleNamespace(reply_markup=markup)
        assert should_attach_channel_post_buttons(
            True, [{'text': '新', 'url': 'https://new'}], msg,
        ) is False

    def test_ok(self):
        assert should_attach_channel_post_buttons(
            True, [{'text': '新', 'url': 'https://new'}], SimpleNamespace(reply_markup=None),
        ) is True


class TestWiring:
    def test_handler_and_nav_wired(self):
        routes = Path('app/modules/core/routes.py').read_text()
        assert '_maybe_attach_channel_post_buttons' in routes
        assert 'await _maybe_attach_channel_post_buttons' in routes
        assert 'page_channel_post_buttons' in routes
        assert 'api_save_channel_post_buttons' in routes
        # Must not replace existing buttons
        assert 'message_has_inline_buttons' in routes
        # Coupon discussion path still present (untouched feature)
        assert 'channel_auto_buttons' in routes
        assert '频道福利' in routes

        base = Path('app/modules/core/templates/base.html').read_text()
        assert 'channel_post_buttons' in base
        assert '频道帖按钮' in base

        tpl = Path('app/modules/core/templates/channel_post_buttons.html').read_text()
        assert '原帖如果已经有按钮' in tpl
        assert '优惠券按钮' in tpl  # clarifies separation
        # Same button-adding UX as scheduled messages (rows + modal)
        assert '新增一行按钮' in tpl
        assert '本行新增一个按钮' in tpl
        assert 'buttonEditModal' in tpl
        assert 'linksContainer' in tpl
        assert 'links_text' not in tpl  # old textarea UI removed


class TestSaveApi:
    def test_save_and_load(self, flask_app, client):
        from app import db
        from app.models import BotGroup, ChannelPostButtonConfig

        with flask_app.app_context():
            ch = BotGroup(chat_id='-100555', title='ch', type='channel', is_active=True)
            db.session.add(ch)
            db.session.commit()
            gid = ch.id

        with client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['login_user_id'] = 1
            sess['login_user_name'] = 'tester'

        resp = client.post(
            '/core/api/save_channel_post_buttons',
            json={
                'group_id': gid,
                'enabled': True,
                'links': [
                    {'text': '官网', 'url': 'https://example.com'},
                    {'text': '', 'url': 'https://bad'},
                ],
            },
        )
        data = resp.get_json() or {}
        assert data.get('status') == 'ok', data
        assert data.get('enabled') is True
        assert len(data.get('links') or []) == 1

        with flask_app.app_context():
            cfg = ChannelPostButtonConfig.query.filter_by(group_id=gid).first()
            assert cfg is not None
            assert cfg.enabled is True
            assert '官网' in (cfg.links or '')

        page = client.get(f'/core/group/{gid}/channel_post_buttons')
        assert page.status_code == 200
        assert '频道帖按钮'.encode() in page.data
        assert b'example.com' in page.data
        assert '新增一行按钮'.encode() in page.data
        assert b'buttonEditModal' in page.data
        assert b'linksContainer' in page.data

        # Multi-button + row/order preserved (same format as scheduled messages)
        resp2 = client.post(
            '/core/api/save_channel_post_buttons',
            json={
                'group_id': gid,
                'enabled': True,
                'links': [
                    {'text': 'A', 'url': 'https://a.example', 'row': 0, 'order': 0},
                    {'text': 'B', 'url': 'https://b.example', 'row': 0, 'order': 1},
                    {'text': 'C', 'url': 'https://c.example', 'row': 1, 'order': 0},
                ],
            },
        )
        data2 = resp2.get_json() or {}
        assert data2.get('status') == 'ok', data2
        links2 = data2.get('links') or []
        assert len(links2) == 3
        assert {(l['text'], l['row'], l['order']) for l in links2} == {
            ('A', 0, 0), ('B', 0, 1), ('C', 1, 0),
        }
        page2 = client.get(f'/core/group/{gid}/channel_post_buttons')
        assert b'a.example' in page2.data
        assert b'b.example' in page2.data
        assert b'c.example' in page2.data


class TestAttachRuntime:
    def test_skips_when_post_has_buttons(self, flask_app):
        from app import db
        from app.models import BotGroup, ChannelPostButtonConfig
        from app.modules.core import routes

        with flask_app.app_context():
            ch = BotGroup(chat_id='-100777', title='ch', type='channel', is_active=True)
            db.session.add(ch)
            db.session.commit()
            db.session.add(ChannelPostButtonConfig(
                group_id=ch.id,
                enabled=True,
                links='[{"text":"新","url":"https://new.com"}]',
            ))
            db.session.commit()
            gid = ch.id
            group = BotGroup.query.get(gid)

        markup = InlineKeyboardMarkup([[InlineKeyboardButton('旧', url='https://old.com')]])
        msg = SimpleNamespace(
            message_id=42, media_group_id=None, reply_markup=markup,
            text='hi', caption=None,
        )
        chat = SimpleNamespace(id=-100777)
        bot = AsyncMock()
        bot.edit_message_reply_markup = AsyncMock()

        with flask_app.app_context():
            group = BotGroup.query.get(gid)
            _run(routes._maybe_attach_channel_post_buttons(bot, msg, chat, group, None))

        bot.edit_message_reply_markup.assert_not_awaited()

    def test_edits_when_no_buttons(self, flask_app):
        from app import db
        from app.models import BotGroup, ChannelPostButtonConfig
        from app.modules.core import routes

        with flask_app.app_context():
            ch = BotGroup(chat_id='-100888', title='ch', type='channel', is_active=True)
            db.session.add(ch)
            db.session.commit()
            db.session.add(ChannelPostButtonConfig(
                group_id=ch.id,
                enabled=True,
                links='[{"text":"去看看","url":"https://go.example"}]',
            ))
            db.session.commit()
            gid = ch.id

        msg = SimpleNamespace(
            message_id=99, media_group_id=None, reply_markup=None,
            text='post', caption=None,
        )
        chat = SimpleNamespace(id=-100888)
        bot = AsyncMock()
        bot.edit_message_reply_markup = AsyncMock(return_value=True)

        with flask_app.app_context():
            group = BotGroup.query.get(gid)
            _run(routes._maybe_attach_channel_post_buttons(bot, msg, chat, group, None))

        bot.edit_message_reply_markup.assert_awaited_once()
        kw = bot.edit_message_reply_markup.await_args.kwargs
        assert kw['chat_id'] == -100888
        assert kw['message_id'] == 99
        assert kw['reply_markup'] is not None
        rows = kw['reply_markup'].inline_keyboard
        assert rows[0][0].text == '去看看'
        assert rows[0][0].url == 'https://go.example'

    def test_edits_multi_row_buttons(self, flask_app):
        from app import db
        from app.models import BotGroup, ChannelPostButtonConfig
        from app.modules.core import routes

        with flask_app.app_context():
            ch = BotGroup(chat_id='-100889', title='ch', type='channel', is_active=True)
            db.session.add(ch)
            db.session.commit()
            db.session.add(ChannelPostButtonConfig(
                group_id=ch.id,
                enabled=True,
                links='[{"text":"A","url":"https://a.example","row":0,"order":0},'
                      '{"text":"B","url":"https://b.example","row":0,"order":1},'
                      '{"text":"C","url":"https://c.example","row":1,"order":0}]',
            ))
            db.session.commit()
            gid = ch.id

        msg = SimpleNamespace(
            message_id=100, media_group_id=None, reply_markup=None,
            text='post', caption=None,
        )
        chat = SimpleNamespace(id=-100889)
        bot = AsyncMock()
        bot.edit_message_reply_markup = AsyncMock(return_value=True)

        with flask_app.app_context():
            group = BotGroup.query.get(gid)
            _run(routes._maybe_attach_channel_post_buttons(bot, msg, chat, group, None))

        bot.edit_message_reply_markup.assert_awaited_once()
        rows = bot.edit_message_reply_markup.await_args.kwargs['reply_markup'].inline_keyboard
        assert len(rows) == 2
        assert [b.text for b in rows[0]] == ['A', 'B']
        assert [b.text for b in rows[1]] == ['C']

    def test_album_flush_anchors_caption(self, flask_app):
        from app.modules.core import routes

        a = SimpleNamespace(message_id=1, caption=None, text=None, reply_markup=None)
        b = SimpleNamespace(message_id=2, caption='图说', text=None, reply_markup=None)
        bot = AsyncMock()
        bot.edit_message_reply_markup = AsyncMock(return_value=True)
        key = (None, -100999, 'mg1')
        routes._CHANNEL_POST_BUTTON_ALBUM_BUFFERS[key] = {
            'messages': [a, b],
            'bot': bot,
            'chat_id': -100999,
            'links': [{'text': '链', 'url': 'https://l.com', 'row': 0, 'order': 0}],
            'task': None,
        }
        _run(routes._flush_channel_post_button_album(key))
        bot.edit_message_reply_markup.assert_awaited_once()
        assert bot.edit_message_reply_markup.await_args.kwargs['message_id'] == 2

    def test_album_flush_skips_if_any_part_has_buttons(self, flask_app):
        from app.modules.core import routes

        markup = InlineKeyboardMarkup([[InlineKeyboardButton('旧', url='https://old')]])
        a = SimpleNamespace(message_id=1, caption=None, text=None, reply_markup=None)
        b = SimpleNamespace(message_id=2, caption='x', text=None, reply_markup=markup)
        bot = AsyncMock()
        bot.edit_message_reply_markup = AsyncMock()
        key = (None, -100998, 'mg2')
        routes._CHANNEL_POST_BUTTON_ALBUM_BUFFERS[key] = {
            'messages': [a, b],
            'bot': bot,
            'chat_id': -100998,
            'links': [{'text': '链', 'url': 'https://l.com'}],
            'task': None,
        }
        _run(routes._flush_channel_post_button_album(key))
        bot.edit_message_reply_markup.assert_not_awaited()
