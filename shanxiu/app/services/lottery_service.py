"""
app/services/lottery_service.py
--------------------------------
Pure-function layer for lottery (抽奖) draw logic.

None of the functions here touch the database or the Telegram API, which
makes them cheap to unit-test without any mocking.
"""

from __future__ import annotations

import random
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

# A participant is (user_id, weight) where weight > 0.
WeightedParticipant = Tuple[int, int]


# ---------------------------------------------------------------------------
# Pure draw helpers
# ---------------------------------------------------------------------------

def pick_winners_random(pool: List[int], count: int) -> List[int]:
    """Select *count* unique winners from *pool* using uniform random sampling.

    Args:
        pool:  List of user IDs eligible to win.
        count: Number of winners to select.

    Returns:
        A list of up to ``min(count, len(pool))`` unique winner IDs.  The
        result order is not guaranteed.
    """
    if not pool or count <= 0:
        return []
    actual = min(count, len(pool))
    return random.sample(pool, actual)


def pick_winner_weighted(participants: List[WeightedParticipant]) -> int | None:
    """Select a single winner using weighted-random selection.

    Args:
        participants: List of ``(user_id, weight)`` tuples.  Weight must be > 0.

    Returns:
        The winning ``user_id``, or ``None`` if *participants* is empty.
    """
    if not participants:
        return None

    total = sum(w for _, w in participants)
    if total <= 0:
        # Fall back to uniform selection
        return random.choice(participants)[0]

    rand_val = random.uniform(0, total)
    cumulative = 0
    for user_id, weight in participants:
        cumulative += weight
        if cumulative >= rand_val:
            return user_id

    # Floating-point edge case: return the last participant
    return participants[-1][0]


def pick_winners_top_n(participants: List[WeightedParticipant], count: int) -> List[int]:
    """Select the top-*count* participants by descending weight.

    Ties are broken by the natural ordering of ``user_id`` (ascending) for
    determinism.

    Args:
        participants: List of ``(user_id, weight)`` tuples.
        count:        Maximum number of winners to return.

    Returns:
        A list of at most *count* user IDs sorted by descending weight.
    """
    if not participants or count <= 0:
        return []
    # Sort descending by weight, then ascending by user_id to break ties
    sorted_p = sorted(participants, key=lambda x: (-x[1], x[0]))
    return [uid for uid, _ in sorted_p[:count]]


def validate_lottery_draw(
    lottery_type: str,
    participants: List[WeightedParticipant],
    top_n_winners: int = 1,
) -> Tuple[bool, str]:
    """Validate that a lottery draw can proceed.

    Args:
        lottery_type:  ``'message_count'``, ``'message_rank'``, or ``'random'``.
        participants:  Eligible participant list.
        top_n_winners: Required winner count (for ``message_rank`` / ``random``).

    Returns:
        ``(True, '')`` if the draw is valid, or ``(False, reason)`` otherwise.

    Note:
        ``random`` allows an empty *participants* list at validate time; the
        caller is expected to supply ``fallback_pool`` (or a non-empty
        participants list) when calling :func:`run_lottery_draw`.
    """
    if lottery_type not in ('message_count', 'message_rank', 'random'):
        return False, f"未知的抽奖类型: {lottery_type}"
    if lottery_type == 'random':
        if top_n_winners < 1:
            return False, f"获奖人数必须 >= 1，当前: {top_n_winners}"
        return True, ''
    if not participants:
        return False, "没有符合条件的参与者"
    if lottery_type == 'message_rank' and top_n_winners < 1:
        return False, f"获奖人数必须 >= 1，当前: {top_n_winners}"
    return True, ''


def run_lottery_draw(
    lottery_type: str,
    participants: List[WeightedParticipant],
    top_n_winners: int = 1,
    fallback_pool: List[int] | None = None,
) -> List[int]:
    """Execute a lottery draw and return the winner ID list.

    Args:
        lottery_type:   ``'message_count'``, ``'message_rank'``, or ``'random'``.
        participants:   ``(user_id, message_count)`` tuples.
        top_n_winners:  Number of top winners (used for ``message_rank`` /
                        ``random``).
        fallback_pool:  Plain list of user IDs used when *participants* is
                        empty (backward-compatibility fallback). For
                        ``random``, also used when participants is empty.

    Returns:
        List of winner user IDs (may be empty if no eligible participants
        and *fallback_pool* is also empty).
    """
    if lottery_type == 'random':
        pool = [uid for uid, _ in participants] if participants else (fallback_pool or [])
        return pick_winners_random(pool, top_n_winners)

    if participants:
        if lottery_type == 'message_count':
            winner = pick_winner_weighted(participants)
            return [winner] if winner is not None else []
        else:  # message_rank
            return pick_winners_top_n(participants, top_n_winners)
    else:
        # Fallback when nobody sent messages during the lottery period
        if fallback_pool:
            count = 1 if lottery_type == 'message_count' else top_n_winners
            return pick_winners_random(fallback_pool, count)
        return []
