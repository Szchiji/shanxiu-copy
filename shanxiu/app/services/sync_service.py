"""
Helpers for cross-group / channel message sync.

Pure functions used by handle_sync_group_messages and handle_sync_channel_posts
so unit tests can cover keyword filtering, formatting, albums, and anti-loop
checks without a live bot.
"""

from __future__ import annotations

import html as html_module
import json
from typing import Any, Optional, Sequence


def keyword_blocks_sync(text: Optional[str], filter_keywords_json: Optional[str]) -> bool:
    """Return True if *text* hits any blacklist keyword in the JSON array.

    Sync filter is blacklist-only: messages containing a listed keyword are
    skipped. Empty / invalid JSON never blocks.
    """
    if not text or not filter_keywords_json:
        return False
    try:
        keywords = json.loads(filter_keywords_json)
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(keywords, list):
        return False
    return any(isinstance(kw, str) and kw and kw in text for kw in keywords)


def should_skip_bot_sender(user: Any) -> bool:
    """Skip syncing messages sent by bots (anti-loop with clone bots).

    Used for *group* sync only. Other bots' group messages are invisible to the
    Bot API unless a userbot is listening — skipping all bots here prevents
    clone-bot echo loops.
    """
    return bool(user is not None and getattr(user, "is_bot", False))


def should_skip_own_bot_sender(user: Any, bot_id: Any) -> bool:
    """Channel-sync anti-loop: skip only posts authored by *this* bot.

    Channel posts from *other* bots are visible to admin bots and must be
    synced (media + buttons). Skipping every ``is_bot`` author silently dropped
    those posts. Only our own bot id would re-enter sync after we post.
    """
    if user is None or bot_id is None:
        return False
    if not getattr(user, "is_bot", False):
        return False
    try:
        return int(user.id) == int(bot_id)
    except (TypeError, ValueError):
        return False


def is_same_source_target(source_chat_id: Any, target_group_id: Any) -> bool:
    """True when source chat id equals configured target (would sync to self)."""
    if source_chat_id is None or target_group_id is None:
        return False
    return str(source_chat_id) == str(target_group_id)


def sync_filter_text(msg: Any) -> Optional[str]:
    """Text used for keyword blacklist: message text or caption."""
    if msg is None:
        return None
    text = getattr(msg, "text", None)
    if text:
        return text
    return getattr(msg, "caption", None) or None


def is_group_chat(chat: Any) -> bool:
    """True for Telegram group / supergroup chats."""
    return bool(chat is not None and getattr(chat, "type", None) in ("group", "supergroup"))


def is_channel_chat(chat: Any) -> bool:
    """True for Telegram channel chats (channel_post source)."""
    return bool(chat is not None and getattr(chat, "type", None) == "channel")


SENDER_PREFIX_STYLES = ("newline", "forward")
DEFAULT_SENDER_PREFIX_STYLE = "newline"
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024


def normalize_prefix_style(style: Any) -> str:
    """Coerce a stored/posted style to a supported value (default ``newline``)."""
    s = (str(style).strip().lower() if style is not None else "")
    return s if s in SENDER_PREFIX_STYLES else DEFAULT_SENDER_PREFIX_STYLE


def build_group_sender_prefix(user: Any, include_prefix: bool = False) -> str:
    """Optional HTML header line for group member sync.

    Default *include_prefix* is False (empty string). When True, returns the
    sender's name on its own line as a clickable ``tg://user?id=`` mention, e.g.
    ``<a href="tg://user?id=42">Alice</a>\n`` — the original content follows on
    the next line. (Telegram may not open a chat for privacy-restricted users.)
    """
    if not include_prefix:
        return ""
    if user is None:
        return "未知\n"
    first = getattr(user, "first_name", None) or ""
    last = getattr(user, "last_name", None) or ""
    name = f"{first} {last}".strip() or "未知"
    safe = html_module.escape(name)
    uid = getattr(user, "id", None)
    if uid is not None:
        return f'<a href="tg://user?id={int(uid)}">{safe}</a>\n'
    return f"{safe}\n"


def build_channel_sender_prefix(chat: Any, include_prefix: bool = False) -> str:
    """Optional header line for channel post sync (default off — clean copy).

    When enabled: channel title on its own line, linked to ``https://t.me/<username>``
    for public channels (plain bold title for private ones).
    """
    if not include_prefix or chat is None:
        return ""
    title = (getattr(chat, "title", None) or "").strip()
    username = getattr(chat, "username", None)
    if not title:
        title = f"@{username}" if username else "频道"
    safe = html_module.escape(title)
    if username:
        return f'<a href="https://t.me/{html_module.escape(str(username), quote=True)}">{safe}</a>\n'
    return f"<b>{safe}</b>\n"


def utf16_len(text: Optional[str]) -> int:
    """Length in UTF-16 code units (how Telegram counts message limits)."""
    if not text:
        return 0
    return len(text.encode("utf-16-le")) // 2


