import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hetai.models import Base, Group, GroupMember, User
from hetai.services.credit import apply_credit
from hetai.services.errors import InsufficientPointsError, TenantError
from hetai.services.points import apply_points


@pytest.fixture
async def session(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'ledgers.sqlite'}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_points_tenant_idempotent_and_atomic(session):
    u1 = User(telegram_id=1, display_name="A", role="member")
    u2 = User(telegram_id=2, display_name="B", role="member")
    g1 = Group(chat_id=-1, title="G1")
    g2 = Group(chat_id=-2, title="G2")
    session.add_all([u1, u2, g1, g2])
    await session.flush()
    session.add_all(
        [
            GroupMember(group_id=g1.id, user_id=u1.id, role="member"),
            GroupMember(group_id=g2.id, user_id=u2.id, role="member"),
        ]
    )
    await session.flush()
    first = await apply_points(session, group_id=g1.id, user_id=u1.id, delta=10, reason="award", idempotency_key="k1")
    replay = await apply_points(session, group_id=g1.id, user_id=u1.id, delta=10, reason="award", idempotency_key="k1")
    assert first["replayed"] is False and first["balance"] == 10
    assert replay["replayed"] is True and replay["balance"] == 10
    with pytest.raises(InsufficientPointsError):
        await apply_points(session, group_id=g1.id, user_id=u1.id, delta=-50, reason="over")
    with pytest.raises(TenantError):
        await apply_points(session, group_id=g2.id, user_id=u1.id, delta=1, reason="cross")
    # u2 in g2 cannot be used to debit g1
    with pytest.raises(TenantError):
        await apply_points(session, group_id=g1.id, user_id=u2.id, delta=-1, reason="cross2")


@pytest.mark.asyncio
async def test_credit_separate_from_points(session):
    u1 = User(telegram_id=11, display_name="C", role="member")
    g1 = Group(chat_id=-11, title="G")
    session.add_all([u1, g1])
    await session.flush()
    session.add(GroupMember(group_id=g1.id, user_id=u1.id, role="member"))
    await session.flush()
    await apply_points(session, group_id=g1.id, user_id=u1.id, delta=40, reason="pts")
    credit = await apply_credit(session, user_id=u1.id, delta=20, reason="cr", idempotency_key="c1")
    replay = await apply_credit(session, user_id=u1.id, delta=20, reason="cr", idempotency_key="c1")
    assert credit["balance"] == 20
    assert replay["replayed"] is True
    # points unchanged by credit
    from sqlalchemy import select
    from hetai.models import PointAccount, CreditAccount

    pts = (await session.execute(select(PointAccount).where(PointAccount.user_id == u1.id))).scalar_one()
    cr = (await session.execute(select(CreditAccount).where(CreditAccount.user_id == u1.id))).scalar_one()
    assert pts.balance == 40
    assert cr.balance == 20
