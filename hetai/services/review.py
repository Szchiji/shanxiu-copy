"""投稿审核：发布与可选积分奖励在同一事务中完成，幂等且不改动全局信用。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hetai.auth import Actor
from hetai.models import Group, GroupMember, PointAccount, PointLedger, ReviewEvent, Submission
from hetai.services.context import actor_can_admin_group
from hetai.services.errors import ForbiddenError, HetaiError, InsufficientPointsError, NotFoundError
from hetai.services.points import apply_points, _maybe_lock

REWARD_KEY = "publish-reward:submission:{id}"
REVERSAL_KEY = "publish-reward-reversal:submission:{id}"


def publish_reward_points(group: Optional[Group]) -> int:
    if not group:
        return 0
    settings = group.settings or {}
    if not settings.get("points_enabled", True):
        return 0
    try:
        value = int(settings.get("publish_reward_points") or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def publish_reward_label(group: Optional[Group]) -> str:
    if not group:
        return ""
    settings = group.settings or {}
    label = (settings.get("publish_reward_label") or "").strip()
    points = publish_reward_points(group)
    if label:
        return label
    if points:
        return f"通过目录投稿奖励 {points} 积分"
    return "通过目录投稿默认不奖励积分（规则未启用或奖励为 0）"


async def _lock_submission(session: AsyncSession, item_id: int) -> Optional[Submission]:
    res = await session.execute(_maybe_lock(session, select(Submission).where(Submission.id == item_id)))
    return res.scalar_one_or_none()


async def list_review_events(session: AsyncSession, submission_id: int) -> list[ReviewEvent]:
    res = await session.execute(
        select(ReviewEvent)
        .where(ReviewEvent.submission_id == submission_id)
        .order_by(ReviewEvent.id.asc())
    )
    return list(res.scalars().all())


async def _existing_reward(session: AsyncSession, submission_id: int) -> Optional[PointLedger]:
    res = await session.execute(
        select(PointLedger).where(PointLedger.idempotency_key == REWARD_KEY.format(id=submission_id))
    )
    return res.scalar_one_or_none()


async def _award_if_configured(session: AsyncSession, group: Group, user_id: int, submission_id: int) -> int:
    amount = publish_reward_points(group)
    if amount <= 0:
        return 0
    result = await apply_points(
        session,
        group_id=group.id,
        user_id=user_id,
        delta=amount,
        reason=publish_reward_label(group) or f"目录投稿通过奖励 {amount} 积分",
        idempotency_key=REWARD_KEY.format(id=submission_id),
    )
    if result.get("replayed"):
        return 0
    return int(result["delta"])


async def _clawback_if_needed(session: AsyncSession, group: Group, user_id: int, submission_id: int) -> int:
    ledger = await _existing_reward(session, submission_id)
    if not ledger or int(ledger.delta) <= 0:
        return 0
    # already reversed?
    existing = await session.execute(
        select(PointLedger).where(PointLedger.idempotency_key == REVERSAL_KEY.format(id=submission_id))
    )
    if existing.scalar_one_or_none():
        return 0
    acc = await session.scalar(
        select(PointAccount).where(PointAccount.group_id == group.id, PointAccount.user_id == user_id)
    )
    available = int(acc.balance) if acc else 0
    take = min(available, int(ledger.delta))
    if take <= 0:
        return 0
    result = await apply_points(
        session,
        group_id=group.id,
        user_id=user_id,
        delta=-take,
        reason=f"更正：撤销投稿通过奖励（原流水保留）",
        idempotency_key=REVERSAL_KEY.format(id=submission_id),
    )
    return int(result["delta"])


async def review_submission(
    session: AsyncSession,
    *,
    actor: Actor,
    membership: Optional[GroupMember],
    item_id: int,
    action: str,
    review_note: str = "",
) -> dict:
    if action not in {"approve", "reject"}:
        raise ValueError("无效操作")
    if not actor_can_admin_group(actor, membership):
        raise ForbiddenError("没有该群的审核权限")

    item = await _lock_submission(session, item_id)
    if not item:
        raise NotFoundError("投稿不存在")
    group = await session.get(Group, item.group_id)
    if not group:
        raise NotFoundError("群组不存在")
    if membership and membership.group_id != item.group_id and not actor.role == "super":
        raise ForbiddenError("没有该群的审核权限")
    if not membership and actor.role != "super":
        raise ForbiddenError("没有该群的审核权限")

    target = "approved" if action == "approve" else "rejected"
    note = (review_note or "").strip()[:256]
    previous = item.status

    if previous == target:
        events = await list_review_events(session, item.id)
        return {
            "replayed": True,
            "status": item.status,
            "reward_delta": 0,
            "item_id": item.id,
            "events": events,
        }

    reward_delta = 0
    if target == "approved":
        item.status = "approved"
        item.review_note = note
        item.reviewed_by = actor.user_id
        item.reviewed_at = datetime.utcnow()
        try:
            reward_delta = await _award_if_configured(session, group, item.author_id, item.id)
        except (HetaiError, ValueError) as exc:
            # fail closed: do not publish if the reward cannot be applied
            raise ValueError(f"无法完成奖励入账，审核未生效：{exc}") from exc
    else:
        item.status = "rejected"
        item.review_note = note
        item.reviewed_by = actor.user_id
        item.reviewed_at = datetime.utcnow()
        if previous == "approved":
            reward_delta = await _clawback_if_needed(session, group, item.author_id, item.id)

    event = ReviewEvent(
        submission_id=item.id,
        actor_id=actor.user_id,
        action=action,
        previous_status=previous,
        new_status=item.status,
        note=note,
        reward_delta=reward_delta,
        is_demo_fixture=bool(item.is_demo_fixture),
    )
    session.add(event)
    await session.flush()
    return {
        "replayed": False,
        "status": item.status,
        "reward_delta": reward_delta,
        "item_id": item.id,
        "event_id": event.id,
    }
