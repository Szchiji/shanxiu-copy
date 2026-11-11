"""用户档案：确保用户存在、读写角色与代称等身份字段。

兰花信用已停用：本模块不再结算分数、等级或遮蔽，也不再根据信用分做业务判定。
users / credit_history 等既有列和表仍保留在数据库中，仅作兼容残留，不再读写业务含义。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select

from bot.db import session_scope
from bot.models import User, UserRole

# Fields that belonged to the removed orchid-credit system. Kept on the row,
# but stripped from API responses.
CREDIT_USER_FIELDS = (
    "lanhua_score",
    "tier",
    "shadow_days",
    "shadow_reason",
    "recovery_progress",
    "total_earned",
    "total_deducted",
    "is_shadowed",
    "last_recovery_at",
)


def user_to_dict(u: User) -> Dict[str, Any]:
    return {
        "user_id": u.user_id,
        "username": u.username,
        "full_name": u.full_name,
        "role": getattr(u, "role", None),
        "guest_alias": getattr(u, "guest_alias", None),
        "is_banned": getattr(u, "is_banned", False),
        "ban_reason": getattr(u, "ban_reason", None),
        "created_at": u.created_at,
        "updated_at": u.updated_at,
    }


def public_user(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    for key in CREDIT_USER_FIELDS:
        out.pop(key, None)
    return out


async def ensure_user(user_id: int, username: str | None = None, full_name: str | None = None) -> Dict[str, Any]:
    async with session_scope() as s:
        res = await s.execute(select(User).where(User.user_id == user_id))
        user = res.scalar_one_or_none()
        if user:
            if username is not None:
                user.username = username
            if full_name is not None:
                user.full_name = full_name
            user.updated_at = datetime.utcnow()
            await s.flush()
            return user_to_dict(user)
        user = User(user_id=user_id, username=username, full_name=full_name)
        s.add(user)
        await s.flush()
        return user_to_dict(user)


async def set_user_role(user_id: int, role: str) -> Dict[str, Any]:
    allowed = {UserRole.TEACHER.value, UserRole.GUEST.value, UserRole.MERCHANT.value}
    if role not in allowed:
        raise ValueError("role 须为 teacher / guest / merchant")
    await ensure_user(user_id)
    async with session_scope() as s:
        res = await s.execute(select(User).where(User.user_id == user_id))
        user = res.scalar_one_or_none()
        if not user:
            raise RuntimeError("用户不存在")
        user.role = role
        user.updated_at = datetime.utcnow()
        await s.flush()
        return user_to_dict(user)


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(select(User).where(User.user_id == user_id))
        user = res.scalar_one_or_none()
        return user_to_dict(user) if user else None
