"""匿名月影会话：创建、中转映射、过期与结算、消息落库。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, or_, select, update

from bot.config import get_settings
from bot.db import session_scope
from bot.models import Session, SessionMessage, SessionStatus

logger = logging.getLogger(__name__)


def _to_dict(row: Session) -> Dict[str, Any]:
    return {
        "session_id": row.session_id,
        "lamp_id": row.lamp_id,
        "user_a_id": row.user_a_id,
        "user_b_id": row.user_b_id,
        "anonymous_a": row.anonymous_a,
        "anonymous_b": row.anonymous_b,
        "status": row.status,
        "message_count": row.message_count,
        "media_count": row.media_count,
        "has_praise": row.has_praise,
        "reported": row.reported,
        "created_at": row.created_at,
        "expire_at": row.expire_at,
        "last_activity": row.last_activity,
        "ended_at": row.ended_at,
        "messages_purge_at": row.messages_purge_at,
        "quality_score": row.quality_score,
    }


def _msg_to_dict(row: SessionMessage) -> Dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "from_role": row.from_role,
        "content": row.content,
        "media_type": row.media_type,
        "file_id": row.file_id,
        "created_at": row.created_at,
    }


def role_for_user(session: Dict[str, Any], user_id: int) -> str:
    if session["user_a_id"] == user_id:
        return "A"
    return "B"


def _guest_alias(session_id: str) -> str:
    """稳定代称：客人 + 短码（无真实身份）。"""
    code = (session_id or "xxxx").replace("-", "")[:2].upper()
    return f"客人{code}"


def _teacher_alias(lamp_title: Optional[str]) -> str:
    title = (lamp_title or "").strip()
    if not title:
        return "老师"
    # 称呼过长时截断，避免前缀刷屏
    return title[:16]


async def create_request(
    lamp_id: str,
    user_a_id: int,
    user_b_id: int,
    *,
    lamp_title: Optional[str] = None,
    guest_alias: Optional[str] = None,
) -> Dict[str, Any]:
    sid = str(uuid.uuid4())
    anon_a = (guest_alias or "").strip()[:32] or _guest_alias(sid)
    anon_b = _teacher_alias(lamp_title)
    async with session_scope() as s:
        row = Session(
            session_id=sid,
            lamp_id=lamp_id,
            user_a_id=user_a_id,
            user_b_id=user_b_id,
            anonymous_a=anon_a,
            anonymous_b=anon_b,
            status=SessionStatus.PENDING.value,
            expire_at=datetime.utcnow() + timedelta(hours=24),
            last_activity=datetime.utcnow(),
        )
        s.add(row)
        await s.flush()
        return _to_dict(row)


async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(select(Session).where(Session.session_id == session_id))
        row = res.scalar_one_or_none()
        return _to_dict(row) if row else None


async def get_active_for_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(
            select(Session)
            .where(
                Session.status == SessionStatus.ACTIVE.value,
                or_(Session.user_a_id == user_id, Session.user_b_id == user_id),
            )
            .order_by(Session.last_activity.desc())
            .limit(1)
        )
        row = res.scalar_one_or_none()
        return _to_dict(row) if row else None


async def accept(session_id: str) -> Optional[Dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(select(Session).where(Session.session_id == session_id))
        row = res.scalar_one_or_none()
        if not row or row.status != SessionStatus.PENDING.value:
            return None
        row.status = SessionStatus.ACTIVE.value
        row.last_activity = datetime.utcnow()
        row.expire_at = datetime.utcnow() + timedelta(hours=24)
        await s.flush()
        return _to_dict(row)


async def reject(session_id: str) -> None:
    async with session_scope() as s:
        res = await s.execute(select(Session).where(Session.session_id == session_id))
        row = res.scalar_one_or_none()
        if row and row.status == SessionStatus.PENDING.value:
            row.status = SessionStatus.ENDED.value
            row.ended_at = datetime.utcnow()
            hours = get_settings().message_retention_hours
            row.messages_purge_at = row.ended_at + timedelta(hours=hours)


async def bump_activity(session_id: str, *, media: bool = False) -> None:
    async with session_scope() as s:
        res = await s.execute(select(Session).where(Session.session_id == session_id))
        row = res.scalar_one_or_none()
        if not row:
            return
        row.message_count = int(row.message_count or 0) + 1
        if media:
            row.media_count = int(row.media_count or 0) + 1
        row.last_activity = datetime.utcnow()


async def save_message(
    session_id: str,
    from_role: str,
    *,
    content: Optional[str] = None,
    media_type: Optional[str] = None,
    file_id: Optional[str] = None,
) -> None:
    """中转时持久化完整消息正文 / 媒体元数据。"""
    async with session_scope() as s:
        s.add(
            SessionMessage(
                session_id=session_id,
                from_role=(from_role or "?")[:8],
                content=content,
                media_type=(media_type or None),
                file_id=(file_id or None),
            )
        )


async def list_messages_for_admin(
    session_id: str, *, limit: int = 200
) -> List[Dict[str, Any]]:
    """管理员可读：按时间列出某会话消息（调用方需校验 ADMIN_IDS）。"""
    async with session_scope() as s:
        res = await s.execute(
            select(SessionMessage)
            .where(SessionMessage.session_id == session_id)
            .order_by(SessionMessage.created_at.asc(), SessionMessage.id.asc())
            .limit(max(1, min(limit, 1000)))
        )
        rows = res.scalars().all()
        return [_msg_to_dict(r) for r in rows]


async def reply_balance_for_session(session_id: str) -> Optional[float]:
    """
    A/B 消息条数均衡度 = min(A,B)/max(A,B) ∈ [0,1]。
    无落库消息时返回 None（启发式 Q 将用中性默认）。
    """
    async with session_scope() as s:
        res = await s.execute(
            select(SessionMessage.from_role, func.count())
            .where(SessionMessage.session_id == session_id)
            .group_by(SessionMessage.from_role)
        )
        counts = {str(row[0]): int(row[1]) for row in res.all()}
    a = int(counts.get("A") or 0)
    b = int(counts.get("B") or 0)
    if a + b <= 0:
        return None
    return float(min(a, b)) / float(max(a, b))


async def purge_expired_messages() -> int:
    """删除已到 messages_purge_at 的会话消息行（先清空正文再删行）。"""
    now = datetime.utcnow()
    async with session_scope() as s:
        res = await s.execute(
            select(Session.session_id).where(
                Session.status == SessionStatus.ENDED.value,
                Session.messages_purge_at.is_not(None),
                Session.messages_purge_at <= now,
            )
        )
        sids = [row[0] for row in res.all()]
        if not sids:
            return 0

        await s.execute(
            update(SessionMessage)
            .where(SessionMessage.session_id.in_(sids))
            .values(content=None, file_id=None)
        )
        result = await s.execute(
            delete(SessionMessage).where(SessionMessage.session_id.in_(sids))
        )
        await s.execute(
            update(Session)
            .where(Session.session_id.in_(sids))
            .values(messages_purge_at=None)
        )
        deleted = int(result.rowcount or 0)
        logger.info("Purged %s session messages for %s sessions", deleted, len(sids))
        return deleted


async def mark_praise(session_id: str) -> None:
    async with session_scope() as s:
        res = await s.execute(select(Session).where(Session.session_id == session_id))
        row = res.scalar_one_or_none()
        if row:
            row.has_praise = True


async def mark_reported(session_id: str) -> None:
    async with session_scope() as s:
        res = await s.execute(select(Session).where(Session.session_id == session_id))
        row = res.scalar_one_or_none()
        if row:
            row.reported = True


async def end_session(session_id: str, settle: bool = True) -> Optional[Dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(select(Session).where(Session.session_id == session_id))
        row = res.scalar_one_or_none()
        if not row or row.status == SessionStatus.ENDED.value:
            return None
        row.status = SessionStatus.ENDED.value
        row.ended_at = datetime.utcnow()
        hours = get_settings().message_retention_hours
        row.messages_purge_at = row.ended_at + timedelta(hours=hours)
        data = _to_dict(row)

    data["settle_delta"] = 0
    return data


async def expire_old_sessions() -> int:
    """超时结束：先查出 id，再走 end_session 统一收尾。"""
    now = datetime.utcnow()
    async with session_scope() as s:
        res = await s.execute(
            select(Session.session_id).where(
                Session.status.in_([SessionStatus.PENDING.value, SessionStatus.ACTIVE.value]),
                Session.expire_at.is_not(None),
                Session.expire_at < now,
            )
        )
        ids = [row[0] for row in res.all()]

    count = 0
    for sid in ids:
        data = await end_session(sid, settle=True)
        if data is not None:
            count += 1
    return count


def peer_id(session: Dict[str, Any], user_id: int) -> int:
    if session["user_a_id"] == user_id:
        return session["user_b_id"]
    return session["user_a_id"]


def anon_name(session: Dict[str, Any], user_id: int) -> str:
    """中转前缀代称：老师侧用资料称呼；客人侧用客人短码。不含 @username / 真名。"""
    if session["user_a_id"] == user_id:
        return (session.get("anonymous_a") or "").strip() or _guest_alias(session.get("session_id") or "")
    return (session.get("anonymous_b") or "").strip() or "老师"
