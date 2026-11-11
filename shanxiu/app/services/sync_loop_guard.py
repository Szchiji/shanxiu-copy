"""Precise anti-loop guard for sync / forward rules.

Instead of skipping every message our own bot authored, we remember exactly which
messages were *produced by* sync / forward-rule delivery: ``(target_chat_id, message_id)``.
When a message is later heard in a source chat (Bot API update or userbot), it is skipped
only if it is such a product. Ordinary messages our bot sends (scheduled posts, auto
replies, admin broadcasts …) are synced like anyone else's.

* Registration happens on the sending call itself (:func:`track_bot` wraps the Bot), so
  header lines, re-uploads, albums and per-message fallbacks are all covered.
* In-memory TTL registry (process-wide, thread-safe — the userbot runs on its own thread);
  ``SyncMessageLog.target_message_id`` is a DB fallback that survives restarts.
* 同步日志保留时长 (sync_log_retention, ≥ 1 h, default 1 day) only deletes old rows; an echo
  arrives seconds after the send, so the DB fallback is always within the retention window.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict
from typing import Any, Iterable, Optional

TTL_SECONDS = 2 * 24 * 3600
MAX_ITEMS = 100_000
# Userbot may hear our output before the Bot API response is parsed; wait this long before checking.
USERBOT_RECHECK_DELAY = 2.5

import contextvars

# True while a sync / forward-rule delivery is sending (TrackingBot) → own-post hook ignores it.
IN_SYNC_DELIVERY: contextvars.ContextVar = contextvars.ContextVar('in_sync_delivery', default=False)

_lock = threading.Lock()
_registry: "OrderedDict[tuple, float]" = OrderedDict()

SEND_METHODS = frozenset({
    'send_message', 'copy_message', 'copy_messages', 'forward_message', 'forward_messages',
    'send_media_group', 'send_photo', 'send_video', 'send_animation', 'send_audio', 'send_voice',
    'send_video_note', 'send_sticker', 'send_document', 'send_poll', 'send_location', 'send_venue',
    'send_contact', 'send_dice',
})


def _key(chat_id: Any, message_id: Any) -> Optional[tuple]:
    try:
        return (str(int(chat_id)), int(message_id))
    except (TypeError, ValueError):
        return None


def _ids_of(result) -> list:
    if result is None or result is True or result is False:
        return []
    if isinstance(result, (list, tuple)):
        out = []
        for r in result:
            out.extend(_ids_of(r))
        return out
    if isinstance(result, int):
        return [result]
    mid = getattr(result, 'message_id', None)
    return [mid] if isinstance(mid, int) else []


def _gc_locked(now: float):
    while _registry:
        k, ts = next(iter(_registry.items()))
        if now - ts > TTL_SECONDS or len(_registry) > MAX_ITEMS:
            _registry.popitem(last=False)
        else:
            break


def mark_sync_output(chat_id: Any, result_or_ids: Any) -> int:
    """Record message(s) we just produced in *chat_id*. Accepts Message / MessageId / list / int."""
    ids = _ids_of(result_or_ids)
    now = time.monotonic()
    n = 0
    with _lock:
        for mid in ids:
            k = _key(chat_id, mid)
            if k is None:
                continue
            _registry[k] = now
            _registry.move_to_end(k)
            n += 1
        _gc_locked(now)
    return n


def is_sync_output_memory(chat_id: Any, message_id: Any) -> bool:
    k = _key(chat_id, message_id)
    if k is None:
        return False
    with _lock:
        return k in _registry


def is_sync_output_db(chat_id: Any, message_id: Any, flask_app=None) -> bool:
    """Fallback for outputs recorded before a restart (SyncMessageLog, last 2 days)."""
    try:
        from datetime import timedelta

        from app.models import SyncMessageLog
        from app.utils import get_beijing_now

        def _q():
            since = get_beijing_now() - timedelta(seconds=TTL_SECONDS)
            return SyncMessageLog.query.filter(
                SyncMessageLog.synced_at >= since,
                SyncMessageLog.target_group_id == str(chat_id),
                SyncMessageLog.target_message_id == int(message_id),
                SyncMessageLog.status == 'success',
            ).first() is not None

        if flask_app is not None:
            with flask_app.app_context():
                return _q()
        return _q()
    except Exception as e:
        print(f"[loop-guard] db check failed: {type(e).__name__}", flush=True)
        return False


def is_sync_output(chat_id: Any, message_id: Any, flask_app=None, use_db: bool = True) -> bool:
    if is_sync_output_memory(chat_id, message_id):
        return True
    return bool(use_db and is_sync_output_db(chat_id, message_id, flask_app))


async def wait_is_sync_output(chat_id: Any, message_id: Any, flask_app=None,
                              delay: float = USERBOT_RECHECK_DELAY) -> bool:
    """Check now; on a miss wait *delay* (send response may still be in flight) and re-check."""
    if is_sync_output_memory(chat_id, message_id):
        return True
    if delay > 0:
        await asyncio.sleep(delay)
    return is_sync_output(chat_id, message_id, flask_app)


def reset():  # tests
    with _lock:
        _registry.clear()


class TrackingBot:
    """Thin proxy: every send-like call records its result as a sync output for ``chat_id``."""

    __slots__ = ('_bot',)

    def __init__(self, bot):
        object.__setattr__(self, '_bot', bot)

    def __getattr__(self, name):
        attr = getattr(self._bot, name)
        if name not in SEND_METHODS or not callable(attr):
            return attr

        async def _tracked(*args, **kwargs):
            token = IN_SYNC_DELIVERY.set(True)
            try:
                result = await attr(*args, **kwargs)
            finally:
                IN_SYNC_DELIVERY.reset(token)
            chat_id = kwargs.get('chat_id', args[0] if args else None)
            try:
                mark_sync_output(chat_id, result)
            except Exception:
                pass
            return result

        return _tracked

    @property
    def unwrapped(self):
        return self._bot


def track_bot(bot):
    if bot is None or isinstance(bot, TrackingBot):
        return bot
    return TrackingBot(bot)


def filter_out_sync_outputs(chat_id: Any, messages: Iterable, flask_app=None) -> list:
    """Drop album parts that are our own sync outputs."""
    return [m for m in messages
            if not is_sync_output(chat_id, getattr(m, 'message_id', None), flask_app, use_db=False)]
