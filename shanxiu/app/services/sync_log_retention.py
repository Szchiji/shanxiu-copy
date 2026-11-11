"""SyncMessageLog retention (同步日志保留时长).

Setting storage (SystemConfig key/value, same as other admin settings):
* ``sync_log_retention_hours:<group_id>`` — per source group (set on the 同步消息日志 page);
* fallback: env ``SYNC_LOG_RETENTION_HOURS`` (default 24).
``0`` means 永久保留 (never delete). Custom values are clamped to 1 h … 8760 h (1 year).

Cutoff semantics (Beijing time):
* day-aligned presets (24 / 72 / 168 / 720 …) use **calendar days**: ``1 天`` keeps
  only today (anything before today's 00:00 is deleted). ``3 天`` keeps today + the
  previous 2 calendar days. This matches the admin wording 「只留 N 天」and applies
  equally to group sync rows and userbot rows (``via='userbot'``).
* Custom hour values that are not multiples of 24 stay as a rolling window.

Loop guard (#302) compatibility: echoes arrive within seconds of the send. The
in-memory registry keeps outputs for 2 days. The DB fallback only needs rows a few
seconds old — always inside any retention window ≥ 1 h.
"""

from __future__ import annotations

import os
import threading
import time as time_mod
from datetime import timedelta
from typing import Optional

KEY_PREFIX = 'sync_log_retention_hours:'
MIN_HOURS = 1
MAX_HOURS = 24 * 365
BATCH_SIZE = 2000
MAX_BATCHES_PER_RUN = 500  # ≤ 1M rows per run; the next hourly run continues
PRESETS = [(24, '1 天'), (72, '3 天'), (168, '7 天'), (720, '30 天'), (0, '永久保留')]

_run_lock = threading.Lock()


def default_hours() -> int:
    try:
        return normalize_hours(int(float(os.getenv('SYNC_LOG_RETENTION_HOURS', '24'))))
    except (TypeError, ValueError):
        return 24


def normalize_hours(value) -> int:
    """0 = forever; otherwise clamp into [MIN_HOURS, MAX_HOURS]. Raises ValueError on junk."""
    h = int(float(value))
    if h <= 0:
        return 0
    return max(MIN_HOURS, min(MAX_HOURS, h))


def _key(group_id) -> str:
    return f'{KEY_PREFIX}{int(group_id)}'


def get_group_retention_hours(group_id) -> int:
    from app.models import SystemConfig
    raw = SystemConfig.get_value(_key(group_id), '')
    if raw in (None, ''):
        return default_hours()
    try:
        return normalize_hours(raw)
    except (TypeError, ValueError):
        return default_hours()


def set_group_retention_hours(group_id, hours) -> int:
    from app import db
    from app.models import SystemConfig
    h = normalize_hours(hours)
    key = _key(group_id)
    row = SystemConfig.query.filter_by(key_name=key).first()
    if row is None:
        row = SystemConfig(key_name=key, value=str(h))
        db.session.add(row)
    else:
        row.value = str(h)
    db.session.commit()
    return h


def describe_hours(h: int) -> str:
    if h == 0:
        return '永久保留'
    if h % 24 == 0:
        return f'{h // 24} 天'
    return f'{h} 小时'


def cutoff_for(hours: int, now):
    """Return the exclusive upper bound for deletion, or None if forever.

    * hours multiple of 24 → calendar days in Beijing: keep ``hours//24`` days
      including today (cutoff = today 00:00 − (days−1)).
    * otherwise → rolling ``now − hours``.
    """
    if hours <= 0:
        return None
    if hours % 24 == 0:
        days = hours // 24
        start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_today - timedelta(days=days - 1)
    return now - timedelta(hours=hours)


def _overrides() -> dict:
    from app.models import SystemConfig
    out = {}
    for row in SystemConfig.query.filter(SystemConfig.key_name.like(f'{KEY_PREFIX}%')).all():
        try:
            out[int(row.key_name[len(KEY_PREFIX):])] = normalize_hours(row.value)
        except (TypeError, ValueError):
            continue
    return out


def _delete_batched(base_filter, deadline: float) -> int:
    """DELETE … WHERE id IN (SELECT id … LIMIT n) in small batches (short locks)."""
    from app import db
    from app.models import SyncMessageLog
    total = 0
    for _ in range(MAX_BATCHES_PER_RUN):
        ids = [r[0] for r in db.session.query(SyncMessageLog.id).filter(*base_filter)
               .order_by(SyncMessageLog.id).limit(BATCH_SIZE).all()]
        if not ids:
            break
        SyncMessageLog.query.filter(SyncMessageLog.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        total += len(ids)
        if len(ids) < BATCH_SIZE or time_mod.monotonic() > deadline:
            break
    return total


def cleanup_sync_logs(now=None, time_budget: float = 120.0) -> dict:
    """Delete expired SyncMessageLog rows (group sync + userbot). Call inside an app context."""
    from app import db
    from app.models import SyncMessageLog
    from app.utils import get_beijing_now

    if not _run_lock.acquire(blocking=False):
        return {'skipped': True}
    try:
        now = now or get_beijing_now()
        deadline = time_mod.monotonic() + time_budget
        default_h = default_hours()
        overrides = _overrides()
        deleted = 0
        deleted_userbot = 0
        try:
            for gid, h in overrides.items():
                cut = cutoff_for(h, now)
                if cut is None:
                    continue
                n = _delete_batched(
                    (SyncMessageLog.source_group_id == gid, SyncMessageLog.synced_at < cut),
                    deadline)
                deleted += n
            cut = cutoff_for(default_h, now)
            if cut is not None:
                cond = [SyncMessageLog.synced_at < cut]
                if overrides:
                    cond.append(db.or_(SyncMessageLog.source_group_id.is_(None),
                                       ~SyncMessageLog.source_group_id.in_(list(overrides))))
                # Count userbot rows in this window before deleting (for the log line).
                ub_q = db.session.query(SyncMessageLog.id).filter(*cond, SyncMessageLog.via == 'userbot')
                deleted_userbot = ub_q.count()
                deleted += _delete_batched(tuple(cond), deadline)
        except Exception:
            db.session.rollback()
            raise
        return {
            'deleted': deleted,
            'deleted_userbot': deleted_userbot,
            'default_hours': default_h,
            'overrides': len(overrides),
            'cutoff': cutoff_for(default_h, now),
        }
    finally:
        _run_lock.release()


async def sync_log_cleanup_job(context):
    """PTB job: run the cleanup in a worker thread so the bot loop is never blocked."""
    import asyncio

    flask_app = None
    try:
        from app.modules.core import routes
        flask_app = routes.global_flask_app
    except Exception:
        pass
    if flask_app is None:
        return

    def _work():
        with flask_app.app_context():
            return cleanup_sync_logs()

    try:
        stats = await asyncio.to_thread(_work)
        if stats.get('skipped'):
            return
        cut = stats.get('cutoff')
        cut_s = cut.strftime('%Y-%m-%d %H:%M') if cut else 'forever'
        print(f"[sync-log-retention] cleanup done: deleted={stats['deleted']} "
              f"userbot={stats.get('deleted_userbot', 0)} "
              f"default={describe_hours(stats['default_hours'])} "
              f"cutoff={cut_s} group_overrides={stats['overrides']}",
              flush=True)
    except Exception as e:
        print(f"[sync-log-retention] cleanup failed: {type(e).__name__}: {str(e)[:200]}", flush=True)
