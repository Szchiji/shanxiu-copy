"""Unit tests for authenticated-user custom member tags helpers."""

from app.utils import (
    format_member_tags,
    normalize_telegram_member_tag,
    parse_member_tags,
    resolve_telegram_member_tag,
    serialize_member_tags,
)


def test_parse_empty_and_none():
    assert parse_member_tags(None) == []
    assert parse_member_tags('') == []
    assert parse_member_tags('   ') == []
    assert parse_member_tags('[]') == []


def test_parse_comma_separated_and_hash():
    assert parse_member_tags('VIP, 核心, 管理') == ['VIP', '核心', '管理']
    assert parse_member_tags('#VIP #核心') == ['VIP', '核心']
    assert parse_member_tags('VIP，核心；管理') == ['VIP', '核心', '管理']


def test_parse_json_array_and_dedupe():
    assert parse_member_tags('["VIP", "核心"]') == ['VIP', '核心']
    assert parse_member_tags(['VIP', 'vip', '核心', 'VIP']) == ['VIP', '核心']
    assert parse_member_tags(('A', 'B')) == ['A', 'B']


def test_parse_limits_length_and_count():
    long_tag = 'x' * 50
    assert parse_member_tags(long_tag) == ['x' * 20]
    many = [str(i) for i in range(20)]
    assert len(parse_member_tags(many)) == 10


def test_serialize_and_format():
    assert serialize_member_tags('VIP, 核心') == '["VIP", "核心"]'
    assert format_member_tags('VIP, 核心') == '#VIP #核心'
    assert format_member_tags([]) == ''
    assert format_member_tags(None) == ''


def test_normalize_telegram_member_tag_rules():
    assert normalize_telegram_member_tag(None, default='认证') == '认证'
    assert normalize_telegram_member_tag('', default='认证') == '认证'
    assert normalize_telegram_member_tag('#官方认证') == '官方认证'
    assert normalize_telegram_member_tag('少妇🎉') == '少妇'
    assert normalize_telegram_member_tag('a' * 20) == 'a' * 16
    assert normalize_telegram_member_tag('  VIP  ') == 'VIP'


def test_resolve_telegram_member_tag_prefers_first_custom():
    conf = {'default_member_tag': '认证'}
    assert resolve_telegram_member_tag('["官方认证", "备用"]', conf) == '官方认证'
    assert resolve_telegram_member_tag('[]', conf) == '认证'
    assert resolve_telegram_member_tag(None, conf) == '认证'
    assert resolve_telegram_member_tag('["🎉"]', conf) == '认证'  # emoji-only falls back
    assert resolve_telegram_member_tag('少妇', {'default_member_tag': '默认'}) == '少妇'
