"""城市分区：运营管理员只看 / 只能处理自己负责的城市。

配置存在 site_settings.ops_config["operator_cities"]（JSONB，已有列，无需迁移）：
    {"<运营 user_id>": ["深圳", "东莞"], ...}
- 超管（ADMIN_IDS）永远看全部。
- 没配城市的运营 = 全部城市（和以前一样，向后兼容）。
- 配了城市的运营：列表只返回这些城市；对别的城市的资料 / 评价 / 举报 / 轮播操作返回 403。
城市来源：资料 lamps.city；待审投稿 posts.lamp_data["city"]；评价 / 举报 / 轮播 / 会话经 lamp_id 找到资料的城市。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import HTTPException
from sqlalchemy import func, or_, select, union

from bot.db import session_scope
from bot.models import HomepagePin, Lamp, LampStatus, Post, PostStatus, Report, ReportStatus, Review, ReviewStatus
from bot.models import Session as ChatSession
from bot.services.admin_acl import is_super

UNKNOWN = "未知"
OUT_OF_SCOPE = "这条不在你负责的城市"


def city_in(col, cities: Iterable[str]):
    """SQL condition: column value is one of the cities ("未知" also matches empty / NULL)."""
    cs = list(cities or [])
    conds = [col.in_([c for c in cs if c != UNKNOWN])]
    if UNKNOWN in cs:
        conds += [col.is_(None), col == ""]
    return or_(*conds)


def clean_cities(raw: Any) -> List[str]:
    out: List[str] = []
    for c in raw or []:
        s = str(c or "").strip()[:32]
        if s and s not in out:
            out.append(s)
    return out[:50]


def clean_operator_cities(raw: Any) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        try:
            uid = int(k)
        except (TypeError, ValueError):
            continue
        cities = clean_cities(v if isinstance(v, (list, tuple)) else [])
        if uid and cities:
            out[str(uid)] = cities
    return out


async def _settings() -> Dict[str, Any]:
    from bot.services import home_service
    return await home_service.get_or_create_settings()


async def operator_cities() -> Dict[str, List[str]]:
    return clean_operator_cities((await _settings()).get("operator_cities") or {})


async def scope_for(admin_id: int) -> Optional[Set[str]]:
    """None = 全部城市；否则 = 允许的城市集合。"""
    if is_super(admin_id):
        return None
    cities = (await operator_cities()).get(str(int(admin_id)))
    return set(cities) if cities else None


async def effective(admin_id: int, city: Optional[str]) -> Optional[Set[str]]:
    """列表接口用：把 ?city= 和运营的城市范围合并。None = 不过滤。"""
    scope = await scope_for(admin_id)
    want = (city or "").strip()
    if want and want not in ("all", "全部"):
        if scope is not None and want not in scope:
            raise HTTPException(status_code=403, detail=OUT_OF_SCOPE)
        return {want}
    return scope


def allowed(cities: Optional[Set[str]], city: Optional[str]) -> bool:
    return cities is None or (city or UNKNOWN) in cities


async def ensure_city(admin_id: int, city: Optional[str]) -> None:
    scope = await scope_for(admin_id)
    if scope is not None and (city or UNKNOWN) not in scope:
        raise HTTPException(status_code=403, detail=OUT_OF_SCOPE)


# ---- resolve the city of a target -------------------------------------------------
async def city_of_lamp(lamp_id: str) -> Optional[str]:
    async with session_scope() as s:
        return (await s.execute(select(Lamp.city).where(Lamp.lamp_id == lamp_id))).scalar_one_or_none()


async def cities_of_lamps(lamp_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [x for x in {str(i) for i in lamp_ids or [] if i}]
    if not ids:
        return {}
    async with session_scope() as s:
        rows = (await s.execute(select(Lamp.lamp_id, Lamp.city, Lamp.title).where(Lamp.lamp_id.in_(ids)))).all()
    return {r[0]: {"city": r[1], "title": r[2]} for r in rows}


async def city_of_post(post_id: str) -> Optional[str]:
    async with session_scope() as s:
        data = (await s.execute(select(Post.lamp_data).where(Post.post_id == post_id))).scalar_one_or_none()
    return (data or {}).get("city") if isinstance(data, dict) else None


async def city_of_review(review_id: str) -> Optional[str]:
    async with session_scope() as s:
        lid = (await s.execute(select(Review.lamp_id).where(Review.review_id == review_id))).scalar_one_or_none()
    return await city_of_lamp(lid) if lid else None


async def city_of_report(report_id: str) -> Optional[str]:
    async with session_scope() as s:
        lid = (await s.execute(select(Report.lamp_id).where(Report.report_id == report_id))).scalar_one_or_none()
    return await city_of_lamp(lid) if lid else None


async def city_of_pin(pin_id: int) -> Optional[str]:
    async with session_scope() as s:
        lid = (await s.execute(select(HomepagePin.lamp_id).where(HomepagePin.id == int(pin_id)))).scalar_one_or_none()
    return await city_of_lamp(lid) if lid else None


async def city_of_session(session_id: str) -> Optional[str]:
    async with session_scope() as s:
        lid = (await s.execute(select(ChatSession.lamp_id).where(ChatSession.session_id == session_id))).scalar_one_or_none()
    return await city_of_lamp(lid) if lid else None


async def guard(admin_id: int, kind: str, target: Any) -> None:
    """单条操作前调用。超管 / 未分城市的运营直接放行（不查库）。"""
    scope = await scope_for(admin_id)
    if scope is None:
        return
    resolver = {
        "post": city_of_post,
        "review": city_of_review,
        "report": city_of_report,
        "lamp": city_of_lamp,
        "pin": city_of_pin,
        "session": city_of_session,
    }[kind]
    city = await resolver(target)
    if kind == "post":
        async with session_scope() as s:
            data=(await s.execute(select(Post.lamp_data).where(Post.post_id==target))).scalar_one_or_none() or {}
        if data.get("edit_workflow_v2") and data.get("edit_lamp_id"):
            current_city=await city_of_lamp(data["edit_lamp_id"])
            if (current_city or UNKNOWN) not in scope:
                raise HTTPException(status_code=403,detail=OUT_OF_SCOPE)
    if (city or UNKNOWN) not in scope:
        raise HTTPException(status_code=403, detail=OUT_OF_SCOPE)


# ---- users: no city column; a user belongs to the cities of their listings / reviews / reports
def _user_ids_in(cities: Set[str]):
    lamp_cities = select(Lamp.lamp_id).where(Lamp.city.in_(cities))
    return union(
        select(Lamp.user_id.label("uid")).where(Lamp.city.in_(cities)),
        select(Review.guest_id.label("uid")).where(Review.lamp_id.in_(lamp_cities)),
        select(Report.reporter_id.label("uid")).where(Report.lamp_id.in_(lamp_cities)),
        select(Post.user_id.label("uid")).where(Post.lamp_data["city"].astext.in_(cities)),
    )


async def user_city_map(user_ids: Iterable[int]) -> Dict[int, List[str]]:
    ids = list({int(x) for x in user_ids or [] if x})
    if not ids:
        return {}
    async with session_scope() as s:
        rows = (await s.execute(select(Lamp.user_id, Lamp.city).where(Lamp.user_id.in_(ids)).distinct())).all()
    out: Dict[int, List[str]] = {}
    for uid, city in rows:
        if city and city not in out.setdefault(int(uid), []):
            out[int(uid)].append(city)
    return out


async def user_in(user_id: int, cities: Set[str]) -> bool:
    async with session_scope() as s:
        sub = _user_ids_in(cities).subquery()
        row = (await s.execute(select(sub.c.uid).where(sub.c.uid == int(user_id)).limit(1))).first()
    return row is not None


async def guard_user(admin_id: int, user_id: int) -> None:
    scope = await scope_for(admin_id)
    if scope is None:
        return
    if not await user_in(user_id, scope):
        raise HTTPException(status_code=403, detail="这个用户不在你负责的城市")


async def filter_user_ids(cities: Set[str], user_ids: Iterable[int]) -> Set[int]:
    ids = list({int(x) for x in user_ids or []})
    if not ids:
        return set()
    async with session_scope() as s:
        sub = _user_ids_in(cities).subquery()
        rows = (await s.execute(select(sub.c.uid).where(sub.c.uid.in_(ids)))).all()
    return {int(r[0]) for r in rows}


# ---- per-city counts for the admin switcher ---------------------------------------
async def summary(scope: Optional[Set[str]], enabled: List[str]) -> Dict[str, Any]:
    async with session_scope() as s:
        pc = Post.lamp_data["city"].astext.label("city")
        posts = (await s.execute(
            select(pc, func.count()).where(Post.status == PostStatus.PENDING.value).group_by(pc)
        )).all()
        reviews = (await s.execute(
            select(Lamp.city, func.count()).select_from(Review).join(Lamp, Lamp.lamp_id == Review.lamp_id, isouter=True)
            .where(Review.status == ReviewStatus.PENDING.value).group_by(Lamp.city)
        )).all()
        reports = (await s.execute(
            select(Lamp.city, func.count()).select_from(Report).join(Lamp, Lamp.lamp_id == Report.lamp_id, isouter=True)
            .where(Report.status == ReportStatus.PENDING.value).group_by(Lamp.city)
        )).all()
        listed = (await s.execute(
            select(Lamp.city, func.count())
            .where(or_(Lamp.status == LampStatus.ACTIVE.value, Lamp.status == LampStatus.HIDDEN.value))
            .group_by(Lamp.city)
        )).all()
        feed = (await s.execute(
            select(Lamp.city, func.count())
            .where(Lamp.status == LampStatus.ACTIVE.value, Lamp.feed_pinned.is_(True)).group_by(Lamp.city)
        )).all()
        carousel = (await s.execute(
            select(Lamp.city, func.count()).select_from(HomepagePin).join(Lamp, Lamp.lamp_id == HomepagePin.lamp_id, isouter=True)
            .group_by(Lamp.city)
        )).all()
    cities: Dict[str, Dict[str, int]] = {}
    order: List[str] = list(enabled or [])

    def put(rows, key):
        for city, n in rows:
            c = (city or "").strip() or UNKNOWN
            if c not in order:
                order.append(c)
            cities.setdefault(c, {})[key] = cities.get(c, {}).get(key, 0) + int(n or 0)

    for rows, key in ((posts, "posts"), (reviews, "reviews"), (reports, "reports"), (listed, "listings"), (feed, "feed_pins"), (carousel, "carousel")):
        put(rows, key)
    keys = ("posts", "reviews", "reports", "listings", "feed_pins", "carousel")
    items = []
    for c in order:
        if scope is not None and c not in scope:
            continue
        row = {k: cities.get(c, {}).get(k, 0) for k in keys}
        row["pending"] = row["posts"] + row["reviews"] + row["reports"]
        items.append({"city": c, "enabled": c in (enabled or []), **row})
    total = {k: sum(x[k] for x in items) for k in (*keys, "pending")}
    return {"cities": items, "total": total}
