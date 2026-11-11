"""Unit tests for Telegram reaction emoji normalization used by auto-like."""

from app.utils import (
    DEFAULT_LIKE_EMOJI,
    TELEGRAM_REACTION_EMOJIS,
    is_valid_like_emoji,
    normalize_like_emoji,
)


def test_default_like_emoji_is_telegram_heart():
    assert DEFAULT_LIKE_EMOJI == '❤'
    assert DEFAULT_LIKE_EMOJI in TELEGRAM_REACTION_EMOJIS


def test_normalize_accepts_whitelist_emoji():
    assert normalize_like_emoji('👍') == '👍'
    assert normalize_like_emoji('🔥') == '🔥'
    assert normalize_like_emoji('🎉') == '🎉'


def test_normalize_maps_presentation_variant_heart():
    # Common UI / IME form includes U+FE0F; Bot API expects plain ❤.
    assert normalize_like_emoji('❤️') == '❤'


def test_normalize_extracts_first_valid_emoji_from_text():
    assert normalize_like_emoji('点赞👍一下') == '👍'
    assert normalize_like_emoji('  🔥  ') == '🔥'


def test_normalize_falls_back_for_invalid_icons():
    assert normalize_like_emoji('⭐') == DEFAULT_LIKE_EMOJI
    assert normalize_like_emoji('🚀') == DEFAULT_LIKE_EMOJI
    assert normalize_like_emoji('not-an-emoji') == DEFAULT_LIKE_EMOJI
    assert normalize_like_emoji('') == DEFAULT_LIKE_EMOJI
    assert normalize_like_emoji(None) == DEFAULT_LIKE_EMOJI


def test_normalize_heart_on_fire_zwj_sequence():
    # Ensure longer ZWJ sequences win over the base heart glyph.
    assert normalize_like_emoji('❤️‍🔥') == '❤️‍🔥'


def test_is_valid_like_emoji():
    assert is_valid_like_emoji('👍') is True
    assert is_valid_like_emoji('❤️') is True
    assert is_valid_like_emoji('⭐') is False
    assert is_valid_like_emoji('') is False
    assert is_valid_like_emoji(None) is False
