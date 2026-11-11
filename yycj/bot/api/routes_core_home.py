"""月影车姬 API 核心 — home / lamps / sessions。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Query

from bot.api.deps import get_current_user_id
from bot.api.routes_core_base import (
    SessionRequestBody,
    _ser_dt,
    _ser_lamp,
    router,
)
from bot.services import anti_brush, home_service, search_service, session_service, users
from bot.services.community import district_groups
from bot.services import pin_query
from bot.services.extras_store import load_extras, load_extras_bulk
from bot.services.open_shift import normalize_hours, shop_status

logger = logging.getLogger(__name__)


async def _with_shop(lamp: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(lamp or {})
    prefilled = bool(data.pop("_extras_bulk", False))
    if not prefilled and not data.get("extras") and data.get("lamp_id"):
        try:
            data["extras"] = await load_extras(data["lamp_id"])
        except Exception:
            data["extras"] = {}
    try:
        st = shop_status(data.get("extras") or {}, data.get("status"), data.get("expires_at"))
        data["open_code"] = st.get("code") or ""
        data["open_text"] = st.get("text") or ""
        data["hours_text"] = st.get("hours") or ""
        ex = data.get("extras") or {}
        data["hours"] = normalize_hours(ex.get("_hours") or ex.get("hours"))
    except Exception:
        data.setdefault("open_code", "")
        data.setdefault("open_text", "")
        data.setdefault("hours_text", "")
    return data


async def _prefill_extras(lamps) -> None:
    """One SELECT for every lamp's extras instead of one per card (N+1 on /api/home)."""
    todo = [x for x in lamps if isinstance(x, dict) and x.get("lamp_id") and not x.get("extras")]
    if not todo:
        return
    try:
        found = await load_extras_bulk([x["lamp_id"] for x in todo])
    except Exception:
        logger.exception("bulk extras failed")
        return
    for x in todo:
        if str(x["lamp_id"]) in found:
            x["extras"] = found[str(x["lamp_id"])]
            x["_extras_bulk"] = True


def _ser_card(lamp: Dict[str, Any]) -> Dict[str, Any]:
    full = _ser_lamp(lamp)
    media = full.get("media") or []
    photos = full.get("photos") or []
    card = {
        "lamp_id": full.get("lamp_id"),
        "title": full.get("title"),
        "city": full.get("city"),
        "district": full.get("district"),
        "price_text": full.get("price_text"),
        "tags": list(full.get("tags") or [])[:3],
        "cover_url": full.get("cover_url") or "",
        "feed_pinned": bool(full.get("feed_pinned")),
        "feed_pin_order": int(full.get("feed_pin_order") or 0),
        "media_count": len(media) or len(photos),
        "open_code": full.get("open_code") or lamp.get("open_code") or "",
        "open_text": full.get("open_text") or lamp.get("open_text") or "",
        "hours_text": full.get("hours_text") or lamp.get("hours_text") or "",
    }
    if lamp.get("review_count") is not None:  # only on sort=reviews
        card["review_count"] = int(lamp.get("review_count") or 0)
    return card


def entry_announcement(base_settings, settings, city, user_id):
    """Only this authenticated reader's popup scope; no arbitrary HTML or other user info."""
    text = str(settings.get("popup_announcement_text") or "").strip()[:3000]
    if not settings.get("popup_announcement_enabled") or not text:
        return None
    own = (base_settings.get("city_overrides") or {}).get(city or "", {})
    local = any(k.startswith("popup_announcement_") for k in own)
    return {"title": str(settings.get("popup_announcement_title") or "本城公告")[:40],
            "text": text, "scope_user": str(user_id), "scope_city": city if local else "*"}


