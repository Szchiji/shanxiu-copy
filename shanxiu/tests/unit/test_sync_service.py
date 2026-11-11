"""
tests/unit/test_sync_service.py
-------------------------------
Unit tests for cross-group / channel sync helpers (keyword blacklist, anti-loop,
HTML formatting, clickable sender prefix, album helpers).
"""

from types import SimpleNamespace

from app.services.sync_service import (
    album_caption_html,
    album_file_id,
    album_media_kind,
    build_channel_sender_prefix,
    build_group_sender_prefix,
    entities_to_html,
    is_album_message,
    is_channel_chat,
    is_group_chat,
    is_same_source_target,
    keyword_blocks_sync,
    should_skip_bot_sender,
    should_skip_own_bot_sender,
    sync_filter_text,
    sync_message_html,
)


class TestKeywordBlocksSync:
    def test_empty_never_blocks(self):
        assert keyword_blocks_sync(None, '["广告"]') is False
        assert keyword_blocks_sync('hello', None) is False
        assert keyword_blocks_sync('hello', '[]') is False

    def test_blacklist_hit(self):
        assert keyword_blocks_sync('这是广告内容', '["广告", "垃圾"]') is True

    def test_blacklist_miss(self):
        assert keyword_blocks_sync('正常消息', '["广告"]') is False

    def test_invalid_json_does_not_block(self):
        assert keyword_blocks_sync('广告', 'not-json') is False
        assert keyword_blocks_sync('广告', '{"a":1}') is False


class TestAntiLoop:
    def test_skip_bot_sender(self):
        assert should_skip_bot_sender(SimpleNamespace(is_bot=True)) is True
        assert should_skip_bot_sender(SimpleNamespace(is_bot=False)) is False
        assert should_skip_bot_sender(None) is False

    def test_skip_own_bot_sender_only(self):
        other = SimpleNamespace(id=1, is_bot=True)
        own = SimpleNamespace(id=42, is_bot=True)
        assert should_skip_own_bot_sender(other, 42) is False
        assert should_skip_own_bot_sender(own, 42) is True
        assert should_skip_own_bot_sender(SimpleNamespace(id=42, is_bot=False), 42) is False

    def test_same_source_target(self):
        assert is_same_source_target(-100123, '-100123') is True
        assert is_same_source_target(-100123, '-100999') is False
        assert is_same_source_target(None, '-100123') is False


class TestSyncFilterText:
    def test_prefers_text_over_caption(self):
        msg = SimpleNamespace(text='hello', caption='cap')
        assert sync_filter_text(msg) == 'hello'

    def test_falls_back_to_caption(self):
        msg = SimpleNamespace(text=None, caption='photo caption')
        assert sync_filter_text(msg) == 'photo caption'

    def test_none_when_empty(self):
        msg = SimpleNamespace(text=None, caption=None)
        assert sync_filter_text(msg) is None


class TestChatKindHelpers:
    def test_group_and_channel(self):
        assert is_group_chat(SimpleNamespace(type='supergroup')) is True
        assert is_group_chat(SimpleNamespace(type='group')) is True
        assert is_group_chat(SimpleNamespace(type='channel')) is False
        assert is_channel_chat(SimpleNamespace(type='channel')) is True
        assert is_channel_chat(SimpleNamespace(type='supergroup')) is False
        assert is_channel_chat(None) is False