def html_visible_len(html_text: Optional[str]) -> int:
    """Visible length of a small Telegram-HTML snippet (tags stripped, entities decoded)."""
    if not html_text:
        return 0
    import re as _re

    plain = html_module.unescape(_re.sub(r"<[^>]+>", "", html_text))
    return utf16_len(plain)


def prefix_fits(prefix_html: str, raw_body: Optional[str], limit: int) -> bool:
    """True when header line + original (plain) body stays within Telegram's *limit*."""
    return html_visible_len(prefix_html) + utf16_len(raw_body) <= limit


def combine_prefix_html(prefix_html: str, body_html: Optional[str]) -> str:
    """Header line + body; with no body, just the name (no trailing newline)."""
    prefix_html = prefix_html or ""
    if not body_html:
        return prefix_html.rstrip("\n")
    return f"{prefix_html}{body_html}"


def album_prefix_index(messages: Sequence[Any]) -> int:
    """Index of the album item that should carry the sender header.

    The caption-bearing item (first one with a caption), else the first item.
    """
    for i, m in enumerate(messages or []):
        if getattr(m, "caption", None):
            return i
    return 0


def _utf16_to_py_index(text: str, utf16_offset: int) -> int:
    """Convert a Telegram UTF-16 code-unit offset to a Python str index."""
    if utf16_offset <= 0:
        return 0
    encoded = text.encode("utf-16-le")
    byte_offset = min(utf16_offset * 2, len(encoded))
    return len(encoded[:byte_offset].decode("utf-16-le"))


def _entity_type_name(entity: Any) -> str:
    raw = getattr(entity, "type", None)
    if raw is None and isinstance(entity, dict):
        raw = entity.get("type")
    if raw is None:
        return ""
    name = getattr(raw, "name", None) or str(raw)
    if "." in name:
        name = name.split(".")[-1]
    return name.lower()


def _entity_attr(entity: Any, key: str) -> Any:
    if isinstance(entity, dict):
        return entity.get(key)
    return getattr(entity, key, None)


def _entity_open_close(entity: Any, text_slice: str) -> tuple[Optional[str], Optional[str]]:
    """Return (open_tag, close_tag) for a Telegram MessageEntity."""
    etype = _entity_type_name(entity)
    if etype in ("bold", "strong"):
        return "<b>", "</b>"
    if etype in ("italic", "em"):
        return "<i>", "</i>"
    if etype in ("underline", "ins"):
        return "<u>", "</u>"
    if etype in ("strikethrough", "strike", "del", "s"):
        return "<s>", "</s>"
    if etype == "spoiler":
        return '<span class="tg-spoiler">', "</span>"
    if etype == "code":
        return "<code>", "</code>"
    if etype == "pre":
        lang = _entity_attr(entity, "language")
        if lang:
            safe_lang = html_module.escape(str(lang))
            return f'<pre><code class="language-{safe_lang}">', "</code></pre>"
        return "<pre>", "</pre>"
    if etype == "text_link":
        url = _entity_attr(entity, "url") or ""
        return f'<a href="{html_module.escape(str(url), quote=True)}">', "</a>"
    if etype == "text_mention":
        user = _entity_attr(entity, "user")
        uid = getattr(user, "id", None) if user is not None else None
        if uid is None and isinstance(user, dict):
            uid = user.get("id")
        if uid is not None:
            return f'<a href="tg://user?id={int(uid)}">', "</a>"
        return None, None
    if etype == "url":
        href = html_module.escape(text_slice, quote=True)
        return f'<a href="{href}">', "</a>"
    if etype == "email":
        href = html_module.escape(text_slice, quote=True)
        return f'<a href="mailto:{href}">', "</a>"
    if etype == "custom_emoji":
        eid = _entity_attr(entity, "custom_emoji_id")
        if eid:
            return f'<tg-emoji emoji-id="{html_module.escape(str(eid))}">', "</tg-emoji>"
        return None, None
    # mention / hashtag / cashtag / bot_command / phone_number: keep escaped text
    return None, None


def entities_to_html(text: Optional[str], entities: Optional[Sequence[Any]] = None) -> str:
    """Convert plain text + Telegram entities to Telegram HTML.

    Entity offsets are UTF-16 code units (Telegram Bot API). Unsupported entity
    types are left as escaped plain text.
    """
    if not text:
        return ""
    if not entities:
        return html_module.escape(text)

    insertions: list[tuple[int, int, int, int, str]] = []
    for i, ent in enumerate(entities):
        offset = _entity_attr(ent, "offset")
        length = _entity_attr(ent, "length")
        if offset is None or length is None:
            continue
        start = _utf16_to_py_index(text, int(offset))
        end = _utf16_to_py_index(text, int(offset) + int(length))
        if start >= end or start < 0 or end > len(text):
            continue
        open_t, close_t = _entity_open_close(ent, text[start:end])
        if not open_t or close_t is None:
            continue
        # Same position: longer entity opens first; shorter closes first.
        insertions.append((start, 0, -int(length), i, open_t))
        insertions.append((end, 1, int(length), i, close_t))

    insertions.sort()
    parts: list[str] = []
    prev = 0
    for pos, _kind, _olen, _i, tag in insertions:
        if pos < prev:
            continue
        parts.append(html_module.escape(text[prev:pos]))
        parts.append(tag)
        prev = pos
    parts.append(html_module.escape(text[prev:]))
    return "".join(parts)


