"""Per-request ban check with a short in-process cache."""
from __future__ import annotations

import logging
import time
from typing import Dict, Tuple

from sqlalchemy import select

logger = logging.getLogger(__name__)

_TTL = 30.0
_CACHE: Dict[int, Tuple[bool, float]] = {}


def invalidate(user_id: int | None = None) -> None:
    if user_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(int(user_id), None)


async def is_banned(user_id: int) -> bool:
    now = time.monotonic()
    hit = _CACHE.get(user_id)
    if hit and hit[1] > now:
        return hit[0]
    from bot.db import session_scope
    from bot.models import User
    try:
        async with session_scope() as s:
            res = await s.execute(select(User.is_banned).where(User.user_id == user_id))
            banned = bool(res.scalar_one_or_none())
    except Exception:
        # Fail open on DB hiccups so a transient error doesn't lock everyone out.
        logger.warning("ban check failed for user", exc_info=True)
        return False
    if len(_CACHE) > 20000:
        _CACHE.clear()
    _CACHE[user_id] = (banned, now + _TTL)
    return banned
