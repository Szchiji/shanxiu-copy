"""
app/utils.py
------------
Shared utility functions used across bot handlers, services, and web
routes.  This module must NOT import from ``app.modules.core.routes`` to
avoid circular dependencies.  It may import from ``app.models`` and
``app.bot.state``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from typing import Optional

import pytz
from cryptography.fernet import Fernet, InvalidToken
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.constants import ReactionEmoji


# ---------------------------------------------------------------------------
# Bot token encryption
# ---------------------------------------------------------------------------

_fernet_instance: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    """Return a cached Fernet instance keyed from ``SECRET_KEY``.

    The key is derived via SHA-256 so it is cheap to compute, yet fully
    dependent on ``SECRET_KEY``.  An attacker who only obtains the database
    dump cannot decrypt the stored tokens without also knowing ``SECRET_KEY``.
    """
    global _fernet_instance
    if _fernet_instance is None:
        secret = os.getenv('SECRET_KEY', 'default_secret_key').encode()
        raw_key = hashlib.sha256(secret + b':shanxiu_bot_token_v1').digest()
        _fernet_instance = Fernet(base64.urlsafe_b64encode(raw_key))
    return _fernet_instance


def encrypt_token(token: str) -> str:
    """Encrypt *token* for database storage.

    Returns the token unchanged if it is empty/None.
    """
    if not token:
        return token
    return _get_fernet().encrypt(token.encode()).decode()


def decrypt_token(value: str) -> str:
    """Decrypt a stored bot token.

    If *value* is not a valid Fernet token (e.g. a legacy plaintext token
    that was saved before encryption was introduced) it is returned as-is,
    ensuring backward compatibility.
    """
    if not value:
        return value
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return value  # plaintext fallback for pre-encryption tokens


# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------

BEIJING_TZ = pytz.timezone('Asia/Shanghai')


def get_beijing_now() -> datetime:
    """Return the current time in Beijing timezone as a naive datetime (for DB storage)."""
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)


def get_beijing_today() -> datetime:
    """Return today's date at midnight in Beijing timezone as a naive datetime."""
    now = datetime.now(BEIJING_TZ)
    return now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


# ---------------------------------------------------------------------------
# Chat-permission helpers
# ---------------------------------------------------------------------------

def get_muted_permissions() -> ChatPermissions:
    """Return ChatPermissions that mute a member (send_messages=False only).

    Using the minimal form is the most compatible approach across Telegram
    clients and API versions.
    """
    return ChatPermissions(can_send_messages=False)


