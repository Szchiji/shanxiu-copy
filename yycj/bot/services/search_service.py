"""灯笼搜索（关键词 + 城市 + 价位 + 模糊距离）。"""
from __future__ import annotations

from html import escape as _h

import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import String, and_, cast, func, or_, select

from bot.db import session_scope
from bot.models import Lamp, LampStatus


def _media_from_lamp(lamp: Lamp) -> List[Dict[str, str]]:
    raw = list(lamp.media or [])
    if raw:
        out = []
        for m in raw:
            if isinstance(m, dict) and m.get("url"):
                out.append({"type": m.get("type") or "image", "url": str(m["url"])})
            elif isinstance(m, str) and m.strip():
                out.append({"type": "image", "url": m.strip()})
        return out
    return [{"type": "image", "url": str(p)} for p in (lamp.photos or []) if p]


def _lamp_to_dict(lamp: Lamp) -> Dict[str, Any]:
    media = _media_from_lamp(lamp)
    photos = [m["url"] for m in media if m.get("type") == "image"] or list(lamp.photos or [])
    return {
        "lamp_id": lamp.lamp_id,
        "user_id": lamp.user_id,
        "city": lamp.city,
        "title": lamp.title,
        "tags": list(lamp.tags or []),
        "price": lamp.price,
        "price_text": lamp.price_text,
        "description": lamp.description or "",
        "photos": photos,
        "media": media,
        "district": getattr(lamp, "district", None),
        "approx_lat": getattr(lamp, "approx_lat", None),
        "approx_lng": getattr(lamp, "approx_lng", None),
        "approx_label": getattr(lamp, "approx_label", None),
        "publisher_role": getattr(lamp, "publisher_role", None),
        "feed_pinned": bool(getattr(lamp, "feed_pinned", False)),
        "feed_pin_order": int(getattr(lamp, "feed_pin_order", 0) or 0),
        "authenticity_score": lamp.authenticity_score,
        "credit_boost": lamp.credit_boost,
        "status": lamp.status,
        "expires_at": lamp.expires_at,
        "created_at": lamp.created_at,
        "updated_at": lamp.updated_at,
    }


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def fuzzy_distance_label(km: Optional[float]) -> Optional[str]:
    if km is None:
        return None
    if km < 1.0:
        return "附近"
    if km < 3.0:
        return "约1-3km"
    if km < 5.0:
        return "约3-5km"
    if km < 10.0:
        return "约5-10km"
    return "稍远"


def attach_fuzzy_distance(lamp: Dict[str, Any], lat: Optional[float], lng: Optional[float]) -> Dict[str, Any]:
    out = dict(lamp)
    label = out.get("approx_label")
    if lat is not None and lng is not None and out.get("approx_lat") is not None and out.get("approx_lng") is not None:
        try:
            km = haversine_km(float(lat), float(lng), float(out["approx_lat"]), float(out["approx_lng"]))
            label = fuzzy_distance_label(km) or label
            out["_distance_km"] = round(km, 2)
        except (TypeError, ValueError):
            pass
    out["fuzzy_distance"] = label
    return out


def parse_query(text: str) -> Dict[str, Any]:
    city = None
    for c in ["台北", "台中", "高雄", "新北", "桃园", "台南", "基隆", "新竹", "香港", "澳门", "深圳", "广州", "上海", "北京", "杭州", "成都", "武汉", "南京", "东莞", "佛山", "珠海", "厦门", "福州"]:
        if c in text:
            city = c
            break
    prices = [int(p) for p in re.findall(r"(\d{3,6})", text)]
    price_min = price_max = None
    if len(prices) >= 2:
        price_min, price_max = min(prices), max(prices)
    elif len(prices) == 1:
        p = prices[0]
        price_min, price_max = int(p * 0.7), int(p * 1.3)
    raw = re.sub(r"[/／]+", " ", text)
    kw = raw
    if city:
        kw = kw.replace(city, " ")
    kw = re.sub(r"\d{3,6}", " ", kw)
    keywords = [t for t in re.split(r"[\s,，、|]+", kw) if t.strip()]
    return {"city": city, "price_min": price_min, "price_max": price_max, "keywords": keywords, "raw": text}


def clean_price(price: Any, price_text: Any) -> Optional[int]:
    """Numeric price used by the 价格档 filter: first number of the price text ("800P 1500PP" → 800).
    The client-sent number is only kept when the text has none and it looks sane."""
    from bot.services.feed_filters import parse_price
    parsed = parse_price(price_text)
    if parsed is not None:
        return parsed
    try:
        v = int(price) if price is not None else None
    except (TypeError, ValueError):
        return None
    return v if v is not None and 50 <= v <= 200000 else None


def district_key(raw: Any) -> str:
    """Group free-text districts: "静安区" and "静安" → "静安"; "浦东新区" stays whole."""
    d = re.sub(r"\s+", "", str(raw or ""))[:32]
    if len(d) >= 3 and d.endswith("区") and not d.endswith("新区"):
        d = d[:-1]
    return d


