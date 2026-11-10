"""群积分账本：原子扣减、幂等、租户边界。与信用账本完全分离。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hetai.models import GroupMember, PointAccount, PointLedger
from hetai.services.errors import InsufficientPointsError, NotFoundError, TenantError


def _maybe_lock(session: AsyncSession, stmt):
    bind = session.get_bind()
    dialect = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect == "sqlite":
        return stmt
    return stmt.with_for_update()


async def _require_membership(session: AsyncSession, group_id: int, user_id: int) -> GroupMember:
    res = await session.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
    )
    member = res.scalar_one_or_none()
    if not member:
        raise TenantError()
    return member


async def _get_or_create_account(
    session: AsyncSession, group_id: int, user_id: int
) -> PointAccount:
    res = await session.execute(
        _maybe_lock(session, select(PointAccount).where(PointAccount.group_id == group_id, PointAccount.user_id == user_id))
    )
    account = res.scalar_one_or_none()
    if account:
        return account
    account = PointAccount(group_id=group_id, user_id=user_id, balance=0)
    session.add(account)
    await session.flush()
    # re-lock
    res = await session.execute(
        _maybe_lock(session, select(PointAccount).where(PointAccount.group_id == group_id, PointAccount.user_id == user_id))
    )
    return res.scalar_one()


async def apply_points(
    session: AsyncSession,
    *,
    group_id: int,
    user_id: int,
    delta: int,
    reason: str,
    idempotency_key: str | None = None,
) -> dict:
    if delta == 0:
        raise ValueError("delta 不能为 0")
    await _require_membership(session, group_id, user_id)
    if idempotency_key:
        existing = await session.execute(
            select(PointLedger).where(PointLedger.idempotency_key == idempotency_key)
        )
        row = existing.scalar_one_or_none()
        if row:
            if row.group_id != group_id or row.user_id != user_id or row.delta != delta:
                raise ValueError("幂等键已被不同操作使用")
            return {
                "replayed": True,
                "ledger_id": row.id,
                "balance": row.balance_after,
                "delta": row.delta,
            }
    account = await _get_or_create_account(session, group_id, user_id)
    new_balance = int(account.balance) + int(delta)
    if new_balance < 0:
        raise InsufficientPointsError(current=int(account.balance), requested=abs(int(delta)))
    account.balance = new_balance
    ledger = PointLedger(
        group_id=group_id,
        user_id=user_id,
        delta=int(delta),
        balance_after=new_balance,
        reason=(reason or "")[:256],
        idempotency_key=idempotency_key or None,
    )
    session.add(ledger)
    await session.flush()
    return {
        "replayed": False,
        "ledger_id": ledger.id,
        "balance": new_balance,
        "delta": int(delta),
    }


async def list_group_accounts(session: AsyncSession, group_id: int) -> list[PointAccount]:
    res = await session.execute(
        select(PointAccount)
        .where(PointAccount.group_id == group_id)
        .order_by(PointAccount.user_id)
    )
    return list(res.scalars().all())


async def list_group_ledger(session: AsyncSession, group_id: int, limit: int = 50) -> list[PointLedger]:
    res = await session.execute(
        select(PointLedger)
        .where(PointLedger.group_id == group_id)
        .order_by(PointLedger.id.desc())
        .limit(limit)
    )
    return list(res.scalars().all())