def get_unrestricted_permissions() -> ChatPermissions:
    """Return ChatPermissions that restore the default rights for a member."""
    return ChatPermissions(
        can_send_messages=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

async def is_user_admin_in_group(bot, chat_id: int, user_id: int) -> bool:
    """Return True if *user_id* is an admin or creator in *chat_id*."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ('creator', 'administrator')
    except Exception as exc:
        print(f"Error checking admin status: {exc}")
        return False


async def is_user_chat_owner(bot, chat_id: int, user_id: int) -> bool:
    """Return True if *user_id* is the creator (owner) of *chat_id*."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status == 'creator'
    except Exception as exc:
        print(f"Error checking chat owner status: {exc}")
        return False


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def convert_chat_id_to_int(chat_id, group_id=None, group_title=None):
    """Safely convert *chat_id* to ``int`` for the Telegram API.

    Returns ``None`` and prints an error on failure (instead of raising).
    """
    try:
        return int(chat_id)
    except (ValueError, TypeError) as exc:
        msg = f"Invalid chat_id '{chat_id}'"
        if group_id:
            msg += f" for group {group_id}"
        if group_title:
            msg += f" ({group_title})"
        msg += f": {exc}"
        print(msg)
        return None


def log_admin_action(
    group_id: int,
    admin_id: int,
    admin_name: str,
    action_type: str,
    target_user_id=None,
    target_user_name=None,
    details=None,
):
    """Persist an admin action to the database.

    Requires an active Flask application context.
    """
    from app import db
    from app.models import AdminActionLog

    try:
        log = AdminActionLog(
            group_id=group_id,
            admin_id=admin_id,
            admin_name=admin_name,
            action_type=action_type,
            target_user_id=target_user_id,
            target_user_name=target_user_name,
            details=details,
        )
        db.session.add(log)
        db.session.commit()
    except Exception as exc:
        print(f"Error logging admin action: {exc}")


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def safe_int(val, default: int = 0) -> int:
    """Convert *val* to int, returning *default* on failure."""
    if val is None:
        return default
    if isinstance(val, str) and val.strip() == '':
        return default
    try:
        return int(val)
    except Exception:
        return default


def parse_telegram_message_link(url: str):
    """Parse a ``t.me`` message link into ``(from_chat_id, message_id)``.

    Supports both username-based and private-channel links:
    - ``https://t.me/username/42``      → ``('@username', 42)``
    - ``https://t.me/c/1234567890/42``  → ``(-1001234567890, 42)``

    Returns ``(None, None)`` when the URL cannot be parsed.
    """
    if not url:
        return None, None
    m = re.match(r'(?:https?://)?t\.me/c/(\d+)/(\d+)', url)
    if m:
        return int(f"-100{m.group(1)}"), int(m.group(2))
    m = re.match(r'(?:https?://)?t\.me/([A-Za-z0-9_]+)/(\d+)', url)
    if m:
        return f"@{m.group(1)}", int(m.group(2))
    return None, None


def build_inline_keyboard_from_links(links: list) -> list:
    """Convert a list of link dicts into rows for an InlineKeyboardMarkup.

    Each dict may contain ``text``, ``url``, ``row`` (int), and ``order`` (int).
    Buttons are grouped by ``row`` and sorted by ``order`` within each row.
    """
    if not links:
        return []

    rows_dict: dict = {}
    for link in links:
        if not link.get('text') or not link.get('url'):
            continue
        row_num = link.get('row', 0)
        order = link.get('order', 0)
        rows_dict.setdefault(row_num, []).append({
            'button': InlineKeyboardButton(link['text'], url=link['url']),
            'order': order,
        })

    keyboard = []
    for row_num in sorted(rows_dict.keys()):
        row_buttons = sorted(rows_dict[row_num], key=lambda x: x['order'])
        keyboard.append([btn['button'] for btn in row_buttons])

    return keyboard


# ---------------------------------------------------------------------------
# Telegram message-reaction helpers
# ---------------------------------------------------------------------------

# Official setMessageReaction emoji whitelist (Bot API ReactionTypeEmoji).
TELEGRAM_REACTION_EMOJIS: tuple[str, ...] = tuple(
    dict.fromkeys(e.value for e in ReactionEmoji)
)
DEFAULT_LIKE_EMOJI = ReactionEmoji.RED_HEART.value  # '❤' (no FE0F)

# Map common presentation variants (e.g. ❤️) back to the API form (❤).
_REACTION_EMOJI_BY_STRIPPED: dict[str, str] = {
    e.replace('\ufe0f', ''): e for e in TELEGRAM_REACTION_EMOJIS
}
# Prefer longer sequences first so ZWJ emojis match before their base glyph.
_REACTION_EMOJIS_BY_LENGTH: tuple[str, ...] = tuple(
    sorted(TELEGRAM_REACTION_EMOJIS, key=len, reverse=True)
)


def normalize_like_emoji(emoji: Optional[str], default: str = DEFAULT_LIKE_EMOJI) -> str:
    """Return a Telegram-allowed reaction emoji for setMessageReaction.

    Users often paste presentation variants (❤️) or free-form icons that are
    not in Telegram's reaction whitelist.  Those values make auto-like silently
    fail.  This helper:

    1. Accepts an exact whitelist match
    2. Maps FE0F / presentation variants onto the canonical form
    3. Extracts the first whitelist emoji embedded in free text
    4. Falls back to *default* (❤) when nothing valid is found
    """
    if not emoji or not str(emoji).strip():
        return default

    raw = str(emoji).strip()
    if raw in TELEGRAM_REACTION_EMOJIS:
        return raw

    stripped = raw.replace('\ufe0f', '')
    mapped = _REACTION_EMOJI_BY_STRIPPED.get(stripped)
    if mapped:
        return mapped

    # Try the same lookups after dropping surrounding non-emoji text.
    for candidate in _REACTION_EMOJIS_BY_LENGTH:
        if candidate in raw:
            return candidate
        cand_stripped = candidate.replace('\ufe0f', '')
        if cand_stripped and cand_stripped in stripped:
            return candidate

    return default


def is_valid_like_emoji(emoji: Optional[str]) -> bool:
    """Return True if *emoji* can be normalized to a Telegram reaction emoji
    without falling back to the default solely because input was empty/invalid.

    Empty input is treated as invalid (caller should keep previous/default).
    """
    if not emoji or not str(emoji).strip():
        return False
    normalized = normalize_like_emoji(emoji, default='')
    return bool(normalized) and normalized in TELEGRAM_REACTION_EMOJIS


# ---------------------------------------------------------------------------
# Authenticated-user custom member tags
# ---------------------------------------------------------------------------

_MAX_MEMBER_TAGS = 10
_MAX_MEMBER_TAG_LEN = 20


def parse_member_tags(value) -> list[str]:
    """Normalize raw tag input into a de-duplicated list of short tag strings.

    Accepts:
    - ``None`` / empty → ``[]``
    - JSON array string (DB storage form)
    - Python list / tuple
    - Comma / Chinese-comma / whitespace separated text (UI form)
    """
    if value is None:
        return []

    raw_items: list = []
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith('['):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    raw_items = parsed
                else:
                    raw_items = [text]
            except Exception:
                raw_items = re.split(r'[,，;；、\s]+', text)
        else:
            raw_items = re.split(r'[,，;；、\s]+', text)
    else:
        raw_items = [value]

    tags: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if item is None:
            continue
        tag = str(item).strip()
        # Strip a single leading '#' so "#VIP" and "VIP" are the same tag.
        if tag.startswith('#'):
            tag = tag[1:].strip()
        if not tag:
            continue
        if len(tag) > _MAX_MEMBER_TAG_LEN:
            tag = tag[:_MAX_MEMBER_TAG_LEN]
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) >= _MAX_MEMBER_TAGS:
            break
    return tags


