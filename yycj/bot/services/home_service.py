"""首页运营：公告、启用城市、精选置顶、评价、演示种子数据。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_, select

from bot.db import session_scope
from bot.models import HomepagePin, Lamp, LampStatus, Review, ReviewStatus, SiteSettings
from bot.services import search_service
from bot.services import home_feed

logger = logging.getLogger(__name__)

DEFAULT_CITIES = ["深圳", "广州", "东莞", "佛山", "珠海", "惠州"]
SETTINGS_KEY = "default"

# 资料侧评价统计（条数/星级），与已停用的用户兰花信用无关。
# 既有 authenticity_score / credit_boost 列仍可能存在于资料行，但不再作为对外评分展示。


def _settings_to_dict(row: SiteSettings) -> Dict[str, Any]:
    cities = list(row.enabled_cities or []) or list(DEFAULT_CITIES)
    ops = home_feed.merge_ops(getattr(row, "ops_config", None) or {})
    out = {
        "announcement_text": row.announcement_text or "",
        "announcement_enabled": bool(row.announcement_enabled),
        "enabled_cities": cities,
        "ops": ops,
        "updated_at": row.updated_at,
        "config_revision": getattr(row,"config_revision",0) or 0,
        "updated_by": getattr(row,"updated_by",None),
    }
    out.update(ops)
    return out


async def get_or_create_settings() -> Dict[str, Any]:
    async with session_scope() as s:
        res = await s.execute(select(SiteSettings).where(SiteSettings.key == SETTINGS_KEY))
        row = res.scalar_one_or_none()
        if not row:
            row = SiteSettings(
                key=SETTINGS_KEY,
                announcement_text="欢迎使用月影车姬 · 请文明交流，理性选择。",
                announcement_enabled=True,
                enabled_cities=list(DEFAULT_CITIES),
            )
            s.add(row)
            await s.flush()
        return _settings_to_dict(row)


async def update_city_overrides(city: str, changes: Dict[str, Any], *, actor_id=None) -> Dict[str, Any]:
    """Set / clear one city's overrides under the settings row lock. A None / empty value clears that key."""
    city = (city or "").strip()[:32]
    async with session_scope() as s:
        res = await s.execute(select(SiteSettings).where(SiteSettings.key == SETTINGS_KEY).with_for_update())
        row = res.scalar_one_or_none()
        if not row:
            row = SiteSettings(key=SETTINGS_KEY)
            s.add(row)
            await s.flush()
        ops = dict(getattr(row, "ops_config", None) or {})
        allc = dict(ops.get("city_overrides") or {}) if isinstance(ops.get("city_overrides"), dict) else {}
        cur = dict(allc.get(city) or {})
        for k, v in (changes or {}).items():
            cv = home_feed.clean_override_value(k, v)
            if cv is None:
                cur.pop(k, None)
            else:
                cur[k] = cv
        if cur:
            allc[city] = cur
        else:
            allc.pop(city, None)
        ops["city_overrides"] = allc
        row.ops_config = home_feed.merge_ops(ops)
        row.config_revision=(row.config_revision or 0)+1
        row.updated_by=actor_id
        row.updated_at = datetime.utcnow()
        await s.flush()
        return _settings_to_dict(row)


async def patch_city_announcement(city: str, changes: Dict[str, Any], *, actor_id=None) -> Dict[str, Any]:
    """Workbench-only partial patch, under the existing settings lock. Preserve legacy fallback:
    disabled/empty city announcements never hide the global announcement. No guest-rule change.
    """
    async with session_scope() as s:
        row=(await s.execute(select(SiteSettings).where(SiteSettings.key==SETTINGS_KEY).with_for_update())).scalar_one_or_none()
        if not row:
            row=SiteSettings(key=SETTINGS_KEY);s.add(row);await s.flush()
        ops=dict(row.ops_config or {});allc=dict(ops.get('city_announcements') or {});old=dict(allc.get(city) or {})
        text=str(changes.get('announcement_text',old.get('text','')) or '')[:2000]
        enabled=bool(changes.get('announcement_enabled',old.get('enabled',False)))
        if text.strip():allc[city]={'text':text,'enabled':enabled}
        else:allc.pop(city,None)
        ops['city_announcements']=allc;row.ops_config=home_feed.merge_ops(ops)
        row.config_revision=(row.config_revision or 0)+1;row.updated_by=actor_id;row.updated_at=datetime.utcnow()
        await s.flush();return _settings_to_dict(row)