def district_variants(key: str) -> List[str]:
    k = district_key(key)
    return [k, k + "区"] if k else []


def is_public_listing(lamp: Dict[str, Any]) -> bool:
    """Public eligibility, separate from shop open/rest/wait/closed/unset state."""
    if not lamp or lamp.get("status") != LampStatus.ACTIVE.value:
        return False
    exp = lamp.get("expires_at")
    if exp is None:
        return True
    if isinstance(exp, str):
        try:
            exp = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        except ValueError:
            return False
    if not isinstance(exp, datetime):
        return False
    if exp.tzinfo:
        exp = exp.astimezone(timezone.utc).replace(tzinfo=None)
    return exp > datetime.utcnow()


async def district_counts(city: str | None, *, public_only: bool = False) -> Dict[str, Any]:
    """Active listings per district in a city (one GROUP BY)."""
    async with session_scope() as s:
        conds = [Lamp.status == LampStatus.ACTIVE.value]
        if public_only:
            conds.append(or_(Lamp.expires_at.is_(None), Lamp.expires_at > datetime.utcnow()))
        if city:
            conds.append(Lamp.city == city)
        rows = (await s.execute(
            select(func.trim(Lamp.district), func.count()).where(and_(*conds)).group_by(func.trim(Lamp.district))
        )).all()
    total = 0
    groups: Dict[str, int] = {}
    for name, n in rows:
        total += int(n)
        k = district_key(name) or ("__missing__" if public_only else "")
        if k:
            groups[k] = groups.get(k, 0) + int(n)
    items = sorted(({"key": k, "count": n} for k, n in groups.items()), key=lambda x: (-x["count"], x["key"]))
    return {"total": total, "items": items[:40]}


async def search_lamps(*, keyword: str | None = None, city: str | None = None, price_min: int | None = None, price_max: int | None = None, limit: int = 15, offset: int = 0, lat: float | None = None, lng: float | None = None, district: str | None = None, sort: str = "new", flt: Any = None) -> List[Dict[str, Any]]:
    by_reviews = sort == "reviews"
    async with session_scope() as s:
        conditions = [Lamp.status == LampStatus.ACTIVE.value]
        if getattr(flt, "public_only", False):
            conditions.append(or_(Lamp.expires_at.is_(None), Lamp.expires_at > datetime.utcnow()))
        if flt is not None:
            # 价格档: strict (面议 / no number is not in any band); 标签: any of the picked tags
            lo, hi = flt.band
            if lo is not None:
                conditions.append(and_(Lamp.price.is_not(None), Lamp.price >= lo))
            if hi is not None:
                conditions.append(and_(Lamp.price.is_not(None), Lamp.price < hi))
            if flt.tags:
                from bot.services.feed_filters import tag_variants
                conditions.append(Lamp.tags.overlap(tag_variants(flt.tags)))
        if city:
            conditions.append(Lamp.city == city)
        if district == "__missing__":
            conditions.append(or_(Lamp.district.is_(None), func.trim(Lamp.district) == ""))
        elif district and district_variants(district):
            conditions.append(func.trim(Lamp.district).in_(district_variants(district)))
        if price_min is not None:
            conditions.append(or_(Lamp.price.is_(None), Lamp.price >= price_min))
        if price_max is not None:
            conditions.append(or_(Lamp.price.is_(None), Lamp.price <= price_max))
        if keyword:
            parsed = parse_query(keyword)
            if parsed["city"] and not city:
                conditions.append(Lamp.city == parsed["city"])
            if parsed["price_min"] is not None and price_min is None:
                conditions.append(or_(Lamp.price.is_(None), Lamp.price >= parsed["price_min"]))
            if parsed["price_max"] is not None and price_max is None:
                conditions.append(or_(Lamp.price.is_(None), Lamp.price <= parsed["price_max"]))
            for kw in (parsed["keywords"] or [keyword]):
                like = f"%{kw}%"
                conditions.append(or_(
                    Lamp.title.ilike(like),
                    Lamp.description.ilike(like),
                    Lamp.city.ilike(like),
                    Lamp.district.ilike(like),
                    Lamp.price_text.ilike(like),
                    Lamp.approx_label.ilike(like),
                    cast(Lamp.tags, String).ilike(like),
                ))
        off = max(0, int(offset or 0))
        if by_reviews:
            # 评价多: approved review count, 置顶 cards stay on top (same as 最新上架)
            from bot.models import Review, ReviewStatus
            rc = (select(Review.lamp_id.label("lid"), func.count().label("n"))
                  .where(Review.status == ReviewStatus.APPROVED.value).group_by(Review.lamp_id).subquery())
            stmt = (select(Lamp, func.coalesce(rc.c.n, 0)).outerjoin(rc, rc.c.lid == Lamp.lamp_id).where(and_(*conditions))
                    .order_by(Lamp.feed_pinned.desc(), Lamp.feed_pin_order.asc(), func.coalesce(rc.c.n, 0).desc(), Lamp.created_at.desc())
                    .offset(off).limit(limit))
            items = []
            for lamp_row, n in (await s.execute(stmt)).all():
                d = _lamp_to_dict(lamp_row)
                d["review_count"] = int(n or 0)
                items.append(d)
        else:
            stmt = select(Lamp).where(and_(*conditions)).order_by(Lamp.feed_pinned.desc(), Lamp.feed_pin_order.asc(), Lamp.created_at.desc(), Lamp.updated_at.desc()).offset(off).limit(limit)
            res = await s.execute(stmt)
            items = [_lamp_to_dict(x) for x in res.scalars().all()]
    enriched = [attach_fuzzy_distance(x, lat, lng) for x in items]
    if by_reviews:
        pass  # SQL order is final
    elif lat is not None and lng is not None:
        enriched.sort(key=lambda x: (0 if x.get("feed_pinned") else 1, x.get("_distance_km") if x.get("_distance_km") is not None else 1e9))
    else:
        pinned = [x for x in enriched if x.get("feed_pinned")]
        fresh = [x for x in enriched if not x.get("feed_pinned")]
        pinned.sort(key=lambda x: int(x.get("feed_pin_order") or 0))
        fresh.sort(key=lambda x: str(x.get("created_at") or x.get("updated_at") or ""), reverse=True)
        enriched = pinned + fresh
    for x in enriched:
        x.pop("_distance_km", None)
    return enriched