def serialize_member_tags(value) -> str:
    """Serialize tags to the JSON array string stored on ``GroupUser.member_tags``."""
    return json.dumps(parse_member_tags(value), ensure_ascii=False)


def format_member_tags(value, sep: str = ' ') -> str:
    """Format tags for display / template placeholders (e.g. ``#VIP #核心``)."""
    tags = parse_member_tags(value)
    if not tags:
        return ''
    return sep.join(f'#{t}' for t in tags)


# Telegram setChatMemberTag limit (Bot API TagLimit.MAX_TAG_LENGTH).
TELEGRAM_MEMBER_TAG_MAX_LEN = 16
# Rough emoji / symbol strip so tags stay within Telegram's "no emoji" rule.
_TELEGRAM_TAG_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # misc symbols & pictographs extended
    "\U00002700-\U000027BF"  # dingbats
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # ZWJ
    "\U000020E3"             # combining enclosing keycap
    "]+",
    flags=re.UNICODE,
)


def normalize_telegram_member_tag(value: Optional[str], default: str = '') -> str:
    """Normalize a string for Telegram ``setChatMemberTag``.

    Rules from Bot API: 0–16 characters, emoji not allowed. Returns *default*
    when the cleaned value is empty.
    """
    if value is None:
        return default
    text = str(value).strip()
    if text.startswith('#'):
        text = text[1:].strip()
    text = _TELEGRAM_TAG_EMOJI_RE.sub('', text)
    # Collapse leftover whitespace after emoji removal.
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return default
    if len(text) > TELEGRAM_MEMBER_TAG_MAX_LEN:
        text = text[:TELEGRAM_MEMBER_TAG_MAX_LEN]
    return text


def resolve_telegram_member_tag(member_tags_value, conf: Optional[dict] = None) -> str:
    """Pick the Telegram nickname tag for an authenticated user.

    Preference order:
    1. First custom tag on the user (``member_tags``)
    2. Group default ``default_member_tag`` (default ``认证``)
    """
    conf = conf or {}
    default = normalize_telegram_member_tag(
        conf.get('default_member_tag', '认证'),
        default='认证',
    )
    tags = parse_member_tags(member_tags_value)
    if tags:
        primary = normalize_telegram_member_tag(tags[0], default='')
        if primary:
            return primary
    return default
