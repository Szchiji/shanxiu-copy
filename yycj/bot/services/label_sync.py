"""Debounced in-place edit of existing listing/coupon channel posts after link-label changes.

Only ACTIVE unexpired listing posts and still-open coupon posts are edited. Missing posts are
skipped (no repost). Featured-review posts are not updated retroactively; new ones store message ids.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, select

from bot.db import session_scope
from bot.models import Lamp, LampStatus, Review, ReviewStatus
from bot.models_extra import Coupon, LabelSyncState

logger = logging.getLogger(__name__)

LABEL_KEYS = ("link_label_detail", "link_label_reviews", "link_label_write")
_LABEL_DEFAULTS = {"link_label_detail": "查看详情", "link_label_reviews": "查看评价", "link_label_write": "写评价"}
DEBOUNCE_SEC = 30
RETRY_CAP = 35

_locks: Dict[str, asyncio.Lock] = {}
_timers: Dict[str, asyncio.Task] = {}


def classify_edit_error(exc: BaseException) -> str:
    """updated / missing / gone / retry. Pure; used by tests."""
    msg = str(exc or "").lower()
    retry_after = getattr(exc, "retry_after", None)
    if retry_after or "retry after" in msg or "flood" in msg or "too many requests" in msg:
        return "retry"
    if "not modified" in msg:
        return "updated"
    if any(s in msg for s in (
        "message to edit not found", "message can't be edited", "message cannot be edited",
        "message identifier is invalid", "message not found", "message_id_invalid",
    )):
        return "missing"
    if any(s in msg for s in (
        "chat not found", "bot was kicked", "bot is not a member", "have no rights",
        "not enough rights", "chat_id is empty", "need administrator", "forbidden",
        "was blocked", "kicked from", "deactivated",
    )):
        return "gone"
    if any(s in msg for s in (
        "timed out", "timeout", "bad gateway", "internal server", "service unavailable",
        "network", "temporarily", "connection", "cancelled", "reset by peer",
    )):
        return "retry"
    return "retry"


def retry_after_sec(exc: BaseException) -> int:
    n = getattr(exc, "retry_after", None)
    try:
        if n is not None:
            return max(0, int(n))
    except (TypeError, ValueError):
        pass
    m = re.search(r"retry after\s+(\d+)", str(exc or ""), re.I)
    return int(m.group(1)) if m else 0


def effective_labels(settings: Dict[str, Any], city: Optional[str]) -> Tuple[str, str, str]:
    from bot.services.home_feed import ops_for_city
    site = ops_for_city(settings or {}, city)
    out = []
    for k in LABEL_KEYS:
        v = site.get(k)
        out.append(str(v if v is not None else _LABEL_DEFAULTS[k])[:16])
    return tuple(out)  # type: ignore[return-value]


def cities_whose_labels_changed(before: Dict[str, Any], after: Dict[str, Any], city: Optional[str] = None) -> List[str]:
    if city:
        names = [str(city).strip()]
    else:
        names = [str(c).strip() for c in (after.get("enabled_cities") or before.get("enabled_cities") or [])]
    out: List[str] = []
    from bot.services.home_feed import ops_for_city
    def location_signature(settings, city):
        site = ops_for_city(settings, city)
        return tuple(site.get(k) for k in ("link_label_location", "community_location_url", "community_enabled"))
    for name in names:
        if name and (effective_labels(before, name) != effective_labels(after, name)
                     or location_signature(before, name) != location_signature(after, name)):
            out.append(name)
    return out


def _lock(city: str) -> asyncio.Lock:
    if city not in _locks:
        _locks[city] = asyncio.Lock()
    return _locks[city]


async def schedule_after_save(before: Dict[str, Any], after: Dict[str, Any], city: Optional[str] = None) -> List[str]:
    cities = cities_whose_labels_changed(before, after, city=city)
    for name in cities:
        await queue_city(name, delay=DEBOUNCE_SEC)
    return cities


async def queue_city(city: str, *, delay: int = DEBOUNCE_SEC) -> None:
    city = (city or "").strip()[:32]
    if not city:
        return
    now = datetime.utcnow()
    async with session_scope() as s:
        row = (await s.execute(select(LabelSyncState).where(LabelSyncState.city == city))).scalar_one_or_none()
        if not row:
            row = LabelSyncState(city=city)
            s.add(row)
        if row.status != "running":
            row.status = "queued"
        row.queued_at = now
        row.note = ""
        await s.flush()
    _arm(city, delay)


def _arm(city: str, delay: int) -> None:
    old = _timers.pop(city, None)
    if old and not old.done():
        old.cancel()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _timers[city] = loop.create_task(_run_later(city, max(0, int(delay))))


async def _run_later(city: str, delay: int) -> None:
    try:
        if delay:
            await asyncio.sleep(delay)
        await run_city(city)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("label sync delayed run failed city=%s", city)


async def job_tick() -> None:
    """Interval safety net: pick up queued cities after a restart, or ones past the debounce."""
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=DEBOUNCE_SEC)
    async with session_scope() as s:
        rows = (await s.execute(select(LabelSyncState).where(
            LabelSyncState.status == "queued",
            LabelSyncState.queued_at.is_not(None),
            LabelSyncState.queued_at <= cutoff,
        ))).scalars().all()
        cities = [r.city for r in rows]
    for city in cities:
        try:
            await run_city(city)
        except Exception:
            logger.exception("label sync tick failed city=%s", city)


async def run_city(city: str, *, only_failed: bool = False) -> Dict[str, Any]:
    city = (city or "").strip()[:32]
    if not city:
        return {"ok": False, "error": "缺少城市"}
    async with _lock(city):
        from bot.services import execution_log
        record=await execution_log.begin('label_sync','edit_existing',city=city,target='channel',retry_kind='label_sync')
        try:
            result=await _run_city_locked(city, only_failed=only_failed)
            await execution_log.finish(record,'failed' if result.get('failed') else 'synced','failed' if result.get('failed') else None)
            return result
        except Exception:
            await execution_log.finish(record,'unknown','delivery_unknown')
            raise


async def _run_city_locked(city: str, *, only_failed: bool) -> Dict[str, Any]:
    now = datetime.utcnow()
    async with session_scope() as s:
        row = (await s.execute(select(LabelSyncState).where(LabelSyncState.city == city))).scalar_one_or_none()
        if not row:
            row = LabelSyncState(city=city)
            s.add(row)
        prior_failed = list(row.failed or []) if only_failed else []
        row.status = "running"
        row.started_at = now
        row.finished_at = None
        if not only_failed:
            row.updated = 0
            row.retry = 0
            row.missing = 0
            row.gone = 0
            row.failed = []
        else:
            row.retry = 0
            row.failed = []
        await s.flush()

    work = prior_failed if only_failed else await _collect_work(city)
    counts = {"updated": 0, "retry": 0, "missing": 0, "gone": 0, "skip": 0}
    failed: List[Dict[str, Any]] = []
    for item in work:
        result, detail, wait = await _sync_one(item)
        if wait:
            await asyncio.sleep(wait)
            result, detail, wait = await _sync_one(item)
        if result == "updated":
            counts["updated"] += 1
        elif result in ("missing", "gone"):
            counts[result] += 1
        elif result == "skip":
            counts["skip"] += 1
        else:
            counts["retry"] += 1
            failed.append({"kind": item.get("kind"), "id": item.get("id"), "error": result, "detail": (detail or "")[:200]})
        if len(work) > 4 and (counts["updated"] + counts["retry"] + counts["missing"] + counts["gone"]) % 8 == 0:
            await _flush_progress(city, counts, failed, running=True)

    reviews = await _review_counts(city)
    async with session_scope() as s:
        row = (await s.execute(select(LabelSyncState).where(LabelSyncState.city == city))).scalar_one_or_none()
        if row:
            if only_failed:
                row.updated = int(row.updated or 0) + counts["updated"]
                row.missing = int(row.missing or 0) + counts["missing"]
                row.gone = int(row.gone or 0) + counts["gone"]
            else:
                row.updated = counts["updated"]
                row.missing = counts["missing"]
                row.gone = counts["gone"]
            row.retry = counts["retry"]
            row.failed = failed
            row.status = "done"
            row.finished_at = datetime.utcnow()
            row.reviews_stored = reviews["stored"]
            row.reviews_legacy = reviews["legacy"]
            row.note = ""
            await s.flush()
            return _public(row)
    return {"ok": True, "city": city, **counts, "failed": failed, **reviews}


async def retry_failed(city: str) -> Dict[str, Any]:
    return await run_city(city, only_failed=True)


async def status(city: str) -> Dict[str, Any]:
    city = (city or "").strip()[:32]
    async with session_scope() as s:
        row = (await s.execute(select(LabelSyncState).where(LabelSyncState.city == city))).scalar_one_or_none()
        reviews = await _review_counts(city)
        if not row:
            return {"ok": True, "city": city, "status": "idle", "updated": 0, "retry": 0,
                    "missing": 0, "gone": 0, "failed": [], **reviews}
        out = _public(row)
        out.update(reviews)
        return out


def _public(row: LabelSyncState) -> Dict[str, Any]:
    def iso(v):
        return v.isoformat() + "Z" if v else None
    return {
        "ok": True,
        "city": row.city,
        "status": row.status or "idle",
        "queued_at": iso(row.queued_at),
        "started_at": iso(row.started_at),
        "finished_at": iso(row.finished_at),
        "updated": int(row.updated or 0),
        "retry": int(row.retry or 0),
        "missing": int(row.missing or 0),
        "gone": int(row.gone or 0),
        "failed": list(row.failed or []),
        "reviews_stored": int(row.reviews_stored or 0),
        "reviews_legacy": int(row.reviews_legacy or 0),
        "note": row.note or "",
    }


async def _flush_progress(city: str, counts: Dict[str, int], failed: List[Dict[str, Any]], running: bool) -> None:
    try:
        async with session_scope() as s:
            row = (await s.execute(select(LabelSyncState).where(LabelSyncState.city == city))).scalar_one_or_none()
            if not row:
                return
            row.updated = counts["updated"]
            row.retry = counts["retry"]
            row.missing = counts["missing"]
            row.gone = counts["gone"]
            row.failed = list(failed)
            if running:
                row.status = "running"
            await s.flush()
    except Exception:
        logger.exception("label sync progress flush failed")


async def _review_counts(city: str) -> Dict[str, int]:
    try:
        async with session_scope() as s:
            q = select(Review).join(Lamp, Lamp.lamp_id == Review.lamp_id).where(
                Lamp.city == city, Review.status == ReviewStatus.APPROVED.value)
            rows = (await s.execute(q)).scalars().all()
        stored = sum(1 for r in rows if r.post_mid)
        return {"reviews_stored": stored, "reviews_legacy": max(0, len(rows) - stored)}
    except Exception:
        logger.exception("review mid counts failed")
        return {"reviews_stored": 0, "reviews_legacy": 0}


async def _collect_work(city: str) -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    work: List[Dict[str, Any]] = []
    async with session_scope() as s:
        lamps = (await s.execute(select(Lamp.lamp_id).where(
            Lamp.city == city,
            Lamp.status == LampStatus.ACTIVE.value,
            or_(Lamp.expires_at.is_(None), Lamp.expires_at > now),
        ))).all()
        lids = [r[0] for r in lamps]
        coupons = (await s.execute(select(Coupon).where(
            Coupon.city == city,
            Coupon.post_mid.is_not(None),
            or_(Coupon.post_closed.is_(False), Coupon.post_closed.is_(None)),
        ))).scalars().all()
    from bot.services.extras_store import load_broadcast
    for lid in lids:
        try:
            chat, mid = await load_broadcast(lid)
        except Exception:
            chat, mid = None, None
        if chat and str(mid).isdigit():
            work.append({"kind": "listing", "id": lid})
    seen = set()
    for c in coupons:
        key = (c.post_chat, int(c.post_mid))
        if key in seen:
            continue
        seen.add(key)
        work.append({"kind": "coupon", "id": c.coupon_id})
    return work


async def _sync_one(item: Dict[str, Any]) -> Tuple[str, str, int]:
    kind, ident = item.get("kind"), item.get("id")
    try:
        if kind == "listing":
            result, detail, wait = await _sync_listing(ident)
        elif kind == "coupon":
            result, detail, wait = await _sync_coupon(ident)
        else:
            return "skip", "", 0
        return result, detail, wait
    except Exception as exc:
        logger.exception("label sync item failed %s %s", kind, ident)
        return classify_edit_error(exc), str(exc)[:200], min(RETRY_CAP, retry_after_sec(exc))


async def _sync_listing(lamp_id: str) -> Tuple[str, str, int]:
    from bot.services import search_service
    from bot.services.broadcast import _caption
    from bot.services.extras_store import load_broadcast
    from bot.main import bot

    chat, mid = await load_broadcast(lamp_id)
    if not chat or not str(mid).isdigit():
        return "missing", "no stored post", 0
    lamp = await search_service.get_lamp(lamp_id)
    if not lamp or lamp.get("status") != LampStatus.ACTIVE.value:
        return "skip", "not active", 0
    exp = lamp.get("expires_at")
    if exp:
        if getattr(exp, "tzinfo", None):
            exp = exp.replace(tzinfo=None)
        if isinstance(exp, datetime) and exp <= datetime.utcnow():
            return "skip", "expired", 0
    extras = lamp.get("extras") if isinstance(lamp, dict) else None
    _, caption, _, _ = await _caption(lamp, extras)
    last: Optional[BaseException] = None
    for attempt in (
        lambda: bot.edit_message_caption(chat_id=chat, message_id=int(mid), caption=caption, reply_markup=None),
        lambda: bot.edit_message_text(chat_id=chat, message_id=int(mid), text=caption, reply_markup=None,
                                      disable_web_page_preview=True),
    ):
        try:
            await attempt()
            return "updated", "", 0
        except Exception as exc:
            last = exc
            kind = classify_edit_error(exc)
            if kind == "updated":
                return "updated", "", 0
            if kind in ("missing", "gone"):
                return kind, str(exc)[:200], 0
            continue
    if last is None:
        return "retry", "", 0
    kind = classify_edit_error(last)
    return kind, str(last)[:200], min(RETRY_CAP, retry_after_sec(last) or 2)


async def _sync_coupon(coupon_id: str) -> Tuple[str, str, int]:
    from bot.services import coupon_push
    result = await coupon_push.sync_edit(coupon_id)
    return result, "", 2 if result == "retry" else 0