def sync_message_html(msg: Any, *, use_caption: bool = False) -> str:
    """HTML body for sync: prefer PTB ``text_html`` / ``caption_html``, else entities."""
    if msg is None:
        return ""
    if use_caption:
        ready = getattr(msg, "caption_html", None)
        if ready:
            return ready
        raw = getattr(msg, "caption", None) or ""
        return entities_to_html(raw, getattr(msg, "caption_entities", None))
    ready = getattr(msg, "text_html", None)
    if ready:
        return ready
    raw = getattr(msg, "text", None) or ""
    return entities_to_html(raw, getattr(msg, "entities", None))


def is_album_message(msg: Any) -> bool:
    """True when the message belongs to a Telegram media group / album."""
    return bool(msg is not None and getattr(msg, "media_group_id", None))


def album_media_kind(msg: Any) -> Optional[str]:
    """Return 'photo' | 'video' | 'document' | 'audio' for album-capable media."""
    if msg is None:
        return None
    if getattr(msg, "photo", None):
        return "photo"
    if getattr(msg, "video", None):
        return "video"
    if getattr(msg, "document", None):
        return "document"
    if getattr(msg, "audio", None):
        return "audio"
    return None


def album_file_id(msg: Any) -> Optional[str]:
    """Best file_id for an album item (largest photo size when photo)."""
    kind = album_media_kind(msg)
    if kind == "photo":
        photos = getattr(msg, "photo", None) or []
        return photos[-1].file_id if photos else None
    if kind == "video":
        video = getattr(msg, "video", None)
        return getattr(video, "file_id", None) if video else None
    if kind == "document":
        doc = getattr(msg, "document", None)
        return getattr(doc, "file_id", None) if doc else None
    if kind == "audio":
        audio = getattr(msg, "audio", None)
        return getattr(audio, "file_id", None) if audio else None
    return None


def album_caption_html(messages: Sequence[Any]) -> str:
    """First non-empty caption in the album, as Telegram HTML."""
    for m in messages:
        if getattr(m, "caption", None):
            return sync_message_html(m, use_caption=True)
    return ""


def classify_sync_message_type(msg: Any) -> str:
    """Rough message_type label for SyncMessageLog."""
    if msg is None:
        return "unknown"
    if getattr(msg, "media_group_id", None) and album_media_kind(msg):
        return "media_group"
    if getattr(msg, "text", None):
        return "text"
    for name in (
        "photo",
        "video",
        "document",
        "audio",
        "voice",
        "video_note",
        "sticker",
        "animation",
        "poll",
        "location",
        "contact",
        "venue",
    ):
        if getattr(msg, name, None):
            return name
    return "unknown"


def is_media_sync_message(msg: Any) -> bool:
    """True for non-text content that respects the sync_media toggle."""
    if msg is None or getattr(msg, "text", None):
        return False
    return any(
        getattr(msg, name, None)
        for name in (
            "photo",
            "video",
            "document",
            "audio",
            "voice",
            "video_note",
            "sticker",
            "animation",
        )
    )


def fetch_chat_info(bot_token: str, chat_id: str, timeout: float = 10.0) -> dict:
    """Call Telegram getChat and return a normalized dict.

    Returns ``{"ok": True, "id": str, "title": str, "type": str, "username": str|None}``
    or ``{"ok": False, "error": str}``. Does not raise on Telegram API errors.
    """
    import requests as _requests

    if not bot_token:
        return {"ok": False, "error": "缺少 Bot Token"}
    chat_id = (chat_id or "").strip()
    if not chat_id:
        return {"ok": False, "error": "目标 chat_id 为空"}

    try:
        resp = _requests.get(
            f"https://api.telegram.org/bot{bot_token}/getChat",
            params={"chat_id": chat_id},
            timeout=timeout,
        )
        data = resp.json()
    except Exception as e:
        # requests errors include the URL (with the bot token) — never surface it
        from app.log_redaction import redact_secrets
        return {"ok": False, "error": f"网络错误: {redact_secrets(str(e))}"}

    if not data.get("ok"):
        return {"ok": False, "error": data.get("description") or "getChat 失败"}

    chat = data.get("result") or {}
    title = chat.get("title") or chat.get("username") or str(chat.get("id", chat_id))
    return {
        "ok": True,
        "id": str(chat.get("id", chat_id)),
        "title": title,
        "type": chat.get("type") or "",
        "username": chat.get("username"),
    }