async def update_settings(
    *,
    announcement_text: Optional[str] = None,
    announcement_enabled: Optional[bool] = None,
    enabled_cities: Optional[List[str]] = None,
    ops_config: Optional[Dict[str, Any]] = None,
    actor_id: Optional[int] = None,
) -> Dict[str, Any]:
    async with session_scope() as s:
        # Row lock: the console fires several saves at once; without FOR UPDATE they
        # read-modify-write ops_config concurrently and overwrite each other's keys.
        res = await s.execute(
            select(SiteSettings).where(SiteSettings.key == SETTINGS_KEY).with_for_update()
        )
        row = res.scalar_one_or_none()
        if not row:
            row = SiteSettings(key=SETTINGS_KEY)
            s.add(row)
            await s.flush()
        if announcement_text is not None:
            row.announcement_text = announcement_text[:2000]
        if announcement_enabled is not None:
            row.announcement_enabled = bool(announcement_enabled)
        if enabled_cities is not None:
            cleaned = [c.strip()[:32] for c in enabled_cities if (c or "").strip()]
            row.enabled_cities = cleaned[:50] or list(DEFAULT_CITIES)
        if ops_config is not None:
            merged = home_feed.merge_ops({**(getattr(row, "ops_config", None) or {}), **ops_config})
            row.ops_config = merged
        row.config_revision=(row.config_revision or 0)+1
        row.updated_by=actor_id
        row.updated_at = datetime.utcnow()
        await s.flush()
        return _settings_to_dict(row)


async def list_active_pins() -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    async with session_scope() as s:
        res = await s.execute(
            select(HomepagePin)
            .where(or_(HomepagePin.expires_at.is_(None), HomepagePin.expires_at > now))
            .order_by(HomepagePin.sort_order.asc(), HomepagePin.id.asc())
        )
        pins = list(res.scalars().all())
    out: List[Dict[str, Any]] = []
    for p in pins:
        lamp = await search_service.get_lamp(p.lamp_id)
        if not lamp or lamp.get("status") != LampStatus.ACTIVE.value:
            continue
        out.append({"id": p.id, "lamp_id": p.lamp_id, "sort_order": p.sort_order, "expires_at": p.expires_at, "lamp": lamp})
    return out


