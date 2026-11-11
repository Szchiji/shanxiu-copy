"""老师优惠券：老师在「我的 · 我的优惠」自己发，客人「领取」拿到一个码，到店出示。

Rules (kept small on purpose):
  · 一次发布 = 一批优惠，最多 MAX_ITEMS 项（比如「100P 2张 / 200PP 3张 / 500夜课 4张」），整批共用有效期。
  · 每位老师同一时间最多 1 批有效；发新的会自动结束旧的。可以随时提前结束（整批）。
  · 有效期 1 / 2 / 3 / 7 天；每项内容最多 40 字、数量 1–200 张（必填）。
  · 每一批每位客人只能领 1 张（选其中一项），领到一个码；某项领满即止。
    数据库 (batch_id, user_id) 唯一，加上领取时锁住整批，并发也领不到第二张。
    老师看得到谁领了哪一项、可以标记「已用」。
  · 不需要先审核：发出即显示，后台可以按城市查看并停用（整批）。
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from sqlalchemy import or_, select, update

from bot.db import session_scope
from bot.models import Lamp, LampStatus, User
from bot.models_extra import Coupon, CouponClaim

PRESET_DAYS = (1, 2, 3, 7)
MAX_TEXT = 40
MAX_QUOTA = 200
MAX_ITEMS = 5          # 一批最多几项
# 「我的」里的整理规则（数据库里一直保留，后台统计不受影响）
DONE_HIDE_DAYS = 7     # 客人：已用 / 已失效的券，过了这么多天就不再显示
HISTORY_DAYS = 30      # 老师：往期优惠显示最近这么多天内结束的
HISTORY_MAX = 5        # 老师：往期优惠最多显示几批
_ALPHA = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class CouponError(ValueError):
    pass


def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(microsecond=0).isoformat() + "Z" if dt else None


def bkey(c: Coupon) -> str:
    """Batch key: batch_id, or the coupon's own id for rows written before batches existed."""
    return getattr(c, "batch_id", None) or c.coupon_id


def _in_batch(key: str):
    return or_(Coupon.batch_id == key, Coupon.coupon_id == key)


def left_label(ends_at: datetime, now: Optional[datetime] = None) -> str:
    sec = int((ends_at - (now or _now())).total_seconds())
    if sec <= 0:
        return "已结束"
    d, h = sec // 86400, (sec % 86400) // 3600
    if d >= 1:
        return f"还剩{d}天{h}小时" if h else f"还剩{d}天"
    if h >= 1:
        return f"还剩{h}小时"
    return f"还剩{max(1, sec // 60)}分钟"


def sold_out(c: Coupon) -> bool:
    return bool(c.quota and (c.claimed or 0) >= c.quota)


def is_live(c: Coupon, now: Optional[datetime] = None) -> bool:
    return c.status == "active" and c.ends_at > (now or _now()) and not sold_out(c)


def item_status(c: Coupon, now: Optional[datetime] = None) -> str:
    now = now or _now()
    if c.status != "active":
        return c.status
    return "expired" if c.ends_at <= now else ("sold_out" if sold_out(c) else "active")


def public(c: Coupon, mine: Optional[CouponClaim] = None) -> Dict[str, Any]:
    now = _now()
    out = {
        "coupon_id": c.coupon_id, "batch_id": bkey(c), "lamp_id": c.lamp_id, "city": c.city, "text": c.text,
        "ends_at": _iso(c.ends_at), "left": left_label(c.ends_at, now),
        "quota": int(c.quota or 0), "claimed": int(c.claimed or 0),
        "remain": (max(0, int(c.quota) - int(c.claimed or 0)) if c.quota else None),
        "live": is_live(c, now), "status": item_status(c, now),
    }
    if mine is not None:
        out["my_code"] = mine.code
        out["my_used"] = bool(mine.used_at)
        out["my_valid"] = code_valid(c, mine, now)
    return out


