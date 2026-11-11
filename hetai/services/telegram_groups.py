"""Map real Telegram membership from events and getChatMember, never from URL hints."""

from __future__ import annotations

from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hetai.auth import FIXTURE_TELEGRAM_IDS, Actor
from hetai.config import Settings
from hetai.models import Group, GroupMember, User

STATUS_ADMIN = {"creator", "administrator"}
STATUS_MEMBER = {"creator", "administrator", "member", "restricted"}
STATUS_LEFT = {"left", "kicked"}

# Tests may replace this to avoid any real network.
GetChatMemberFn = Callable[[int, int], Any]
_get_chat_member_impl: GetChatMemberFn | None = None


def set_get_chat_member_impl(fn: GetChatMemberFn | None) -> None:
    global _get_chat_member_impl
    _get_chat_member_impl = fn


def telegram_status_to_role(status: str) -> str:
    if status == "creator":
        return "owner"
    if status == "administrator":
        return "admin"
    return "member"


def _payload_status(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("status") or "")
    return str(getattr(payload, "status", "") or "")


async def upsert_real_user(
    session: AsyncSession,
    settings: Settings,
    telegram_id: int,
    *,
    username: str | None = None,
    display_name: str = "",
) -> User:
    if telegram_id in FIXTURE_TELEGRAM_IDS:
        raise ValueError("拒绝将示例身份与真实 Telegram 用户合并")
    res = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = res.scalar_one_or_none()
    from hetai.auth import real_role_for_telegram_id

    role = real_role_for_telegram_id(settings, telegram_id, user.role if user else "member")
    if user:
        if user.is_demo_fixture:
            raise ValueError("拒绝将示例夹具用户提升为真实用户")
        if username:
            user.username = username
        if display_name:
            user.display_name = display_name
        user.role = role
        return user
    user = User(
        telegram_id=telegram_id,
        username=username,
        display_name=display_name or str(telegram_id),
        role=role,
        is_demo_fixture=False,
    )
    session.add(user)
    await session.flush()
    return user


async def upsert_real_group(
    session: AsyncSession,
    chat_id: int,
    *,
    title: str = "",
    bot_status: str = "",
) -> Group:
    res = await session.execute(select(Group).where(Group.chat_id == chat_id))
    group = res.scalar_one_or_none()
    settings_blob: dict = {}
    if group and isinstance(group.settings, dict):
        settings_blob = dict(group.settings)
    if bot_status:
        settings_blob["bot_status"] = bot_status
    settings_blob["source"] = "telegram"
    settings_blob.setdefault("moderation_authorized", False)
    if group:
        if group.is_demo_fixture:
            raise ValueError("拒绝将示例群与真实 Telegram 群合并")
        if title:
            group.title = title
        group.settings = settings_blob
        if bot_status in STATUS_LEFT:
            group.is_active = False
        elif bot_status in STATUS_MEMBER:
            group.is_active = True
        return group
    group = Group(
        chat_id=chat_id,
        title=title or f"群 {chat_id}",
        description="",
        settings=settings_blob,
        is_active=bot_status not in STATUS_LEFT,
        is_demo_fixture=False,
    )
    session.add(group)
    await session.flush()
    return group


async def apply_membership(
    session: AsyncSession,
    group: Group,
    user: User,
    status: str,
) -> Optional[GroupMember]:
    res = await session.execute(
        select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == user.id)
    )
    row = res.scalar_one_or_none()
    if status in STATUS_LEFT:
        if row:
            await session.delete(row)
            await session.flush()
        return None
    if status not in STATUS_MEMBER:
        return row
    role = telegram_status_to_role(status)
    if row:
        row.role = role
        return row
    row = GroupMember(group_id=group.id, user_id=user.id, role=role)
    session.add(row)
    await session.flush()
    return row


async def ingest_my_chat_member(session: AsyncSession, settings: Settings, payload: dict[str, Any]) -> Group | None:
    chat = payload.get("chat") or {}
    new_member = payload.get("new_chat_member") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    try:
        chat_id = int(chat_id)
    except (TypeError, ValueError):
        return None
    status = str(new_member.get("status") or "")
    title = chat.get("title") or chat.get("username") or ""
    return await upsert_real_group(session, chat_id, title=str(title), bot_status=status)


async def verify_membership_via_api(
    session: AsyncSession,
    settings: Settings,
    actor: Actor,
    chat_id: int,
) -> Optional[GroupMember]:
    """Hints never grant access; only a live or injected getChatMember result does."""
    if actor.is_demo or actor.telegram_id in FIXTURE_TELEGRAM_IDS:
        return None
    payload = None
    if _get_chat_member_impl is not None:
        payload = await _maybe_await(_get_chat_member_impl(chat_id, actor.telegram_id))
    elif settings.outbound_live:
        from hetai.bot.runtime import get_bot

        bot = get_bot()
        if bot is None:
            return None
        try:
            payload = await bot.get_chat_member(chat_id, actor.telegram_id)
        except Exception:
            return None
    else:
        return None
    status = _payload_status(payload)
    if not status:
        return None
    title = ""
    chat = getattr(payload, "chat", None)
    if isinstance(payload, dict):
        title = str((payload.get("chat") or {}).get("title") or "")
    elif chat is not None:
        title = str(getattr(chat, "title", "") or "")
    group = await upsert_real_group(session, int(chat_id), title=title)
    user = await session.get(User, actor.user_id)
    if user is None or user.is_demo_fixture:
        return None
    return await apply_membership(session, group, user, status)


async def _maybe_await(value):
    if hasattr(value, "__await__"):
        return await value
    return value
