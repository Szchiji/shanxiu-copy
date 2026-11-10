"""全局信用账本，与群积分分离，禁止跨账本对冲。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hetai.models import CreditAccount, CreditLedger, User
from hetai.services.errors import NotFoundError


def _maybe_lock(session: AsyncSession, stmt):
    bind = session.get_bind()
    dialect = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect == "sqlite":
        return stmt
    return stmt.with_for_update()

CREDIT_MIN = 0
CREDIT_MAX = 1000


async def _get_or_create_account(session: AsyncSession, user_id: int) -> CreditAccount:
    user = await session.get(User, user_id)
    if not user:
        raise NotFoundError("用户不存在")
    res = await session.execute(
        _maybe_lock(session, select(CreditAccount).where(CreditAccount.user_id == user_id))
    )
    account = res.scalar_one_or_none()
    if account:
        return account
    account = CreditAccount(user_id=user_id, balance=0)
    session.add(account)
    await session.flush()
    res = await session.execute(
        _maybe_lock(session, select(CreditAccount).where(CreditAccount.user_id == user_id))
    )
    return res.scalar_one()


async def apply_credit(
    session: AsyncSession,
    *,
    user_id: int,
    delta: int,
    reason: str,
    idempotency_key: str | None = None,
) -> dict:
    if delta == 0:
        raise ValueError("delta 不能为 0")
    if idempotency_key:
        existing = await session.execute(
            select(CreditLedger).where(CreditLedger.idempotency_key == idempotency_key)
        )
        row = existing.scalar_one_or_none()
        if row:
            if row.user_id != user_id or row.delta != delta:
                raise ValueError("幂等键已被不同操作使用")
            return {
                "replayed": True,
                "ledger_id": row.id,
                "balance": row.balance_after,
                "delta": row.delta,
            }
    account = await _get_or_create_account(session, user_id)
    new_balance = max(CREDIT_MIN, min(CREDIT_MAX, int(account.balance) + int(delta)))
    # if clamp would ignore a debit/credit entirely past bounds, still record applied delta
    applied = new_balance - int(account.balance)
    if applied == 0:
        # out of bounds no-op still stored only if we consider it a change request
        raise ValueError("调整后余额未变化（已达上下限）")
    account.balance = new_balance
    ledger = CreditLedger(
        user_id=user_id,
        delta=applied,
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
        "delta": applied,
    }


async def list_credit_ledger(session: AsyncSession, user_id: int, limit: int = 50) -> list[CreditLedger]:
    res = await session.execute(
        select(CreditLedger)
        .where(CreditLedger.user_id == user_id)
        .order_by(CreditLedger.id.desc())
        .limit(limit)
    )
    return list(res.scalars().all())
