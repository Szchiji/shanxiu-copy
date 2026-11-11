"""Retry helper for Telegram Bot API calls (moved here from bot.main, PR #46).

· TelegramRetryAfter (flood control): wait retry_after + a small buffer.
· Transient errors — TelegramServerError (5xx / Bad Gateway), TelegramNetworkError, timeouts, aiohttp
  client errors: short backoff (1s, 3s, ...).
Up to ``attempts`` tries with a cap on the total wait; then the last error is raised. Anything else
(e.g. TelegramBadRequest "message to edit not found") is raised immediately. Never logs the request
itself (its URL carries the bot token).
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("yueying")

TG_RETRY_ATTEMPTS = 3
TG_RETRY_BUFFER = 1.5          # seconds on top of retry_after
TG_RETRY_MAX_WAIT = 90.0       # total seconds one call may spend waiting
TG_BACKOFF = (1.0, 3.0, 6.0)   # waits after transient failure 1, 2, 3 ...


def is_transient(exc: BaseException) -> bool:
    """Bad Gateway / other 5xx, network trouble, timeouts — worth trying again shortly."""
    try:
        from aiogram.exceptions import TelegramNetworkError, TelegramServerError
        if isinstance(exc, (TelegramServerError, TelegramNetworkError)):
            return True
    except Exception:
        pass
    try:
        import aiohttp
        if isinstance(exc, aiohttp.ClientError):
            return True
    except Exception:
        pass
    return isinstance(exc, (asyncio.TimeoutError, ConnectionError))


async def tg_call(label: str, make_call, *, attempts: int = TG_RETRY_ATTEMPTS, max_wait: float = TG_RETRY_MAX_WAIT,
                  transient: bool = True, sleep=asyncio.sleep):
    """Run ``await make_call()`` with retries (see module doc)."""
    from aiogram.exceptions import TelegramRetryAfter
    waited = 0.0
    for n in range(1, attempts + 1):
        try:
            return await make_call()
        except TelegramRetryAfter as exc:
            delay = float(getattr(exc, "retry_after", 1) or 1) + TG_RETRY_BUFFER
            kind = "flood control"
            if n >= attempts or waited + delay > max_wait:
                logger.warning("%s: Telegram flood control, giving up after %s attempt(s) (retry_after=%ss, waited %.0fs)",
                               label, n, getattr(exc, "retry_after", "?"), waited)
                raise
        except Exception as exc:
            if not (transient and is_transient(exc)):
                raise
            delay = TG_BACKOFF[min(n - 1, len(TG_BACKOFF) - 1)]
            kind = f"{type(exc).__name__}: {str(exc)[:80]}"
            if n >= attempts or waited + delay > max_wait:
                logger.warning("%s: transient Telegram error, giving up after %s attempt(s) (%s)", label, n, kind)
                raise
        logger.info("%s: %s, retry %s/%s in %.1fs", label, kind, n + 1, attempts, delay)
        waited += delay
        await sleep(delay)
