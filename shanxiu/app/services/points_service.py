"""
app/services/points_service.py
-------------------------------
Pure-function layer for points (积分) arithmetic.

These functions operate only on plain Python values; they never touch the
database or the Telegram API.  This makes them trivially unit-testable.

DB-aware helpers that orchestrate model lookups together with these pure
functions live in the same module for convenience but are clearly labelled.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InsufficientPointsError(ValueError):
    """Raised when a deduction would push the balance below zero."""

    def __init__(self, current: int, requested: int) -> None:
        self.current = current
        self.requested = requested
        super().__init__(
            f"积分不足：需要 {requested} 积分，当前余额 {current} 积分"
        )


# ---------------------------------------------------------------------------
# Pure arithmetic helpers
# ---------------------------------------------------------------------------

def award_points(current_balance: int, amount: int) -> int:
    """Return the new balance after awarding *amount* points.

    Args:
        current_balance: The user's current points balance (must be >= 0).
        amount: Points to add (must be > 0).

    Returns:
        Updated balance (current_balance + amount).

    Raises:
        ValueError: If *amount* is not positive or *current_balance* is negative.
    """
    if amount <= 0:
        raise ValueError(f"award_points: amount must be positive, got {amount}")
    if current_balance < 0:
        raise ValueError(f"award_points: current_balance must be >= 0, got {current_balance}")
    return current_balance + amount


def deduct_points(current_balance: int, amount: int) -> int:
    """Return the new balance after deducting *amount* points.

    Args:
        current_balance: The user's current points balance (must be >= 0).
        amount: Points to deduct (must be > 0).

    Returns:
        Updated balance (current_balance - amount).

    Raises:
        InsufficientPointsError: If *amount* exceeds *current_balance*.
        ValueError: If *amount* is not positive or *current_balance* is negative.
    """
    if amount <= 0:
        raise ValueError(f"deduct_points: amount must be positive, got {amount}")
    if current_balance < 0:
        raise ValueError(f"deduct_points: current_balance must be >= 0, got {current_balance}")
    if current_balance < amount:
        raise InsufficientPointsError(current=current_balance, requested=amount)
    return current_balance - amount


def apply_points_change(current_balance: int, change: int) -> int:
    """Apply a signed *change* to *current_balance*.

    Positive *change* awards points; negative *change* deducts them.

    Raises:
        InsufficientPointsError: For negative *change* that exceeds the balance.
    """
    if change > 0:
        return award_points(current_balance, change)
    if change < 0:
        return deduct_points(current_balance, -change)
    return current_balance  # change == 0, no-op


def can_earn_more_today(earned_today: int, max_daily_points: int | None) -> bool:
    """Return True if the user can still earn points today.

    Args:
        earned_today:      Points already earned today (must be >= 0).
        max_daily_points:  Daily cap; ``None`` means unlimited.

    Returns:
        ``True`` when the user has not yet hit the daily cap (or there is no cap).
    """
    if max_daily_points is None:
        return True
    if max_daily_points <= 0:
        return False
    return earned_today < max_daily_points


def clamp_award_to_daily_cap(amount: int, earned_today: int, max_daily_points: int | None) -> int:
    """Return the actual amount that can be awarded without exceeding the daily cap.

    Args:
        amount:            Intended award amount (must be > 0).
        earned_today:      Points already earned today (must be >= 0).
        max_daily_points:  Daily cap; ``None`` means unlimited.

    Returns:
        The actual amount to award, which may be less than *amount* or 0 if the
        cap has already been reached.
    """
    if max_daily_points is None:
        return amount
    remaining = max(0, max_daily_points - earned_today)
    return min(amount, remaining)


# ---------------------------------------------------------------------------
# DB-aware helpers (require Flask app context)
# ---------------------------------------------------------------------------

def get_or_create_user_points(db, UserPoints, group_id: int, user_id: int):
    """Fetch or create a UserPoints record inside the current app context.

    Returns the ``UserPoints`` ORM instance (not yet committed).
    """
    record = UserPoints.query.filter_by(
        group_id=group_id, user_id=user_id
    ).first()
    if record is None:
        record = UserPoints(group_id=group_id, user_id=user_id, points_balance=0)
        db.session.add(record)
    return record


def record_points_transaction(
    db,
    PointsLog,
    group_id: int,
    user_id: int,
    points_change: int,
    reason: str,
    balance_after: int,
):
    """Append a PointsLog entry inside the current app context.

    Does **not** call ``db.session.commit()``.  The caller is responsible for
    committing (or rolling back) the session.
    """
    entry = PointsLog(
        group_id=group_id,
        user_id=user_id,
        points_change=points_change,
        reason=reason,
        balance_after=balance_after,
    )
    db.session.add(entry)
    return entry