async def list_all_pins() -> List[Dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(select(HomepagePin).order_by(HomepagePin.sort_order.asc(), HomepagePin.id.asc()))
        pins = list(res.scalars().all())
    out = []
    for p in pins:
        lamp = await search_service.get_lamp(p.lamp_id)
        out.append({"id": p.id, "lamp_id": p.lamp_id, "sort_order": p.sort_order, "expires_at": p.expires_at, "created_at": p.created_at, "created_by": p.created_by, "lamp": lamp})
    return out


async def add_pin(lamp_id: str, *, sort_order: int = 0, expires_at: Optional[datetime] = None, expires_hours: Optional[int] = None, created_by: Optional[int] = None) -> Dict[str, Any]:
    lamp = await search_service.get_lamp(lamp_id)
    if not lamp:
        raise ValueError("资料不存在")
    if expires_hours is not None and expires_at is None:
        expires_at = datetime.utcnow() + timedelta(hours=max(1, int(expires_hours)))
    async with session_scope() as s:
        res = await s.execute(select(HomepagePin).where(HomepagePin.lamp_id == lamp_id))
        pin = res.scalar_one_or_none()
        if pin:
            pin.sort_order = int(sort_order)
            pin.expires_at = expires_at
            if created_by is not None:
                pin.created_by = created_by
        else:
            pin = HomepagePin(lamp_id=lamp_id, sort_order=int(sort_order), expires_at=expires_at, created_by=created_by)
            s.add(pin)
        await s.flush()
        return {"id": pin.id, "lamp_id": pin.lamp_id, "sort_order": pin.sort_order, "expires_at": pin.expires_at}


async def remove_pin(pin_id: int) -> bool:
    async with session_scope() as s:
        res = await s.execute(select(HomepagePin).where(HomepagePin.id == pin_id))
        pin = res.scalar_one_or_none()
        if not pin:
            return False
        s.delete(pin)
        return True


async def reorder_pins(ordered_ids: List[int]) -> List[Dict[str, Any]]:
    async with session_scope() as s:
        for idx, pid in enumerate(ordered_ids):
            res = await s.execute(select(HomepagePin).where(HomepagePin.id == int(pid)))
            pin = res.scalar_one_or_none()
            if pin:
                pin.sort_order = idx
        await s.flush()
    return await list_all_pins()


def _review_to_dict(r: Review, *, admin: bool = False) -> Dict[str, Any]:
    """Public shape. 约课凭证 (proofs) only with admin=True — moderation paths only."""
    out = {"review_id": r.review_id, "session_id": r.session_id, "lamp_id": r.lamp_id, "target_user_id": r.target_user_id, "guest_id": r.guest_id, "stars": r.stars, "text": r.text or "", "photos": list(r.photos or []), "sections": dict(getattr(r, "sections", None) or {}), "status": r.status, "created_at": r.created_at, "reviewed_at": r.reviewed_at, "review_note": r.review_note}
    if admin:
        out["proofs"] = list(getattr(r, "proofs", None) or [])
    else:
        out["proof_count"] = len(list(getattr(r, "proofs", None) or []))
    return out


SECTION_LIMITS = {"diff": 200, "pros": 200, "cons": 200, "extra": 1000}
SECTION_LABELS = {"diff": "人照课表差异", "pros": "优点", "cons": "不足", "extra": "补充"}
SECTION_MIN = 5  # 差异 / 优点 / 不足 each needs at least this many characters


def clean_sections(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, lim in SECTION_LIMITS.items():
        v = str(raw.get(k) or "").strip()[:lim]
        if v:
            out[k] = v
    return out


def sections_error(sec: Dict[str, str]) -> Optional[str]:
    for k in ("diff", "pros", "cons"):
        if len(sec.get(k, "")) < SECTION_MIN:
            return f"「{SECTION_LABELS[k]}」至少写 {SECTION_MIN} 个字"
    return None


def sections_text(sec: Dict[str, str]) -> str:
    """Plain-text form kept in reviews.text, so older clients / exports still read it."""
    return "\n".join(f"{SECTION_LABELS[k]}：{sec[k]}" for k in ("diff", "pros", "cons", "extra") if sec.get(k))


async def review_needs_audit(city: Optional[str]) -> bool:
    """评价需审核 for a card's city (city override, else site-wide; unset = ON)."""
    settings = home_feed.ops_for_city(await get_or_create_settings(), city)
    return home_feed.audit_on(settings.get("review_require_audit"))


async def create_review(*, guest_id: int, lamp_id: str, stars: int, text: str = "", photos: Optional[List[str]] = None, session_id: Optional[str] = None, target_user_id: Optional[int] = None, auto_approve: bool = False, sections: Optional[Dict[str, str]] = None, proofs: Optional[List[str]] = None) -> Dict[str, Any]:
    stars = max(1, min(5, int(stars)))
    review_id = str(uuid.uuid4())
    sec = clean_sections(sections or {})
    if sec and not (text or "").strip():
        text = sections_text(sec)
    async with session_scope() as s:
        r = Review(review_id=review_id, session_id=session_id, lamp_id=lamp_id, target_user_id=target_user_id, guest_id=guest_id, stars=stars, text=(text or "")[:2000], photos=list(photos or [])[:6], sections=sec, proofs=list(proofs or [])[:5], status=ReviewStatus.PENDING.value)
        if auto_approve:
            # Same end state as an admin approval (set_review_status APPROVED): visible on the
            # card and counted in reputation, which is computed from approved reviews.
            r.status = ReviewStatus.APPROVED.value
            r.reviewed_at = datetime.utcnow()
            r.review_note = "免审直接发布"
        s.add(r)
        await s.flush()
        return _review_to_dict(r, admin=True)


async def list_reviews_for_lamp(lamp_id: str, *, approved_only: bool = True) -> List[Dict[str, Any]]:
    async with session_scope() as s:
        cond = [Review.lamp_id == lamp_id]
        if approved_only:
            cond.append(Review.status == ReviewStatus.APPROVED.value)
        res = await s.execute(select(Review).where(and_(*cond)).order_by(Review.created_at.desc()).limit(50))
        return [_review_to_dict(x) for x in res.scalars().all()]


async def list_pending_reviews(limit: int = 50, cities: Optional[set] = None) -> List[Dict[str, Any]]:
    stmt = select(Review).where(Review.status == ReviewStatus.PENDING.value)
    if cities is not None:
        from bot.services.city_scope import city_in
        stmt = stmt.where(Review.lamp_id.in_(select(Lamp.lamp_id).where(city_in(Lamp.city, cities))))
    async with session_scope() as s:
        res = await s.execute(stmt.order_by(Review.created_at.asc()).limit(max(1, min(limit, 200))))
        return [_review_to_dict(x, admin=True) for x in res.scalars().all()]


async def list_my_reviews(guest_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(select(Review).where(Review.guest_id == guest_id).order_by(Review.created_at.desc()).limit(max(1, min(limit, 100))))
        return [_review_to_dict(x) for x in res.scalars().all()]


async def set_review_status(review_id: str, status: str, *, note: Optional[str] = None) -> Dict[str, Any]:
    if status not in {ReviewStatus.APPROVED.value, ReviewStatus.REJECTED.value, ReviewStatus.BRUSH.value}:
        raise ValueError("无效评价状态")
    async with session_scope() as s:
        res = await s.execute(select(Review).where(Review.review_id == review_id))
        r = res.scalar_one_or_none()
        if not r:
            raise LookupError("评价不存在")
        if r.status != ReviewStatus.PENDING.value:
            raise RuntimeError("已处理或不在待审状态")
        r.status = status
        r.reviewed_at = datetime.utcnow()
        if note:
            r.review_note = note[:256]
        await s.flush()
        out = _review_to_dict(r)
    if status == ReviewStatus.APPROVED.value and int(out.get("stars") or 0) >= 4:
        try:
            import asyncio
            from bot.services.broadcast import broadcast_review
            from bot.services import search_service as _ss
            lamp = await _ss.get_lamp(out.get("lamp_id") or "")
            if lamp:
                asyncio.get_running_loop().create_task(broadcast_review(out, lamp))
        except Exception:
            pass
    return out


async def reputation_for_lamp(lamp_id: str) -> Dict[str, Any]:
    lamp = await search_service.get_lamp(lamp_id)
    base = int((lamp or {}).get("authenticity_score") or 80)
    reviews = await list_reviews_for_lamp(lamp_id, approved_only=True)
    if reviews:
        avg = sum(int(r["stars"]) for r in reviews) / len(reviews)
        star_score = avg * 20
        score = int(round(base * 0.6 + star_score * 0.4))
    else:
        score = base
        avg = None
    return {"score": max(0, min(100, score)), "base": base, "review_count": len(reviews), "avg_stars": round(avg, 2) if avg is not None else None}


async def seed_demo_if_empty() -> Dict[str, Any]:
    await get_or_create_settings()
    async with session_scope() as s:
        res = await s.execute(select(Lamp).where(Lamp.status == LampStatus.ACTIVE.value).limit(1))
        if res.scalar_one_or_none():
            return {"seeded": False, "reason": "already_has_active"}
    demos = [
        {"title": "小月", "city": "深圳", "district": "南山区", "approx_label": "附近", "approx_lat": 22.5333, "approx_lng": 113.9300, "price_text": "面议", "tags": ["清新", "健谈"], "description": "演示资料 · 月影车姬首页预览用。", "media": [{"type": "image", "url": "https://picsum.photos/seed/yycj1/600/800"}], "publisher_role": "teacher", "authenticity_score": 86},
        {"title": "阿影", "city": "深圳", "district": "福田区", "approx_label": "约1-3km", "approx_lat": 22.5400, "approx_lng": 114.0550, "price_text": "6000+", "tags": ["成熟", "有礼"], "description": "演示资料二 · 仅供界面联调。", "media": [{"type": "image", "url": "https://picsum.photos/seed/yycj2/600/800"}], "publisher_role": "teacher", "authenticity_score": 82},
        {"title": "商家演示", "city": "深圳", "district": "罗湖区", "approx_label": "约3-5km", "approx_lat": 22.5480, "approx_lng": 114.1200, "price_text": "套餐详询", "tags": ["商家", "可约"], "description": "商家身份发布演示。", "media": [{"type": "image", "url": "https://picsum.photos/seed/yycj3/600/800"}], "publisher_role": "merchant", "authenticity_score": 78},
    ]
    created = []
    for d in demos:
        lamp = await search_service.create_lamp_from_post(user_id=0, city=d["city"], title=d["title"], tags=d["tags"], price=None, price_text=d["price_text"], description=d["description"], photos=[], authenticity_score=d["authenticity_score"], district=d["district"], approx_lat=d["approx_lat"], approx_lng=d["approx_lng"], approx_label=d["approx_label"], media=d["media"], publisher_role=d["publisher_role"])
        await search_service.approve_lamp(lamp["lamp_id"])
        created.append(lamp["lamp_id"])
    if created:
        await add_pin(created[0], sort_order=0, expires_hours=72 * 24, created_by=0)
    logger.info("Seeded %s demo lamps for 月影车姬", len(created))
    return {"seeded": True, "lamp_ids": created}


set_feed_pin = home_feed.set_feed_pin
list_feed_pins = home_feed.list_feed_pins
list_approved_lamps_brief = home_feed.list_approved_lamps_brief
DEFAULT_OPS = home_feed.DEFAULT_OPS