def batch_public(items: Sequence[Coupon], mine_by: Optional[Dict[str, CouponClaim]] = None) -> Dict[str, Any]:
    """One batch for the API. Top-level fields sum / join the items (a one-item batch looks exactly like the
    old single coupon); ``items`` lists every item with its own remain / live / my_code."""
    rows = sorted(items, key=lambda c: c.id or 0)
    its = [public(c, (mine_by or {}).get(c.coupon_id)) for c in rows]
    out = dict(its[0])
    if len(its) > 1:
        for k in ("my_code", "my_used", "my_valid"):
            out.pop(k, None)
    sts = {x["status"] for x in its}
    out.update({
        "batch_id": bkey(rows[0]), "items": its, "n_items": len(its),
        "text": " / ".join(x["text"] for x in its),
        "quota": sum(x["quota"] for x in its) if all(x["quota"] for x in its) else 0,
        "claimed": sum(x["claimed"] for x in its),
        "remain": sum(x["remain"] for x in its) if all(x["quota"] for x in its) else None,
        "live": any(x["live"] for x in its),
        "status": sts.pop() if len(sts) == 1 else ("active" if "active" in sts else its[0]["status"]),
        "my_codes": [{"coupon_id": x["coupon_id"], "text": x["text"], "code": x["my_code"], "used": x["my_used"],
                      "valid": x["my_valid"]} for x in its if x.get("my_code")],
    })
    return out


def code_valid(c: Coupon, cl: CouponClaim, now: Optional[datetime] = None) -> bool:
    """A claimed code is good until the coupon's own end time (not claim time + N), even if the teacher
    ended the coupon early or it sold out. Void once used or when the admin stopped the coupon."""
    return not cl.used_at and c.status != "stopped" and c.ends_at > (now or _now())


def clean_text(raw: Any) -> str:
    t = " ".join(str(raw or "").split())
    if not t:
        raise CouponError("请写优惠内容，比如「100P」")
    if len(t) > MAX_TEXT:
        raise CouponError(f"优惠内容最多 {MAX_TEXT} 个字")
    if "http" in t.lower() or "t.me" in t.lower() or "@" in t:
        raise CouponError("优惠内容里不能放链接或 @账号")
    return t


def clean_items(items: Any, legacy: bool = False) -> List[Dict[str, Any]]:
    """[{text, quota}] → cleaned. New form: 1–MAX_ITEMS items, 数量 required (1–MAX_QUOTA).
    ``legacy`` (old single-coupon body from a cached client): one item, quota 0 = 不限 still allowed."""
    if not isinstance(items, (list, tuple)) or not items:
        raise CouponError("请至少添加一项优惠")
    if len(items) > MAX_ITEMS:
        raise CouponError(f"一次最多 {MAX_ITEMS} 项优惠")
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for i, it in enumerate(items, 1):
        it = it if isinstance(it, dict) else {}
        pre = f"第 {i} 项：" if len(items) > 1 else ""
        try:
            text = clean_text(it.get("text"))
        except CouponError as e:
            raise CouponError(pre + str(e))
        try:
            q = int(it.get("quota") or 0)
        except (TypeError, ValueError):
            q = -1
        if legacy:
            if q < 0 or q > MAX_QUOTA:
                raise CouponError(f"限量填 1–{MAX_QUOTA} 张，或者不限")
        elif q < 1 or q > MAX_QUOTA:
            raise CouponError(f"{pre}数量填 1–{MAX_QUOTA} 张")
        if text.lower() in seen:
            raise CouponError(f"{pre}和前面的优惠重复了")
        seen.add(text.lower())
        out.append({"text": text, "quota": q})
    return out


async def _active_rows(s, user_id: int) -> List[Coupon]:
    """The teacher's current batch (newest active one)."""
    rows = (await s.execute(select(Coupon).where(Coupon.user_id == user_id, Coupon.status == "active")
                            .order_by(Coupon.id.desc()))).scalars().all()
    if not rows:
        return []
    key = bkey(rows[0])
    return sorted([c for c in rows if bkey(c) == key], key=lambda c: c.id)


async def _batch_rows(s, key: str) -> List[Coupon]:
    return list((await s.execute(select(Coupon).where(_in_batch(key)).order_by(Coupon.id))).scalars().all())