@router.get("/home")
async def api_home(
    city: Optional[str] = None,
    q: Optional[str] = None,
    district: Optional[str] = Query(default=None, max_length=32),
    sort: str = Query(default="new", pattern="^(new|reviews|near)$"),
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    price: Optional[str] = Query(default=None, max_length=16),
    tags: Optional[str] = Query(default=None, max_length=160),
    service: Optional[str] = Query(default=None, pattern="^(all|open|rest)$"),
    open_now: int = Query(default=0, ge=0, le=1, alias="open"),
    radius: Optional[float] = Query(default=None, ge=0, le=50),
    at: Optional[str] = Query(default=None, max_length=32),
    coupon: int = Query(default=0, ge=0, le=1),
    place: int = Query(default=1, ge=0, le=1),
    map_results: int = Query(default=0, ge=0, le=1),
    limit: int = Query(default=3, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=500),
    user_id: int = Depends(get_current_user_id),
) -> Dict[str, Any]:
    base_settings = await home_service.get_or_create_settings()
    enabled = list(base_settings.get("enabled_cities") or [])
    strict_public = bool(map_results or service is not None)
    if open_now and service not in (None, "open"):
        raise HTTPException(status_code=400, detail="服务状态条件冲突，请重新选择")
    if strict_public and city and city not in enabled:
        raise HTTPException(status_code=400, detail="城市未开放，请重新选择")
    use_city = city if city else (enabled[0] if enabled else None)
    if use_city and enabled and use_city not in enabled:
        use_city = enabled[0] if enabled else None
    if strict_public and not use_city:
        raise HTTPException(status_code=400, detail="请先选择已开放城市")
    # Per-city overrides (后台 → 站点设置 → 城市); unset keys keep the site-wide value.
    from bot.services.home_feed import ops_for_city
    settings = ops_for_city(base_settings, use_city)
    page_size = int(settings.get("home_feed_page_size") or 3)
    if limit == 3 and page_size != 3:
        limit = page_size

    if map_results:
        limit, offset = 600, 0

    area_key = search_service.district_key(district) if district else ""
    from bot.services.feed_filters import CENTERS, Filter, distance_label
    known_regions = {search_service.district_key(k) for k in CENTERS.get(use_city, {})} | {"__missing__"}
    region_counts = None
    if strict_public:
        try:
            region_counts = await search_service.district_counts(use_city, public_only=True)
            if isinstance(region_counts, dict):
                known_regions.update(x["key"] for x in region_counts.get("items", []))
        except Exception:
            logger.exception("public region options failed")
            if area_key and area_key not in known_regions:
                raise HTTPException(status_code=503, detail="地区选项暂时无法加载，请重试") from None
        if area_key and area_key not in known_regions:
            raise HTTPException(status_code=400, detail="地区不属于当前城市，请重新选择")
    # 价格档 / 标签 / 营业中 / 距离: the same rules for the feed and the carousel.
    near_src = "gps" if (lat is not None and lng is not None) else ""
    if not near_src and at:
        # no location permission: the user picked a district as the centre
        from bot.services.feed_filters import center_of
        pt = center_of(use_city, at)
        if pt:
            lat, lng = pt
            near_src = "at"
    has_place = False
    try:
        from bot.services import places
        my_place = await places.get_point(user_id)
        has_place = bool(my_place)
        if not near_src and my_place and place:
            # 常用位置 (我的) is the default centre for 附近 / 距我近; never echoed back
            lat, lng = my_place
            near_src = "place"
    except Exception:
        logger.exception("load place failed")
    flt = Filter(price=price, tags=tags, open_only=bool(open_now), lat=lat, lng=lng, radius=radius, sort=sort,
                 exact_center=near_src != "at", service=service, public_only=strict_public)
    sort = flt.sort
    coupons_on = bool(settings.get("coupons_enabled", True))
    if coupon and coupons_on:
        # 「有优惠」 filter: only listings with a live coupon (computed per listing, like 营业中)
        from bot.services import coupons as coupon_svc
        flt.coupon_ids = await coupon_svc.live_lamp_ids(use_city)
    pins = []
    try:
        pins_raw = await pin_query.list_active_pins()
        await _prefill_extras([p.get("lamp") for p in pins_raw])
        for p in pins_raw:
            try:
                lamp = await _with_shop(p.get("lamp") or {})
                if strict_public and not search_service.is_public_listing(lamp):
                    continue
                if use_city and lamp.get("city") and lamp.get("city") != use_city:
                    continue
                # 地区筛选同时作用于轮播；该区没有轮播时前端自动隐藏轮播
                if area_key and (search_service.district_key(lamp.get("district")) or "__missing__") != area_key:
                    continue
                if not flt.match_sql_side(lamp) or not flt.match_python_side(lamp):
                    continue
                pins.append(
                    {
                        "id": p["id"],
                        "sort_order": p["sort_order"],
                        "expires_at": _ser_dt(p.get("expires_at")),
                        "lamp": _ser_card(lamp),
                    }
                )
            except Exception:
                logger.exception("serialize pin failed")
    except Exception:
        logger.exception("list pins failed")

    items = []
    scanned_count = 0
    computed = flt.needs_python
    try:
        items = await search_service.search_lamps(
            keyword=q,
            city=use_city,
            # 营业中 / 距离 are per-listing: take the city's matching set (small) and page it here
            limit=600 if computed else limit,
            offset=0 if computed else offset,
            lat=None if computed else lat,
            lng=None if computed else lng,
            district=(district or "").strip() or None,
            sort="reviews" if sort == "reviews" else "new",
            flt=flt,
        )
        scanned_count = len(items)
        if computed:
            await _prefill_extras(items)
            shop = [await _with_shop(x) for x in items]
            shop = [x for x in shop if flt.match_python_side(x)]
            if sort == "near":
                for x in shop:
                    x["_km"] = flt.distance(x)
                shop.sort(key=lambda x: (0 if x.get("feed_pinned") else 1,
                                         int(x.get("feed_pin_order") or 0) if x.get("feed_pinned") else 0,
                                         x["_km"] if x.get("_km") is not None else 1e9))
            elif sort != "reviews":
                pinned = sorted([x for x in shop if x.get("feed_pinned")], key=lambda x: int(x.get("feed_pin_order") or 0))
                fresh = sorted([x for x in shop if not x.get("feed_pinned")], key=lambda x: str(x.get("created_at") or ""), reverse=True)
                shop = pinned + fresh
            items = shop[offset: offset + limit]
        elif sort != "reviews":
            pinned = [x for x in items if x.get("feed_pinned")]
            fresh = [x for x in items if not x.get("feed_pinned")]
            pinned.sort(key=lambda x: int(x.get("feed_pin_order") or 0))
            fresh.sort(key=lambda x: str(x.get("created_at") or x.get("updated_at") or ""), reverse=True)
            items = pinned + fresh
    except Exception:
        logger.exception("home search failed")
        # Map search must not disguise a failed query as an empty viewport.
        # Preserve the historical list-mode contract; no internal error detail leaks.
        if map_results:
            raise HTTPException(status_code=503, detail="地图资料暂时无法加载，请重试") from None
        items = []

    announcement = None
    if settings.get("announcement_enabled") and settings.get("announcement_text"):
        announcement = {"text": settings["announcement_text"], "enabled": True}
    # Per-city announcement (set in 后台 → 城市) replaces the global one for that city only.
    city_ann = (settings.get("city_announcements") or {}).get(use_city or "")
    if isinstance(city_ann, dict) and city_ann.get("enabled") and str(city_ann.get("text") or "").strip():
        announcement = {"text": city_ann["text"], "enabled": True}

    await _prefill_extras(items)
    safe_items = []
    for x in items:
        try:
            card = _ser_card(x if computed else await _with_shop(x))
            if flt.center:
                # rounded estimate only; listing coordinates never leave the server
                card["distance"] = flt.label(x)
            safe_items.append(card)
        except Exception:
            logger.exception("serialize lamp failed")

    if coupons_on and safe_items:
        try:
            from bot.services import coupons as coupon_svc
            live = await coupon_svc.live_by_lamp([c.get("lamp_id") for c in safe_items])
            for c in safe_items:
                if c.get("lamp_id") in live:
                    c["coupon"] = live[c["lamp_id"]]
        except Exception:
            logger.exception("coupon badges failed")

    # 地区筛选：当前城市各区在架数（仅首屏返回）。轮播不受筛选影响。
    districts = region_counts
    if offset == 0 and districts is None:
        try:
            districts = await search_service.district_counts(use_city)
        except Exception:
            logger.exception("district counts failed")

    contacts = {}
    try:
        contacts = await _home_contacts(settings)
    except Exception:
        logger.exception("home contacts failed")

    return {
        "ok": True,
        "city": use_city,
        "enabled_cities": enabled,
        "announcement": announcement,
        "popup_announcement": entry_announcement(base_settings, settings, use_city, user_id),
        "pins": pins,
        "items": safe_items,
        "map_groups": district_groups(safe_items, use_city) if map_results else [],
        "map_truncated": bool(map_results and scanned_count >= 600),
        "district": search_service.district_key(district) if district else "",
        "sort": sort,
        "districts": districts,
        "map_filter_options": {"districts": [{"key": k, "label": "未标注地区" if k == "__missing__" else k} for k in sorted(known_regions)], "services": ["all", "open", "rest"]} if strict_public else None,
        "map_scanned_count": scanned_count if map_results else None,
        "filters": {"service": flt.service, "price": price or "", "tags": flt.tags, "open": int(flt.open_only),
                    "radius": flt.radius, "near": bool(flt.center), "active": flt.active_count(area_key),
                    "near_src": near_src if flt.center else "", "has_place": has_place},
        "count": len(safe_items),
        "limit": limit,
        "offset": offset,
        "has_more": len(safe_items) >= limit,
        "chat_cta_label": settings.get("chat_cta_label") or "想聊聊",
        "show_chat_cta": settings.get("show_chat_cta") is not False,
        "media_max_count": int(settings.get("media_max_count") or 6),
        "page_size": int(settings.get("home_feed_page_size") or 3),
        "carousel_interval_sec": int(settings.get("carousel_interval_sec") or 4),
        "coupon_display": (settings.get("coupon_display") or "badge,filter,list") if coupons_on else "",
        "contacts": contacts,
        "listing_fields": settings.get("listing_fields") or [],
        "review_form": {
            "public_photos": False,
            "proof_required": True,
        },
    }


