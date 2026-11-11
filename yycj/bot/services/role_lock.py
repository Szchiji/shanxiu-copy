"""角色一周锁定。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import text

from bot.config import get_settings
from bot.db import session_scope
from bot.services import users

logger = logging.getLogger(__name__)
LOCK_DAYS = 7


async def _ensure_col() -> None:
    try:
        async with session_scope() as s:
            await s.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS role_set_at TIMESTAMP"))
            await s.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS role_locked_until TIMESTAMP"))
    except Exception:
        logger.exception("ensure role lock columns failed")


async def attach_lock(user: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not user or not user.get("user_id"):
        return user
    try:
        async with session_scope() as s:
            row = (
                await s.execute(
                    text("SELECT role_set_at, role_locked_until FROM users WHERE user_id = :u"),
                    {"u": int(user["user_id"])},
                )
            ).first()
        if row:
            user["role_set_at"] = row[0]
            until = row[1]
            if until is None and row[0] is not None:
                until = row[0] + timedelta(days=LOCK_DAYS)
            user["role_locked_until"] = until
    except Exception:
        logger.exception("attach role lock failed")
    return user


async def set_user_role(user_id: int, role: str) -> Dict[str, Any]:
    await _ensure_col()
    role = (role or "").strip().lower()
    from bot.models import UserRole
    allowed = {UserRole.TEACHER.value, UserRole.GUEST.value, UserRole.MERCHANT.value}
    if role not in allowed:
        raise ValueError("请选择客人 / 老师 / 商家")
    await users.ensure_user(user_id)
    admin = get_settings().is_admin(user_id)
    now = datetime.utcnow()
    async with session_scope() as s:
        res = await s.execute(
            text("SELECT role, role_set_at, role_locked_until FROM users WHERE user_id = :u"),
            {"u": user_id},
        )
        row = res.first()
        current = (row[0] if row else None) or ""
        stamped = row[1] if row else None
        until = row[2] if row and len(row) > 2 else None
        lock_until = until or (stamped + timedelta(days=LOCK_DAYS) if stamped else None)
        if (not admin) and current and current != role and lock_until and lock_until > now:
            left = lock_until - now
            days = max(1, int((left.total_seconds() + 86399) // 86400))
            raise ValueError(f"角色一周内只能换一次，还剩 {days} 天")
        await s.execute(
            text(
                "UPDATE users SET role = :r, role_set_at = :t, role_locked_until = :u2, updated_at = :t WHERE user_id = :u"
            ),
            {"r": role, "t": now, "u2": now + timedelta(days=LOCK_DAYS), "u": user_id},
        )
    user = await users.get_user(user_id) or await users.ensure_user(user_id)
    user["role"] = role
    user["role_set_at"] = now
    user["role_locked_until"] = now + timedelta(days=LOCK_DAYS)
    return user


async def unlock_role(user_id: int) -> Dict[str, Any]:
    await _ensure_col()
    async with session_scope() as s:
        await s.execute(
            text("UPDATE users SET role_locked_until = NULL WHERE user_id = :u"),
            {"u": user_id},
        )
    user = await users.get_user(user_id) or await users.ensure_user(user_id)
    user["role_locked_until"] = None
    return user


_orig_ensure = users.ensure_user
_orig_get = users.get_user


async def ensure_user(user_id: int, username: str | None = None, full_name: str | None = None):
    user = await _orig_ensure(user_id, username=username, full_name=full_name)
    return await attach_lock(user)


async def get_user(user_id: int):
    user = await _orig_get(user_id)
    return await attach_lock(user)


users.set_user_role = set_user_role  # type: ignore[assignment]
users.ensure_user = ensure_user  # type: ignore[assignment]
users.get_user = get_user  # type: ignore[assignment]
