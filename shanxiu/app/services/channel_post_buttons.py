"""频道帖自动加按钮：纯逻辑（是否跳过、相册锚定消息、链接规范化）。

与讨论区「优惠券 / 自动发送频道按钮」无关；本功能用 editMessageReplyMarkup
给频道原帖贴内联 URL 按钮。原帖已有按钮则跳过，从不整组替换。
"""

from __future__ import annotations

import json
from typing import Any, Optional


def message_has_inline_buttons(msg: Any) -> bool:
    """True if *msg* already carries an inline keyboard (any non-empty row)."""
    markup = getattr(msg, 'reply_markup', None) if msg is not None else None
    if markup is None:
        return False
    keyboard = getattr(markup, 'inline_keyboard', None)
    if not keyboard:
        # Some PTB objects expose reply_markup as dict-like
        if isinstance(markup, dict):
            keyboard = markup.get('inline_keyboard') or markup.get('keyboard')
        if not keyboard:
            return False
    return any(bool(row) for row in keyboard)


def normalize_button_links(raw: Any) -> list:
    """Return ``[{text, url, row, order}, ...]`` keeping only valid URL buttons.

    Accepts a list or a JSON string. Missing row/order → each link on its own row.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or '[]')
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []

    out = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        text = (item.get('text') or '').strip()
        url = (item.get('url') or '').strip()
        if not text or not url:
            continue
        row = item.get('row')
        order = item.get('order')
        if row is None:
            row = idx
        if order is None:
            order = 0
        try:
            row = int(row)
            order = int(order)
        except (TypeError, ValueError):
            row, order = idx, 0
        out.append({'text': text, 'url': url, 'row': row, 'order': order})
    return out


def pick_album_anchor_message(messages: Any) -> Optional[Any]:
    """Album message that should receive buttons: caption item, else lowest message_id."""
    msgs = [m for m in (messages or []) if m is not None]
    if not msgs:
        return None

    def _mid(m):
        mid = getattr(m, 'message_id', None)
        return mid if mid is not None else 0

    with_caption = [
        m for m in msgs
        if (getattr(m, 'caption', None) or getattr(m, 'text', None))
    ]
    if with_caption:
        return min(with_caption, key=_mid)
    return min(msgs, key=_mid)


def should_attach_channel_post_buttons(
    enabled: bool,
    links: Any,
    msg: Any,
) -> bool:
    """Whether we should call editMessageReplyMarkup for this post.

    Skip when disabled, no valid links, or the post already has inline buttons.
    """
    if not enabled:
        return False
    if not normalize_button_links(links):
        return False
    if message_has_inline_buttons(msg):
        return False
    return True