class TestSenderPrefix:
    def test_group_prefix_off_by_default(self):
        """Group sync defaults to clean copy (no sender prefix)."""
        assert build_group_sender_prefix(None) == ''
        assert build_group_sender_prefix(SimpleNamespace(first_name='Alice', last_name=None, id=1)) == ''
        assert build_group_sender_prefix(
            SimpleNamespace(first_name='A', last_name='B', id=1), include_prefix=False
        ) == ''

    def test_group_prefix_name_on_own_line(self):
        assert build_group_sender_prefix(None, include_prefix=True) == '未知\n'
        assert build_group_sender_prefix(
            SimpleNamespace(first_name='Alice', last_name=None, id=42), include_prefix=True
        ) == '<a href="tg://user?id=42">Alice</a>\n'
        assert build_group_sender_prefix(
            SimpleNamespace(first_name='A', last_name='B', id=7), include_prefix=True
        ) == '<a href="tg://user?id=7">A B</a>\n'
        # Escape HTML in names
        assert build_group_sender_prefix(
            SimpleNamespace(first_name='A<b>', last_name=None, id=1), include_prefix=True
        ) == '<a href="tg://user?id=1">A&lt;b&gt;</a>\n'
        # No user id → plain escaped name
        assert build_group_sender_prefix(
            SimpleNamespace(first_name='Bob', last_name=None), include_prefix=True
        ) == 'Bob\n'

    def test_channel_prefix_is_empty(self):
        """Channel posts sync without a ``[频道名]`` prefix."""
        assert build_channel_sender_prefix(SimpleNamespace(title='公告频道')) == ''
        assert build_channel_sender_prefix(SimpleNamespace(title='  ')) == ''
        assert build_channel_sender_prefix(None) == ''

    def test_channel_prefix_when_enabled(self):
        assert build_channel_sender_prefix(
            SimpleNamespace(title='公告', username='news'), include_prefix=True
        ) == '<a href="https://t.me/news">公告</a>\n'
        assert build_channel_sender_prefix(
            SimpleNamespace(title='私密<x>', username=None), include_prefix=True
        ) == '<b>私密&lt;x&gt;</b>\n'


class TestEntitiesToHtml:
    def test_plain_escapes(self):
        assert entities_to_html('a<b>', None) == 'a&lt;b&gt;'
        assert entities_to_html('', None) == ''
        assert entities_to_html(None, None) == ''

    def test_bold_and_link(self):
        bold = SimpleNamespace(type='bold', offset=0, length=5)
        link = SimpleNamespace(type='text_link', offset=6, length=4, url='https://ex.com')
        html = entities_to_html('Hello link', [bold, link])
        assert html == '<b>Hello</b> <a href="https://ex.com">link</a>'

    def test_text_mention(self):
        user = SimpleNamespace(id=99)
        ent = SimpleNamespace(type='text_mention', offset=0, length=3, user=user)
        assert entities_to_html('Bob', [ent]) == '<a href="tg://user?id=99">Bob</a>'

    def test_utf16_emoji_offset(self):
        # "😀hi" — grinning face is one Unicode scalar but 2 UTF-16 units
        text = '😀hi'
        bold = SimpleNamespace(type='bold', offset=2, length=2)  # "hi"
        assert entities_to_html(text, [bold]) == '😀<b>hi</b>'


class TestSyncMessageHtml:
    def test_prefers_text_html_property(self):
        msg = SimpleNamespace(text='x', text_html='<b>x</b>', entities=None)
        assert sync_message_html(msg) == '<b>x</b>'

    def test_falls_back_to_entities(self):
        ent = SimpleNamespace(type='italic', offset=0, length=3)
        msg = SimpleNamespace(text='hey', text_html=None, entities=[ent])
        assert sync_message_html(msg) == '<i>hey</i>'

    def test_caption_path(self):
        msg = SimpleNamespace(caption='cap', caption_html=None, caption_entities=None)
        assert sync_message_html(msg, use_caption=True) == 'cap'


class TestAlbumHelpers:
    def test_is_album_message(self):
        assert is_album_message(SimpleNamespace(media_group_id='abc')) is True
        assert is_album_message(SimpleNamespace(media_group_id=None)) is False
        assert is_album_message(None) is False

    def test_album_media_kind_and_file_id(self):
        photo = SimpleNamespace(
            photo=[SimpleNamespace(file_id='p1'), SimpleNamespace(file_id='p2')],
            video=None, document=None, audio=None,
        )
        assert album_media_kind(photo) == 'photo'
        assert album_file_id(photo) == 'p2'

        video = SimpleNamespace(
            photo=None, video=SimpleNamespace(file_id='v1'), document=None, audio=None,
        )
        assert album_media_kind(video) == 'video'
        assert album_file_id(video) == 'v1'

    def test_album_caption_html_first_nonempty(self):
        m1 = SimpleNamespace(caption=None, caption_html=None, caption_entities=None)
        m2 = SimpleNamespace(
            caption='hi', caption_html='<b>hi</b>', caption_entities=None,
        )
        assert album_caption_html([m1, m2]) == '<b>hi</b>'
        assert album_caption_html([m1]) == ''
