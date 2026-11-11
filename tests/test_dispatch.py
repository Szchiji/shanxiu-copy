"""分流规则单测：不连 Telegram、不连数据库，纯 JSON 判定。

    cd merged && python -m pytest tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dispatch import Target, decide  # noqa: E402


def _msg(chat_type: str, text: str = "", **extra):
    return {"message": {"message_id": 1, "date": 0, "chat": {"id": 1, "type": chat_type}, "text": text, **extra}}


def test_group_message_goes_to_shanxiu():
    assert decide(_msg("supergroup", "大家好")).target is Target.SX


def test_channel_post_goes_to_shanxiu():
    assert decide({"channel_post": {"message_id": 2, "date": 0, "chat": {"id": -100, "type": "channel"}, "text": "公告"}}).target is Target.SX


def test_membership_updates_go_to_shanxiu():
    assert decide({"my_chat_member": {"chat": {"id": -100, "type": "supergroup"}}}).target is Target.SX
    assert decide({"chat_member": {"chat": {"id": -100, "type": "supergroup"}}}).target is Target.SX


def test_private_start_goes_to_yycj():
    assert decide(_msg("private", "/start")).target is Target.YY
    assert decide(_msg("private", "/start@merged_bot payload")).target is Target.YY
    assert decide(_msg("private", "/admin")).target is Target.YY


def test_private_other_commands_go_to_shanxiu():
    for cmd in ("/checkin", "/points", "/mylink", "/rank", "/lottery", "/help"):
        assert decide(_msg("private", cmd)).target is Target.SX, cmd


def test_private_plain_text_tries_yycj_first():
    assert decide(_msg("private", "你好")).target is Target.YY_FIRST


def test_private_reply_button_stays_with_yycj():
    assert decide(_msg("private", "🔍 搜索灯笼")).target is Target.YY


def test_private_callback_prefixes():
    yy = {"callback_query": {"id": "1", "data": "session_accept:abc", "from": {"id": 1}, "message": {"chat": {"id": 1, "type": "private"}}}}
    sx = {"callback_query": {"id": "1", "data": "points_shop:3", "from": {"id": 1}, "message": {"chat": {"id": 1, "type": "private"}}}}
    assert decide(yy).target is Target.YY
    assert decide(sx).target is Target.SX


def test_group_callback_goes_to_shanxiu():
    payload = {"callback_query": {"id": "1", "data": "session_end:x", "from": {"id": 1}, "message": {"chat": {"id": -5, "type": "supergroup"}}}}
    assert decide(payload).target is Target.SX


def test_unknown_update_falls_back_to_shanxiu():
    assert decide({}).target is Target.SX
    assert decide({"inline_query": {"id": "1"}}).target is Target.SX