async def list_active_lamps(city: str | None = None, limit: int = 20) -> List[Dict[str, Any]]:
    return await search_lamps(city=city, limit=limit)


async def get_lamp(lamp_id: str) -> Optional[Dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))
        lamp = res.scalar_one_or_none()
        return _lamp_to_dict(lamp) if lamp else None


async def create_lamp_from_post(user_id: int, city: str, title: str, tags: List[str], price: Optional[int], price_text: Optional[str], description: str, photos: List[str], authenticity_score: int = 80, *, district: Optional[str] = None, approx_lat: Optional[float] = None, approx_lng: Optional[float] = None, approx_label: Optional[str] = None, media: Optional[List[Dict[str, str]]] = None, publisher_role: Optional[str] = None) -> Dict[str, Any]:
    lamp_id = str(uuid.uuid4())
    media_list = list(media or [])
    if not media_list and photos:
        media_list = [{"type": "image", "url": p} for p in photos]
    photo_urls = [m["url"] for m in media_list if m.get("type") == "image"] or list(photos or [])
    price = clean_price(price, price_text)
    async with session_scope() as s:
        lamp = Lamp(lamp_id=lamp_id, user_id=user_id, city=city, title=title, tags=tags or [], price=price, price_text=price_text, description=description or "", photos=photo_urls, media=media_list, district=district, approx_lat=approx_lat, approx_lng=approx_lng, approx_label=approx_label, publisher_role=publisher_role, authenticity_score=authenticity_score, status=LampStatus.PENDING.value)
        s.add(lamp)
        await s.flush()
        return _lamp_to_dict(lamp)


async def approve_lamp(lamp_id: str) -> None:
    async with session_scope() as s:
        res = await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))
        lamp = res.scalar_one_or_none()
        if lamp:
            lamp.status = LampStatus.ACTIVE.value
            lamp.updated_at = datetime.utcnow()


async def reject_lamp(lamp_id: str) -> None:
    async with session_scope() as s:
        res = await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))
        lamp = res.scalar_one_or_none()
        if lamp:
            lamp.status = LampStatus.REJECTED.value
            lamp.updated_at = datetime.utcnow()


def format_lamp_card(lamp: Dict[str, Any]) -> str:
    tags = " · ".join(lamp.get("tags") or []) or "无标签"
    price = lamp.get("price_text") or (str(lamp.get("price")) if lamp.get("price") else "面议")
    title = lamp.get("title") or "未命名"
    city = lamp.get("city") or "未知"
    district = lamp.get("district") or ""
    loc = f"{city}" + (f" · {district}" if district else "")
    lines = [f"🌕 <b>{_h(str(title))}</b>", f"📍 {_h(loc)}  |  💰 {_h(str(price))}", f"🏷 {_h(tags)}", f"<code>{_h(str(lamp.get('lamp_id', '')))}</code>"]
    desc = (lamp.get("description") or "").strip()
    if desc:
        lines.append("")
        lines.append(_h(desc[:200]) + ("…" if len(desc) > 200 else ""))
    return "\n".join(lines)


async def backfill_prices(limit: int = 5000) -> int:
    """One pass at startup: re-derive lamps.price from price_text (fixes "800P 1500PP" → 8001500)."""
    import logging
    changed = 0
    async with session_scope() as s:
        rows = (await s.execute(select(Lamp).limit(limit))).scalars().all()
        for lamp in rows:
            want = clean_price(lamp.price, lamp.price_text)
            if want != lamp.price:
                lamp.price = want
                changed += 1
    if changed:
        logging.getLogger(__name__).info("price backfill: %d listing(s) fixed", changed)
    return changed