async def create(user_id: int, text: Any = None, days: Any = 2, quota: Any = 0,
                 items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    clean = clean_items(items) if items is not None else clean_items([{"text": text, "quota": quota}], legacy=True)
    try:
        d = int(days)
    except (TypeError, ValueError):
        d = 0
    if d not in PRESET_DAYS:
        raise CouponError("有效期只能选 1 / 2 / 3 / 7 天")
    async with session_scope() as s:
        lamp = (await s.execute(select(Lamp).where(Lamp.user_id == user_id, Lamp.status == LampStatus.ACTIVE.value)
                                .order_by(Lamp.id.desc()).limit(1))).scalar_one_or_none()
        if not lamp:
            raise CouponError("资料上架后才能发优惠")
        old = [x for (x,) in (await s.execute(select(Coupon.coupon_id).where(Coupon.user_id == user_id, Coupon.status == "active"))).all()]
        await s.execute(update(Coupon).where(Coupon.user_id == user_id, Coupon.status == "active").values(status="ended"))
        batch, ends = str(uuid.uuid4()), _now() + timedelta(days=d)
        rows = []
        for it in clean:
            c = Coupon(coupon_id=str(uuid.uuid4()), batch_id=batch, user_id=user_id, lamp_id=lamp.lamp_id, city=lamp.city or "",
                       text=it["text"], ends_at=ends, quota=it["quota"], claimed=0, status="active")
            s.add(c)
            await s.flush()     # ids in order → the first item is the batch head (carries the channel post)
            rows.append(c)
        out = batch_public(rows)
    await _after_change(old, "ended")
    await _publish(rows[0].coupon_id)
    return out


async def _publish(coupon_id: str) -> None:
    try:
        from bot.services import coupon_push
        await coupon_push.publish(coupon_id)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("coupon publish failed")


async def _after_change(coupon_ids, reason: str) -> None:
    """Close the channel post of each affected batch (close() resolves any item to its batch head; idempotent)."""
    from bot.services import coupon_push
    for cid in coupon_ids or []:
        try:
            await coupon_push.close(cid, reason)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("coupon post close failed")


async def _refresh_post(coupon_id: str) -> None:
    try:
        from bot.services import coupon_push
        await coupon_push.refresh(coupon_id)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("coupon post refresh failed")


async def end(user_id: int) -> bool:
    """提前结束 (whole batch): no new claims. Codes already claimed stay valid until the batch's own end time."""
    async with session_scope() as s:
        ids = [x for (x,) in (await s.execute(select(Coupon.coupon_id).where(Coupon.user_id == user_id, Coupon.status == "active"))).all()]
        await s.execute(update(Coupon).where(Coupon.user_id == user_id, Coupon.status == "active").values(status="ended"))
    await _after_change(ids, "ended")
    return bool(ids)


async def _claims_of(s, coupon_ids: List[str]):
    if not coupon_ids:
        return []
    return (await s.execute(select(CouponClaim, User.guest_alias).outerjoin(User, User.user_id == CouponClaim.user_id)
                            .where(CouponClaim.coupon_id.in_(coupon_ids)).order_by(CouponClaim.id.desc()))).all()


async def mine(user_id: int) -> Dict[str, Any]:
    now = _now()
    async with session_scope() as s:
        cur = await _active_rows(s, user_id)
        if cur and cur[0].ends_at <= now:
            for c in cur:
                c.status = "ended"
            cur = []
        has_lamp = (await s.execute(select(Lamp.id).where(Lamp.user_id == user_id, Lamp.status == LampStatus.ACTIVE.value).limit(1))).first() is not None
        claims: List[Dict[str, Any]] = []
        current = None
        if cur:
            by_id = {c.coupon_id: c for c in cur}
            for cl, alias in await _claims_of(s, list(by_id)):
                c = by_id[cl.coupon_id]
                claims.append({"code": cl.code, "who": alias or "客人", "used": bool(cl.used_at), "valid": code_valid(c, cl, now),
                               "state": claim_state(c, cl, now), "coupon_id": c.coupon_id, "item": c.text, "at": _iso(cl.created_at)})
            current = batch_public(cur)
            for it in current["items"]:
                mine_ = [x for x in claims if x["coupon_id"] == it["coupon_id"]]
                it.update(used=sum(1 for x in mine_ if x["used"]), pending=sum(1 for x in mine_ if x["state"] == "usable"))
            current["used"] = sum(it["used"] for it in current["items"])
            current["pending"] = sum(it["pending"] for it in current["items"])
        history = await _history(s, user_id, bkey(cur[0]) if cur else None, now)
        has_claims = (await s.execute(select(CouponClaim.id).join(Coupon, Coupon.coupon_id == CouponClaim.coupon_id)
                                      .where(Coupon.user_id == user_id).limit(1))).first() is not None
        return {"current": current, "claims": claims, "history": history, "history_days": HISTORY_DAYS,
                "can_issue": has_lamp, "has_claims": has_claims,
                "presets": list(PRESET_DAYS), "max_text": MAX_TEXT, "max_quota": MAX_QUOTA, "max_items": MAX_ITEMS}


def claim_state(c: Coupon, cl: CouponClaim, now: Optional[datetime] = None) -> str:
    """usable / used / void (expired or stopped)."""
    if cl.used_at:
        return "used"
    return "usable" if code_valid(c, cl, now) else "void"


def done_at(c: Coupon, cl: CouponClaim) -> Optional[datetime]:
    """When a code stopped being usable: used_at, else the coupon's own end time (also for 停用)."""
    return cl.used_at or c.ends_at


def _history_status(c: Coupon, now: datetime) -> str:
    if c.status == "stopped":
        return "已停用"
    if c.ends_at > now:
        return "已提前结束 · 已领的券码" + left_label(c.ends_at, now).replace("还剩", "还能用")
    return "已过期"


async def _history(s, user_id: int, current_key: Optional[str], now: datetime) -> List[Dict[str, Any]]:
    """Recently ended / stopped batches (newest first), each with per-item claims."""
    since = now - timedelta(days=HISTORY_DAYS)
    rows = (await s.execute(select(Coupon).where(Coupon.user_id == user_id, Coupon.ends_at >= since)
                            .order_by(Coupon.id.desc()).limit(HISTORY_MAX * MAX_ITEMS * 2))).scalars().all()
    batches: Dict[str, List[Coupon]] = {}
    for c in rows:
        k = bkey(c)
        if k == current_key:
            continue
        if k not in batches and len(batches) >= HISTORY_MAX:
            continue
        batches.setdefault(k, []).append(c)
    out: List[Dict[str, Any]] = []
    for k, items in batches.items():
        items = sorted(items, key=lambda c: c.id)
        by_id = {c.coupon_id: c for c in items}
        cls_by: Dict[str, List[Dict[str, Any]]] = {c.coupon_id: [] for c in items}
        for cl, alias in await _claims_of(s, list(by_id)):
            c = by_id[cl.coupon_id]
            cls_by[c.coupon_id].append({"code": cl.code, "who": alias or "客人", "used": bool(cl.used_at), "valid": code_valid(c, cl, now),
                                        "state": claim_state(c, cl, now), "coupon_id": c.coupon_id, "item": c.text,
                                        "can_undo": c.status != "stopped" and c.ends_at > now})
        its = [{"coupon_id": c.coupon_id, "text": c.text, "quota": int(c.quota or 0), "claimed": len(cls_by[c.coupon_id]),
                "used": sum(1 for x in cls_by[c.coupon_id] if x["used"]),
                "pending": sum(1 for x in cls_by[c.coupon_id] if x["state"] == "usable"), "claims": cls_by[c.coupon_id]}
               for c in items]
        head = items[0]
        out.append({"batch_id": k, "coupon_id": head.coupon_id, "text": " / ".join(c.text for c in items),
                    "ends_at": _iso(head.ends_at), "status": _history_status(head, now), "items": its,
                    "claimed": sum(x["claimed"] for x in its), "used": sum(x["used"] for x in its),
                    "pending": sum(x["pending"] for x in its), "claims": [y for x in its for y in x["claims"]]})
    return out


async def mark_used(user_id: int, code: str, used: bool = True) -> bool:
    code = (code or "").strip().upper()
    async with session_scope() as s:
        row = (await s.execute(select(CouponClaim).join(Coupon, Coupon.coupon_id == CouponClaim.coupon_id)
                               .where(Coupon.user_id == user_id, CouponClaim.code == code)
                               .order_by(CouponClaim.id.desc()).limit(1))).scalar_one_or_none()
        if not row:
            return False
        row.used_at = _now() if used else None
        return True


async def _viewer_claims(s, viewer: Optional[int], coupon_ids: List[str]) -> Dict[str, CouponClaim]:
    if not viewer or not coupon_ids:
        return {}
    return {cl.coupon_id: cl for cl in (await s.execute(select(CouponClaim).where(
        CouponClaim.coupon_id.in_(coupon_ids), CouponClaim.user_id == viewer))).scalars()}


async def for_lamp(lamp_id: str, viewer: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """The listing's current batch (every item with its own remain / 领取 / my_code)."""
    now = _now()
    async with session_scope() as s:
        rows = (await s.execute(select(Coupon).where(Coupon.lamp_id == lamp_id, Coupon.status == "active")
                                .order_by(Coupon.id.desc()))).scalars().all()
        if rows:
            key = bkey(rows[0])
            rows = [c for c in rows if bkey(c) == key]
        if not rows or rows[0].ends_at <= now:
            if viewer:   # teacher ended it early / it sold out: the guest's codes stay valid until ends_at
                got = (await s.execute(select(Coupon, CouponClaim).join(CouponClaim, CouponClaim.coupon_id == Coupon.coupon_id)
                                       .where(Coupon.lamp_id == lamp_id, CouponClaim.user_id == viewer)
                                       .order_by(CouponClaim.id.desc()).limit(10))).all()
                for c, cl in got:
                    if code_valid(c, cl, now):
                        items = await _batch_rows(s, bkey(c))
                        out = batch_public(items, await _viewer_claims(s, viewer, [x.coupon_id for x in items]))
                        out["own"] = False
                        return out
            return None
        out = batch_public(rows, await _viewer_claims(s, viewer, [c.coupon_id for c in rows]))
        out["own"] = bool(viewer and viewer == rows[0].user_id)
        return out


def _by_batch(rows: Iterable[Coupon]) -> Dict[str, List[Coupon]]:
    out: Dict[str, List[Coupon]] = {}
    for c in rows:
        out.setdefault(bkey(c), []).append(c)
    return {k: sorted(v, key=lambda c: c.id or 0) for k, v in out.items()}


async def live_by_lamp(lamp_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [x for x in lamp_ids if x]
    if not ids:
        return {}
    now = _now()
    async with session_scope() as s:
        rows = (await s.execute(select(Coupon).where(Coupon.lamp_id.in_(ids), Coupon.status == "active", Coupon.ends_at > now))).scalars().all()
    out: Dict[str, Dict[str, Any]] = {}
    for items in _by_batch(rows).values():
        live = [c for c in items if is_live(c, now)]
        if live:
            out[live[0].lamp_id] = {"text": " / ".join(c.text for c in live), "left": left_label(live[0].ends_at, now)}
    return out


async def live_lamp_ids(city: Optional[str]) -> Set[str]:
    now = _now()
    async with session_scope() as s:
        q = select(Coupon).where(Coupon.status == "active", Coupon.ends_at > now)
        if city:
            q = q.where(Coupon.city == city)
        rows = (await s.execute(q)).scalars().all()
    return {c.lamp_id for c in rows if is_live(c, now)}


async def claim(user_id: int, coupon_id: str) -> Dict[str, Any]:
    """Claim one item — at most ONE claim per guest per batch. Returns the whole batch
    (with this item's my_code at the top level for one-item batches)."""
    from sqlalchemy.exc import IntegrityError
    async with session_scope() as s:
        c = (await s.execute(select(Coupon).where(Coupon.coupon_id == coupon_id))).scalar_one_or_none()
        if not c:
            raise CouponError("优惠不存在")
        if c.user_id == user_id:
            raise CouponError("不能领自己的优惠")
        key = bkey(c)
        # lock every item of the batch (same order everywhere → no deadlock), then check this guest's claim in the batch
        await s.execute(select(Coupon.id).where(_in_batch(key)).order_by(Coupon.id).with_for_update())
        c = (await s.execute(select(Coupon).where(Coupon.coupon_id == coupon_id).execution_options(populate_existing=True))).scalar_one()
        items_ids = [x for (x,) in (await s.execute(select(Coupon.coupon_id).where(_in_batch(key)))).all()]
        had_any = (await s.execute(select(CouponClaim).where(CouponClaim.user_id == user_id, or_(
            CouponClaim.batch_id == key, CouponClaim.coupon_id.in_(items_ids))).order_by(CouponClaim.id).limit(1))).scalar_one_or_none()
        if had_any and had_any.coupon_id != coupon_id:
            raise CouponError("本批优惠每人限领 1 张，你已经领过了")
        had = had_any
        full = False
        if not had:
            if c.status != "active" or c.ends_at <= _now():
                raise CouponError("这个优惠已结束")
            if sold_out(c):
                raise CouponError("已经领完了")
            s.add(CouponClaim(coupon_id=coupon_id, batch_id=key, user_id=user_id, code="".join(secrets.choice(_ALPHA) for _ in range(6))))
            c.claimed = int(c.claimed or 0) + 1
            try:
                await s.flush()
            except IntegrityError:   # a parallel request won the (batch_id, user_id) unique index
                raise CouponError("本批优惠每人限领 1 张，你已经领过了")
            full = sold_out(c)
        items = await _batch_rows(s, bkey(c))
        mine_by = await _viewer_claims(s, user_id, [x.coupon_id for x in items])
        out = batch_public(items, mine_by)
        out["claimed_id"] = coupon_id
        out["claimed_code"] = mine_by[coupon_id].code
        all_full = full and all(sold_out(x) for x in items)
    if all_full:
        await _after_change([coupon_id], "sold_out")
    elif full:
        await _refresh_post(coupon_id)     # mark that item 已领完 on the channel post
    return out


async def my_claims(user_id: int) -> Dict[str, Any]:
    """客人「我的优惠券」: usable codes first; 已用 / 已失效 listed separately and hidden after DONE_HIDE_DAYS
    (rows stay in the DB). Each row is one item's code (``text`` = that item)."""
    now = _now()
    cut = now - timedelta(days=DONE_HIDE_DAYS)
    async with session_scope() as s:
        rows = (await s.execute(select(CouponClaim, Coupon, Lamp.title).join(Coupon, Coupon.coupon_id == CouponClaim.coupon_id)
                                .outerjoin(Lamp, Lamp.lamp_id == Coupon.lamp_id)
                                .where(CouponClaim.user_id == user_id).order_by(CouponClaim.id.desc()).limit(100))).all()
    usable: List[Dict[str, Any]] = []
    done: List[Dict[str, Any]] = []
    hidden = 0
    for cl, c, title in rows:
        st = claim_state(c, cl, now)
        d = public(c, cl)
        d["title"] = title or ""
        d["state"] = st
        if st == "usable":
            usable.append(d)
            continue
        when = done_at(c, cl)
        if when and when < cut:
            hidden += 1
            continue
        d["done_at"] = _iso(when)
        done.append(d)
    usable.sort(key=lambda x: x.get("ends_at") or "")   # soonest to expire first
    done.sort(key=lambda x: x.get("done_at") or "", reverse=True)
    return {"items": usable + done, "usable": len(usable), "done": len(done), "hidden": hidden, "hide_days": DONE_HIDE_DAYS}


async def city_list(city: Optional[str], limit: int = 30) -> List[Dict[str, Any]]:
    """本城优惠: one row per batch (live items only)."""
    now = _now()
    async with session_scope() as s:
        q = select(Coupon, Lamp).join(Lamp, Lamp.lamp_id == Coupon.lamp_id).where(
            Coupon.status == "active", Coupon.ends_at > now, Lamp.status == LampStatus.ACTIVE.value)
        if city:
            q = q.where(Coupon.city == city)
        rows = (await s.execute(q.order_by(Coupon.ends_at.asc(), Coupon.id.asc()).limit(limit * MAX_ITEMS * 2))).all()
    lamps = {c.coupon_id: lamp for c, lamp in rows}
    out = []
    for items in sorted(_by_batch([c for c, _ in rows]).values(), key=lambda v: v[0].ends_at):
        live = [c for c in items if is_live(c, now)]
        if not live:
            continue
        lamp = lamps[live[0].coupon_id]
        d = batch_public(live)
        d.update({"title": lamp.title, "district": lamp.district or "", "price_text": lamp.price_text or ""})
        out.append(d)
    return out[:limit]


async def admin_list(cities: Optional[Set[str]], status: str = "active", limit: int = 100) -> List[Dict[str, Any]]:
    """后台：one row per batch with its items."""
    now = _now()
    async with session_scope() as s:
        q = select(Coupon, Lamp.title).outerjoin(Lamp, Lamp.lamp_id == Coupon.lamp_id)
        if cities is not None:
            q = q.where(Coupon.city.in_(list(cities)))
        if status == "active":
            q = q.where(Coupon.status == "active", Coupon.ends_at > now)
        rows = (await s.execute(q.order_by(Coupon.id.desc()).limit(limit * MAX_ITEMS))).all()
    titles = {c.coupon_id: t for c, t in rows}
    groups = _by_batch([c for c, _ in rows])
    out = []
    for items in sorted(groups.values(), key=lambda v: -(v[0].id or 0))[:limit]:
        head = items[0]
        d = batch_public(items)
        d.update({"title": titles.get(head.coupon_id) or "", "user_id": head.user_id, "created_at": _iso(head.created_at),
                  "stopped_by": head.stopped_by})
        out.append(d)
    return out


async def admin_stop(coupon_id: str, admin_id: int) -> Optional[str]:
    """停用 the whole batch this item belongs to (its codes become void)."""
    async with session_scope() as s:
        c = (await s.execute(select(Coupon).where(Coupon.coupon_id == coupon_id))).scalar_one_or_none()
        if not c:
            return None
        for x in await _batch_rows(s, bkey(c)):
            x.status, x.stopped_by = "stopped", admin_id
        city = c.city
    await _after_change([coupon_id], "stopped")
    return city


async def lookup(user_id: int, code: str) -> Optional[Dict[str, Any]]:
    """老师核销: find a code among this teacher's coupons (current or earlier)."""
    code = (code or "").strip().upper()
    if not code:
        return None
    async with session_scope() as s:
        row = (await s.execute(select(CouponClaim, Coupon, User.guest_alias).join(Coupon, Coupon.coupon_id == CouponClaim.coupon_id)
                               .outerjoin(User, User.user_id == CouponClaim.user_id)
                               .where(Coupon.user_id == user_id, CouponClaim.code == code)
                               .order_by(CouponClaim.id.desc()).limit(1))).first()
    if not row:
        return None
    cl, c, alias = row
    return {"code": cl.code, "who": alias or "客人", "used": bool(cl.used_at), "valid": code_valid(c, cl),
            "text": c.text, "coupon_id": c.coupon_id, "ends_at": _iso(c.ends_at), "left": left_label(c.ends_at)}


async def held_codes(guest_id: int, teacher_id: int) -> List[Dict[str, Any]]:
    """Every unused, valid code the guest holds for this teacher (约课 / 想聊聊 carries them to the teacher)."""
    async with session_scope() as s:
        rows = (await s.execute(select(CouponClaim, Coupon).join(Coupon, Coupon.coupon_id == CouponClaim.coupon_id)
                                .where(Coupon.user_id == teacher_id, CouponClaim.user_id == guest_id)
                                .order_by(CouponClaim.id.desc()).limit(20))).all()
    out = [{"code": cl.code, "text": c.text, "ends_at": c.ends_at, "id": cl.id} for cl, c in rows if code_valid(c, cl)]
    return sorted(out, key=lambda x: x["id"])


async def held_code(guest_id: int, teacher_id: int) -> Optional[Dict[str, Any]]:
    """The guest's newest unused, valid code for this teacher."""
    codes = await held_codes(guest_id, teacher_id)
    return codes[-1] if codes else None
