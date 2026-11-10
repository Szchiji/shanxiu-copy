"""写入明确标注为「示例数据」的演示夹具。仅用于演示模式。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hetai.models import (
    CreditAccount,
    CreditLedger,
    Group,
    GroupMember,
    PointAccount,
    PointLedger,
    Submission,
    TaskConfig,
    User,
)


async def ensure_task_templates(session: AsyncSession) -> None:
    templates = [
        ("ledger_integrity", "账本巡检", "核对积分与信用余额与流水一致性。不发送通知。", 3600),
        ("submission_aging", "待审投稿统计", "刷新待审数量到任务状态。不发送通知。", 600),
        ("session_placeholder", "匿名会话（未实现）", "已推迟，运行时只记录“未实现”。", 86400),
    ]
    for key, name, desc, interval in templates:
        res = await session.execute(select(TaskConfig).where(TaskConfig.key == key))
        if res.scalar_one_or_none():
            continue
        session.add(
            TaskConfig(
                key=key,
                name=name,
                description=desc,
                enabled=(key != "session_placeholder"),
                interval_seconds=interval,
                last_status="never",
                last_detail="尚未运行；本版本不发送通知",
            )
        )
    await session.flush()


async def seed_demo_if_empty(session: AsyncSession) -> dict:
    count = await session.scalar(select(func.count()).select_from(User))
    await ensure_task_templates(session)
    if count and count > 0:
        return {"seeded": False, "reason": "already-has-users"}

    admin = User(
        telegram_id=900001,
        username="demo_admin",
        display_name="陈管理",
        role="super",
        is_demo_fixture=True,
    )
    operator = User(
        telegram_id=900002,
        username="demo_ops",
        display_name="李运营",
        role="operator",
        is_demo_fixture=True,
    )
    member = User(
        telegram_id=900003,
        username="demo_member",
        display_name="王成员",
        role="member",
        is_demo_fixture=True,
    )
    guest = User(
        telegram_id=900004,
        username="demo_guest",
        display_name="赵访客",
        role="member",
        is_demo_fixture=True,
    )
    session.add_all([admin, operator, member, guest])
    await session.flush()

    g1 = Group(
        chat_id=-100900001,
        title="示例群 · 华东运营",
        description="示例数据：用于演示群设置、成员与积分租户隔离。",
        settings={"points_enabled": True, "directory_enabled": True},
        is_demo_fixture=True,
    )
    g2 = Group(
        chat_id=-100900002,
        title="示例群 · 测试交流",
        description="示例数据：与华东运营群的积分账本相互隔离。",
        settings={"points_enabled": True, "directory_enabled": True},
        is_demo_fixture=True,
    )
    session.add_all([g1, g2])
    await session.flush()

    session.add_all(
        [
            GroupMember(group_id=g1.id, user_id=admin.id, role="owner"),
            GroupMember(group_id=g1.id, user_id=operator.id, role="admin"),
            GroupMember(group_id=g1.id, user_id=member.id, role="member"),
            GroupMember(group_id=g2.id, user_id=admin.id, role="owner"),
            GroupMember(group_id=g2.id, user_id=guest.id, role="member"),
        ]
    )
    session.add_all(
        [
            CreditAccount(user_id=admin.id, balance=820),
            CreditAccount(user_id=operator.id, balance=610),
            CreditAccount(user_id=member.id, balance=430),
            CreditAccount(user_id=guest.id, balance=200),
        ]
    )
    session.add_all(
        [
            CreditLedger(
                user_id=member.id,
                delta=430,
                balance_after=430,
                reason="示例数据：初始信用",
                idempotency_key="demo-credit-member-init",
            ),
            PointAccount(group_id=g1.id, user_id=member.id, balance=120),
            PointAccount(group_id=g1.id, user_id=operator.id, balance=50),
            PointAccount(group_id=g2.id, user_id=guest.id, balance=15),
            PointLedger(
                group_id=g1.id,
                user_id=member.id,
                delta=120,
                balance_after=120,
                reason="示例数据：签到累计",
                idempotency_key="demo-point-g1-member-init",
            ),
            PointLedger(
                group_id=g2.id,
                user_id=guest.id,
                delta=15,
                balance_after=15,
                reason="示例数据：另一群余额，与华东运营隔离",
                idempotency_key="demo-point-g2-guest-init",
            ),
        ]
    )
    session.add_all(
        [
            Submission(
                group_id=g1.id,
                author_id=member.id,
                title="滨江晚茶摊",
                body="示例数据：一份待审核的目录投稿，描述晚茶摊位置与营业时间。",
                status="pending",
                is_demo_fixture=True,
            ),
            Submission(
                group_id=g1.id,
                author_id=operator.id,
                title="仓库咖啡角",
                body="示例数据：已通过的目录条目，会出现在应用信息流。",
                status="approved",
                review_note="示例数据：内容完整，已通过。",
                reviewed_by=admin.id,
                is_demo_fixture=True,
            ),
            Submission(
                group_id=g2.id,
                author_id=guest.id,
                title="重复广告",
                body="示例数据：已拒绝的投稿。",
                status="rejected",
                review_note="示例数据：判定为广告。",
                reviewed_by=admin.id,
                is_demo_fixture=True,
            ),
        ]
    )
    await session.flush()
    return {"seeded": True, "users": 4, "groups": 2, "label": "示例数据"}
