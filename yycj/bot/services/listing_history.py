"""Proven listing edit snapshots and bounded restore-as-new-edit.

Records only editable fields after a real apply. Does not invent pre-feature history,
roll back status/expiry/credit/stats/pins/publish refs, auto-restore, or call Telegram.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text

from bot.db import session_scope
from bot.models import Lamp, ListingEditVersion
from bot.services.extras_clean import clean_user_extras
from bot.services.listing_edits import EditConflict, fingerprint, live
from bot.services.listing_lock import listing_lock
from bot.services.open_shift import normalize_hours

FIELD_LABELS = {
    "city": "城市",
    "title": "称呼",
    "tags": "标签",
    "price": "价位数字",
    "price_text": "价位",
    "description": "介绍",
    "district": "区域",
    "approx_label": "大致位置",
    "extras": "自定义栏",
    "hours": "营业时间",
    "media": "图片/视频",
}
SOURCE_LABELS = {
    "baseline": "记录起点（当时的资料原文）",
    "teacher_edit": "老师修改",
    "admin_edit": "管理员修改",
    "review_approved": "审核通过后生效",
    "restore": "恢复上一版",
}
NOTICE = "仅展示本功能上线后可证明的版本；更早修改不会补造，也不能按记忆恢复。"
_DISK_DIR = Path("/tmp/yycj-media")
_SYSTEM_KEYS = (
    "status",
    "expires_at",
    "unlist_reason",
    "authenticity_score",
    "credit_boost",
    "feed_pinned",
    "feed_pin_order",
    "feed_pin_expires_at",
    "publisher_role",
    "user_id",
    "approx_lat",
    "approx_lng",
    "bc_chat",
    "bc_mid",
    "edit_revision",
    "media_revision",
    "created_at",
    "updated_at",
)


def media_key(item: Dict[str, Any] | None) -> str:
    return str((item or {}).get("file_id") or (item or {}).get("url") or "").strip().removeprefix("file_id:")


def sanitize_media(items) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        typ = str(raw.get("type") or "image").lower().strip()
        if typ not in ("image", "video"):
            typ = "image"
        ref = media_key(raw)
        if not ref or ref.lower().startswith("data:"):
            continue
        item: Dict[str, Any] = {"type": typ, "url": ref}
        if raw.get("file_id") or not ref.startswith(("http://", "https://")):
            item["file_id"] = ref
        thumb = str(raw.get("thumb_file_id") or raw.get("thumbnail") or "").strip()
        if thumb:
            item["thumb_file_id"] = thumb
        preview = str(raw.get("preview_url") or "").strip()
        if preview and not preview.lower().startswith(("javascript:", "data:text/html")):
            item["preview_url"] = preview
        out.append(item)
        if len(out) >= 9:
            break
    return out


def media_fingerprint(items) -> str:
    return hashlib.sha256(json.dumps(fingerprint(sanitize_media(items)), ensure_ascii=False).encode()).hexdigest()


def cached_media(file_id: str) -> bool:
    ref = str(file_id or "").strip()
    if not ref:
        return False
    path = _DISK_DIR / hashlib.sha256(ref.encode("utf-8")).hexdigest()[:40]
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def media_availability(items, current_items) -> Dict[str, Any]:
    current = {media_key(x) for x in sanitize_media(current_items)}
    missing = []
    for item in sanitize_media(items):
        key = media_key(item)
        if key and (key in current or cached_media(key)):
            continue
        missing.append({"type": item.get("type"), "ref": (key[:10] + "…") if len(key) > 10 else key or "未知"})
    return {"available": not missing, "missing": missing}


def snapshot_values(lamp: Lamp, extras: Optional[dict]) -> Dict[str, Any]:
    extras = extras if isinstance(extras, dict) else {}
    hours = normalize_hours(extras.get("_hours") or extras.get("hours"))
    return {
        "city": str(lamp.city or "")[:32],
        "title": str(lamp.title or "")[:128],
        "tags": list(lamp.tags or [])[:5],
        "price": lamp.price,
        "price_text": (str(lamp.price_text or "")[:32] or None),
        "description": str(lamp.description or "")[:2000],
        "district": lamp.district or None,
        "approx_label": lamp.approx_label or None,
        "extras": clean_user_extras({k: v for k, v in extras.items() if not str(k).startswith("_")}),
        "hours": hours or [],
        "media": sanitize_media(lamp.media or ([{"type": "image", "url": p} for p in (lamp.photos or []) if p])),
    }


def _norm(value):
    if value is None:
        return None
    if isinstance(value, list):
        return [_norm(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _norm(value[k]) for k in sorted(value)}
    return value


def changed_fields(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    keys = ("city", "title", "tags", "price", "price_text", "description", "district", "approx_label", "extras", "hours", "media")
    return [k for k in keys if _norm(before.get(k)) != _norm(after.get(k))]


async def load_extras(s, lamp_id: str) -> dict:
    raw = (await s.execute(text("SELECT extras FROM lamps WHERE lamp_id=:id"), {"id": lamp_id})).scalar()
    return raw if isinstance(raw, dict) else {}


async def capture(s, lamp: Lamp) -> Dict[str, Any]:
    return snapshot_values(lamp, await load_extras(s, lamp.lamp_id))


def _next_id() -> str:
    return str(uuid.uuid4())


async def _next_sequence(s, lamp_id: str) -> int:
    value = (await s.execute(select(func.max(ListingEditVersion.sequence)).where(ListingEditVersion.lamp_id == lamp_id))).scalar()
    return int(value or 0) + 1


async def _add(s, lamp_id, snapshot, *, source, actor_id, actor_kind, operation_id, source_version_id, changed, applied=True):
    row = ListingEditVersion(
        version_id=_next_id(),
        lamp_id=lamp_id,
        sequence=await _next_sequence(s, lamp_id),
        recorded_at=datetime.utcnow(),
        source=source,
        actor_id=int(actor_id) if actor_id is not None else None,
        actor_kind=actor_kind,
        operation_id=operation_id,
        source_version_id=source_version_id,
        changed_fields=list(changed),
        snapshot=dict(snapshot),
        media_fingerprint=media_fingerprint(snapshot.get("media")),
        applied=applied,
    )
    s.add(row)
    return row


async def record_applied(s, lamp: Lamp, *, before: Dict[str, Any], source: str, actor_id, actor_kind: str, operation_id=None, source_version_id=None):
    """Call in the same transaction after editable fields were actually written."""
    after = await capture(s, lamp)
    changed = changed_fields(before, after)
    exists = (await s.execute(select(func.count()).select_from(ListingEditVersion).where(ListingEditVersion.lamp_id == lamp.lamp_id))).scalar()
    if not exists:
        await _add(s, lamp.lamp_id, before, source="baseline", actor_id=None, actor_kind="system", operation_id=None, source_version_id=None, changed=[], applied=True)
    if not changed:
        return None
    return await _add(
        s,
        lamp.lamp_id,
        after,
        source=source,
        actor_id=actor_id,
        actor_kind=actor_kind,
        operation_id=operation_id,
        source_version_id=source_version_id,
        changed=changed,
        applied=True,
    )


def parse_updated_at(value) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, TypeError) as exc:
        raise EditConflict("请刷新后再试（资料版本已变化或不完整）") from exc


def stamp(value) -> Optional[str]:
    if value is None:
        return None
    return value.replace(tzinfo=None).isoformat() if hasattr(value, "isoformat") else str(value)


def actor_label(row: ListingEditVersion, *, admin: bool, owner_id: int) -> str:
    if row.source == "baseline" or row.actor_kind == "system":
        return "记录时的资料原文"
    if row.actor_kind == "teacher":
        if row.actor_id is None:
            return "老师（身份未记录）"
        return "老师本人" if int(row.actor_id) == int(owner_id) else "老师"
    if row.actor_kind == "admin":
        if admin and row.actor_id is not None:
            return f"管理员 {row.actor_id}"
        return "管理员" if row.actor_id is not None else "管理员（身份未记录）"
    return "来源未记录"


def public_current(lamp: Lamp, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **snapshot,
        "status": lamp.status,
        "expires_at": stamp(lamp.expires_at),
        "updated_at": stamp(lamp.updated_at),
        "edit_revision": lamp.edit_revision or 0,
        "media_revision": lamp.media_revision or 0,
    }


def public_version(row: ListingEditVersion, *, admin: bool, owner_id: int, current_media, include_snapshot=False) -> Dict[str, Any]:
    media = media_availability((row.snapshot or {}).get("media"), current_media)
    item = {
        "version_id": row.version_id,
        "sequence": row.sequence,
        "recorded_at": stamp(row.recorded_at),
        "source": row.source,
        "source_label": SOURCE_LABELS.get(row.source, row.source),
        "actor_label": actor_label(row, admin=admin, owner_id=owner_id),
        "changed_fields": list(row.changed_fields or []),
        "changed_labels": [FIELD_LABELS.get(k, k) for k in (row.changed_fields or [])],
        "applied": bool(row.applied),
        "media_available": media["available"],
        "media_missing": media["missing"],
        "source_version_id": row.source_version_id,
    }
    if include_snapshot:
        item["snapshot"] = dict(row.snapshot or {})
    return item


def restorable(item: Dict[str, Any], current: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    changed = changed_fields(snapshot, {k: current.get(k) for k in snapshot})
    text_changed = [k for k in changed if k != "media"]
    media_changed = "media" in changed
    can_text = bool(text_changed)
    can_media = bool(media_changed) and bool(item["media_available"])
    item.update(
        differs=bool(changed),
        restorable=can_text or can_media,
        can_restore_text=can_text,
        can_restore_media=can_media,
        current_changed_fields=changed,
        current_changed_labels=[FIELD_LABELS.get(k, k) for k in changed],
    )
    return item


async def _load_owned(s, lamp_id: str, actor_id: int, *, admin: bool) -> Lamp:
    lamp = (await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))).scalar_one_or_none()
    if not lamp or (not admin and int(lamp.user_id) != int(actor_id)):
        raise EditConflict("找不到你有权查看的资料")
    if admin:
        from bot.services import city_scope
        await city_scope.ensure_city(actor_id, lamp.city)
    return lamp


async def history(lamp_id: str, actor_id: int, *, admin=False, limit=30) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 30), 50))
    async with session_scope() as s:
        lamp = await _load_owned(s, lamp_id, actor_id, admin=admin)
        current = await capture(s, lamp)
        rows = list(
            (
                await s.execute(
                    select(ListingEditVersion)
                    .where(ListingEditVersion.lamp_id == lamp_id, ListingEditVersion.applied.is_(True))
                    .order_by(ListingEditVersion.sequence.desc())
                    .limit(limit)
                )
            ).scalars()
        )
        items = [restorable(public_version(row, admin=admin, owner_id=lamp.user_id, current_media=current["media"]), current, row.snapshot or {}) for row in rows]
    return {
        "ok": True,
        "lamp_id": lamp_id,
        "notice": NOTICE,
        "current": public_current(lamp, current),
        "items": items,
        "total": len(items),
        "media_restore_limited": True,
        "media_restore_note": "只有当前资料仍在使用、或本机缓存里还能读到的旧媒体才可恢复；不能向 Telegram 求证的旧文件会明确失败，不会悄悄清空现有图片/视频。",
    }


async def preview(lamp_id: str, version_id: str, actor_id: int, *, admin=False) -> Dict[str, Any]:
    async with session_scope() as s:
        lamp = await _load_owned(s, lamp_id, actor_id, admin=admin)
        row = await s.get(ListingEditVersion, version_id)
        if not row or row.lamp_id != lamp_id or not row.applied:
            raise EditConflict("没有可证明的这一版记录")
        current = await capture(s, lamp)
        item = restorable(
            public_version(row, admin=admin, owner_id=lamp.user_id, current_media=current["media"], include_snapshot=True),
            current,
            row.snapshot or {},
        )
    return {"ok": True, "lamp_id": lamp_id, "notice": NOTICE, "current": public_current(lamp, current), "version": item}


def payload_from_snapshot(snapshot: Dict[str, Any], *, include_media: bool, current_media) -> Dict[str, Any]:
    payload = {
        "city": str(snapshot.get("city") or "")[:32],
        "title": str(snapshot.get("title") or "")[:128],
        "tags": [str(t).strip()[:24] for t in (snapshot.get("tags") or []) if str(t).strip()][:5],
        "price": snapshot.get("price"),
        "price_text": (str(snapshot.get("price_text") or "")[:32]),
        "description": str(snapshot.get("description") or "")[:2000],
        "district": (str(snapshot.get("district") or "").strip()[:64] or None),
        "approx_label": (str(snapshot.get("approx_label") or "").strip()[:128] or None),
        "extras": clean_user_extras(snapshot.get("extras") or {}),
        "hours": normalize_hours(snapshot.get("hours")) or [],
    }
    if include_media:
        payload["media"] = sanitize_media(snapshot.get("media"))
        payload["photos"] = [m.get("url") or m.get("file_id") for m in payload["media"] if (m.get("type") or "image") == "image"]
    return payload


async def restore(lamp_id: str, version_id: str, actor_id: int, operation_id: str, *, admin=False, expected_updated_at, confirm=False, include_media=False):
    if not confirm:
        raise EditConflict("请先预览并明确确认后再恢复")
    expected = parse_updated_at(expected_updated_at)
    from bot.services import listing_edits

    async with listing_lock(lamp_id):
        async with session_scope() as s:
            lamp = (await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id).with_for_update())).scalar_one_or_none()
            if not lamp or (not admin and int(lamp.user_id) != int(actor_id)):
                raise EditConflict("找不到你有权恢复的资料")
            if admin:
                from bot.services import city_scope
                await city_scope.ensure_city(actor_id, lamp.city)
            if stamp(lamp.updated_at) != stamp(expected):
                raise EditConflict("资料已被更新，请刷新修改记录后再预览确认")
            row = await s.get(ListingEditVersion, version_id)
            if not row or row.lamp_id != lamp_id or not row.applied:
                raise EditConflict("没有可证明的这一版记录")
            current = await capture(s, lamp)
            snapshot = dict(row.snapshot or {})
            for banned in _SYSTEM_KEYS:
                snapshot.pop(banned, None)
            if admin:
                from bot.services import city_scope
                await city_scope.ensure_city(actor_id, snapshot.get("city") or lamp.city)
            changed = changed_fields(snapshot, current)
            if include_media and "media" in changed:
                media = media_availability(snapshot.get("media"), current["media"])
                if not media["available"]:
                    raise EditConflict("旧图片/视频已不可用，不能恢复该版本媒体，也不会清空现有媒体。可仅恢复文字和营业时间。")
            elif not include_media:
                changed = [k for k in changed if k != "media"]
            if not changed:
                raise EditConflict("这一版与当前资料相同，无需恢复")
        payload = payload_from_snapshot(snapshot, include_media=include_media and "media" in changed_fields(snapshot, current), current_media=current["media"])
        return await listing_edits.submit(
            lamp_id,
            actor_id,
            payload,
            operation_id,
            admin=admin,
            history_source="restore",
            source_version_id=version_id,
        )