async def _home_contacts(settings: Dict[str, Any]) -> Dict[str, Any]:
    from bot.services import bot_info
    ident = await bot_info.get_bot_identity()
    bot_url = bot_info.bot_tme_url(ident.get("username"))
    admin_url = bot_info.normalize_contact(settings.get("admin_contact") or "")
    return {
        "bot_username": ident.get("username") or "",
        "bot_url": bot_url,
        "admin_url": admin_url,
        "show_bot": bool(settings.get("show_bot_link", True)) and bool(bot_url),
        "show_admin": bool(settings.get("show_admin_link", True)) and bool(admin_url),
        "bot_label": settings.get("bot_btn_label") or "机器人",
        "admin_label": settings.get("admin_btn_label") or "管理员",
    }


@router.get("/home/facets")
async def api_home_facets(city: Optional[str] = None, user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    """筛选面板的选项：价格档（按本城价格自动分档）、常用标签、可估算距离的区。60 秒缓存。"""
    from sqlalchemy import select
    from bot.db import session_scope
    from bot.models import Lamp, LampStatus
    from bot.services import feed_filters as ff
    settings = await home_service.get_or_create_settings()
    enabled = list(settings.get("enabled_cities") or [])
    use_city = city if city in enabled else (enabled[0] if enabled else (city or ""))
    hit = ff.facets_cached(use_city)
    if hit:
        return hit
    async with session_scope() as s:
        rows = (await s.execute(
            select(Lamp.price, Lamp.tags, Lamp.district, Lamp.approx_lat)
            .where(Lamp.status == LampStatus.ACTIVE.value, Lamp.city == use_city)
        )).all()
    geo = sorted({search_service.district_key(d) for _p, _t, d, _a in rows if ff.center_of(use_city, d)})
    located = sum(1 for _p, _t, d, a in rows if a is not None or ff.center_of(use_city, d))
    data = {
        "ok": True,
        "city": use_city,
        "total": len(rows),
        "price_bands": ff.price_bands([p for p, _t, _d, _a in rows if p]),
        "unpriced": sum(1 for p, _t, _d, _a in rows if not p),
        "tags": ff.top_tags([t for _p, t, _d, _a in rows], n=12),
        "radii": list(ff.RADII_PINNED if any(a is not None for _p, _t, _d, a in rows) else ff.RADII),
        "geo_districts": [{"key": k} for k in geo],
        "located": located,
    }
    ff.facets_store(use_city, data)
    return data


@router.get("/cities")
async def api_cities(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    settings = await home_service.get_or_create_settings()
    return {"ok": True, "cities": settings.get("enabled_cities") or []}


@router.get("/lamps")
async def api_lamps(
    q: Optional[str] = None,
    city: Optional[str] = None,
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    limit: int = Query(default=20, ge=1, le=50),
    user_id: int = Depends(get_current_user_id),
) -> Dict[str, Any]:
    if not await anti_brush.check_search_rate(user_id):
        raise HTTPException(status_code=429, detail="搜索过于频繁，请稍后再试")
    items = await search_service.search_lamps(
        keyword=q, city=city, price_min=price_min, price_max=price_max, limit=limit, lat=lat, lng=lng
    )
    await _prefill_extras(items)
    out = []
    for x in items:
        out.append(_ser_lamp(await _with_shop(x)))
    return {"ok": True, "items": out, "count": len(out)}


@router.get("/lamps/{lamp_id}")
async def api_lamp_detail(
    lamp_id: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    user_id: int = Depends(get_current_user_id),
) -> Dict[str, Any]:
    lamp = await search_service.get_lamp(lamp_id)
    if not search_service.is_public_listing(lamp):
        raise HTTPException(status_code=404, detail="资料不存在或未上架")
    lamp = search_service.attach_fuzzy_distance(lamp, lat, lng)
    lamp.pop("_distance_km", None)
    lamp = await _with_shop(lamp)
    rep = await home_service.reputation_for_lamp(lamp_id)
    reviews = await home_service.list_reviews_for_lamp(lamp_id, approved_only=True)
    for r in reviews:
        r["audit_verified"] = bool(r.get("reviewed_at") and r.get("review_note") != "免审直接发布")
        r["created_at"] = _ser_dt(r.get("created_at"))
        r.pop("guest_id", None)
        r.pop("target_user_id", None)
    from bot.services.listing_stats import record_view_soon
    record_view_soon(lamp_id, user_id, lamp.get("user_id"))
    out = _ser_lamp(lamp)
    if isinstance(out.get("extras"), dict):
        # internal state (_open_today, _remind_*, _bc_*) is not for the public detail page
        out["extras"] = {k: v for k, v in out["extras"].items() if not str(k).startswith("_")}
    from bot.services.community import public_links
    from bot.services.home_feed import ops_for_city
    site = ops_for_city(await home_service.get_or_create_settings(), lamp.get("city"))
    return {"ok": True, "lamp": out, "reputation": rep, "reviews": reviews,
            "community_links": public_links(site)}


@router.post("/sessions/request")
async def api_session_request(
    body: SessionRequestBody,
    user_id: int = Depends(get_current_user_id),
) -> Dict[str, Any]:
    lamp = await search_service.get_lamp(body.lamp_id)
    if not lamp or lamp.get("status") != "active":
        raise HTTPException(status_code=404, detail="资料不存在或未上架")
    if (await _with_shop(lamp)).get("open_code") == "rest":
        raise HTTPException(status_code=409, detail="休息中，暂不联系")
    if lamp["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="不能与自己发起会话")
    u = await users.ensure_user(user_id)
    if not await anti_brush.check_session_request_rate(user_id):
        raise HTTPException(status_code=429, detail="请求过于频繁")
    existing = await session_service.get_active_for_user(user_id)
    if existing:
        raise HTTPException(status_code=400, detail="已有进行中的会话，请先结束")
    alias = (body.guest_alias or "").strip()
    if not alias:
        me = await users.ensure_user(user_id)
        alias = (me.get("guest_alias") or "").strip() or None
    sess = await session_service.create_request(
        body.lamp_id, user_id, lamp["user_id"], lamp_title=lamp.get("title"), guest_alias=alias
    )
    # 约课用优惠：the guest's unused, valid code for this teacher rides along in the notice
    held_all = []
    try:
        from bot.services import coupons as coupon_svc
        held_all = await coupon_svc.held_codes(user_id, lamp["user_id"])
    except Exception:
        logger.exception("coupon lookup failed")
    try:
        import html as _html
        from bot.keyboards import session_accept_kb
        from bot.main import bot
        note = ""
        if held_all:
            codes = "、".join(f"<code>{_html.escape(h['code'])}</code>（{_html.escape(h['text'])}）" for h in held_all)
            note = (f"\n🎟 对方带了优惠券：{codes}。"
                    f"\n核销：我的 · 我的优惠 → 输入券码 → 标记已用。")
        await bot.send_message(
            lamp["user_id"],
            f"🌕 有人通过月影车姬就你的资料 <b>{_html.escape(str(lamp.get('title') or ''))}</b> 发起匿名会话。\n"
            f"对方身份已遮蔽，接受后由机器人中转消息（24h 内有效）。" + note,
            reply_markup=session_accept_kb(sess["session_id"]),
        )
    except Exception:
        logger.exception("notify lamp owner failed")
    return {
        "ok": True,
        "session_id": sess["session_id"],
        "status": sess["status"],
        "coupon_code": held_all[-1]["code"] if held_all else None,
        "coupon_codes": [h["code"] for h in held_all],
        "message": "已发送邀请，券码已带上 · 已复制券码，发给老师即可" if held_all else "已发送想聊聊邀请，等待对方在 Bot 中接受",
    }
