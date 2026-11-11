"""待审评价 → 机器人通知管理员（带图 + 通过/拒绝/刷评按钮），并和后台审核保持同步。

收件人：超管（ADMIN_IDS）全部收到；后台添加的运营号只收到自己负责城市的（没配城市 = 全部）。
每位收件人发一条带按钮的消息（1 张图 = 图+说明；2–3 张 = 相册 + 带按钮的说明；无图 = 文字）。
发出的消息记在 review_notices 表里：
- 用来同步：机器人按钮或后台（单条 / 批量）处理后，把每位管理员那条消息改成「已通过/已拒绝」并去掉按钮；
- 也是「已通知」标记：补发只发从没通知过的。
通知失败只记日志，绝不影响提交。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from html import escape as _h
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select

from bot.db import session_scope
from bot.models import Review, ReviewStatus
from bot.models_extra import ReviewNotice

logger = logging.getLogger(__name__)
_TASKS: set = set()

STATUS_LABEL = {
    ReviewStatus.APPROVED.value: "✅ 已通过",
    ReviewStatus.REJECTED.value: "❌ 已拒绝",
    ReviewStatus.BRUSH.value: "🚫 已判为刷评",
}


def _bot():
    from bot.main import bot
    return bot


async def recipients(city: Optional[str]) -> List[int]:
    from bot.config import get_settings
    from bot.services import admin_acl, city_scope
    out: List[int] = []
    for uid in get_settings().admin_id_list:
        if uid > 0 and uid not in out:
            out.append(uid)
    await admin_acl.warm()
    ops = await city_scope.operator_cities()
    c = (city or "").strip() or city_scope.UNKNOWN
    for uid in sorted(admin_acl.extra_ids()):
        if uid in out or uid <= 0:
            continue
        scope = ops.get(str(uid))
        if not scope or c in scope:
            out.append(uid)
    return out


def _photo_ids(review: Dict[str, Any]) -> List[str]:
    out = []
    for p in review.get("photos") or []:
        s = str(p or "").strip()
        if s:
            out.append(s)
    return out[:3]


def _proof_ids(review: Dict[str, Any]) -> List[str]:
    """约课凭证 — admin DMs only."""
    out = []
    for p in review.get("proofs") or []:
        s = str(p or "").strip()
        if s:
            out.append(s)
    return out[:5]


_SEC = (("diff", "人照课表差异"), ("pros", "优点"), ("cons", "不足"), ("extra", "补充"))


async def _caption(review: Dict[str, Any], lamp: Dict[str, Any], limit: int = 4000) -> str:
    """Moderation text. 4000 for a text message (补充 up to 1000 字 fits), 1024 for a photo caption."""
    from bot.services import users
    gid = review.get("guest_id")
    who = f"<code>{gid}</code>"
    try:
        u = await users.ensure_user(int(gid))
        if u.get("username"):
            who += f" @{_h(str(u['username']))}"
        if u.get("guest_alias"):
            who += f" · {_h(str(u['guest_alias']))}"
    except Exception:
        pass
    stars = int(review.get("stars") or 0)
    photos = _photo_ids(review)
    proofs = _proof_ids(review)
    why = "带凭证，需人工审核" if proofs else "带图评价，需人工审核" if photos else "评价需审核"
    place = " ".join(x for x in [str(lamp.get("city") or ""), str(lamp.get("district") or "")] if x)
    text = str(review.get("text") or "").strip()
    sec = review.get("sections") or {}
    if isinstance(sec, dict) and any(sec.get(k) for k, _ in _SEC):
        body = "\n".join(f"<b>{lbl}</b>：{_h(str(sec.get(k))[:1000 if k == 'extra' else 300])}" for k, lbl in _SEC if sec.get(k))
    else:
        body = f"内容：{_h(text[:600]) if text else '（无文字）'}"
    return (
        f"📝 新评价待审核 <code>{_h(str(review.get('review_id') or '')[:8])}</code>\n"
        f"资料：{_h(str(lamp.get('title') or '-'))}{(' · ' + _h(place)) if place else ''}\n"
        f"打分：{'★' * stars}{'☆' * max(0, 5 - stars)}（{stars} 星）\n"
        f"客人：{who}\n"
        f"{body}\n"
        f"公开晒图：{len(photos)} 张 · 🔒约课凭证：{len(proofs)} 张（仅管理员）\n{why}"
    )[:limit]


async def _remember(review_id: str, chat_id: int, message_id: int, kind: str, body: str) -> None:
    async with session_scope() as s:
        s.add(ReviewNotice(review_id=review_id, chat_id=int(chat_id), message_id=int(message_id or 0), kind=kind, body=body))


async def notified(review_id: str) -> bool:
    async with session_scope() as s:
        row = (await s.execute(select(ReviewNotice.id).where(ReviewNotice.review_id == review_id).limit(1))).first()
        return row is not None


async def notify_new_review(review: Dict[str, Any], lamp: Optional[Dict[str, Any]] = None) -> int:
    """Send the moderation card to every admin in scope. Returns how many admins got it. Never raises."""
    try:
        rid = str(review.get("review_id") or "")
        if not rid or review.get("status") != ReviewStatus.PENDING.value:
            return 0
        if await notified(rid):
            return 0
        if lamp is None:
            from bot.services import search_service
            lamp = await search_service.get_lamp(str(review.get("lamp_id") or "")) or {}
        from aiogram.types import InputMediaPhoto
        from bot.keyboards import admin_review_kb
        bot = _bot()
        caption = await _caption(review, lamp, 1024 if len(_photo_ids(review)) == 1 else 4000)
        kb = admin_review_kb(rid)
        photos = _photo_ids(review)
        proofs = _proof_ids(review)
        to = await recipients(lamp.get("city"))
        sent = 0
        for uid in to:
            try:
                msg = None
                kind = "text"
                if proofs:
                    # 凭证 album first (labelled), then the photos/caption card below
                    try:
                        if len(proofs) == 1:   # a media group needs 2–10 items
                            await bot.send_photo(uid, proofs[0], caption="🔒 约课凭证（仅管理员可见）")
                        else:
                            await bot.send_media_group(uid, media=[
                                InputMediaPhoto(media=p, caption=("🔒 约课凭证（仅管理员可见）" if i == 0 else None))
                                for i, p in enumerate(proofs)
                            ])
                    except Exception:
                        logger.exception("review proofs to admin %s failed", uid)
                if len(photos) == 1:
                    try:
                        msg = await bot.send_photo(uid, photos[0], caption=caption, reply_markup=kb)
                        kind = "caption"
                    except Exception:
                        logger.exception("review photo to admin %s failed, sending text", uid)
                elif len(photos) > 1:
                    try:
                        await bot.send_media_group(uid, media=[InputMediaPhoto(media=p) for p in photos])
                    except Exception:
                        logger.exception("review album to admin %s failed, sending text", uid)
                if msg is None:
                    msg = await bot.send_message(uid, caption, reply_markup=kb)
                    kind = "text"
                await _remember(rid, uid, msg.message_id, kind, caption)
                sent += 1
            except Exception:
                logger.exception("notify admin %s of review %s failed", uid, rid[:8])
        if not sent:
            logger.warning("no admin received review %s (recipients=%d)", rid[:8], len(to))
            await _remember(rid, 0, 0, "none", "")  # don't retry forever; it is still in the backend list
        else:
            logger.info("review %s sent to %d admin(s)", rid[:8], sent)
        return sent
    except Exception:
        logger.exception("notify review failed")
        return 0


def notify_soon(review: Dict[str, Any], lamp: Optional[Dict[str, Any]] = None) -> None:
    """Fire-and-forget from the submit request: the guest never waits for Telegram."""
    try:
        t = asyncio.get_running_loop().create_task(notify_new_review(dict(review), lamp))
        _TASKS.add(t)
        t.add_done_callback(_TASKS.discard)
    except RuntimeError:
        pass


async def sync_handled(review_ids: Iterable[str], status: str, by: Optional[int] = None) -> int:
    """After 通过/拒绝/刷评 anywhere: update every admin's bot message and remove the buttons."""
    ids = [str(x) for x in review_ids if x]
    if not ids:
        return 0
    async with session_scope() as s:
        rows = (await s.execute(select(ReviewNotice).where(ReviewNotice.review_id.in_(ids), ReviewNotice.chat_id != 0))).scalars().all()
        notes = [(r.chat_id, r.message_id, r.kind, r.body) for r in rows]
    if not notes:
        return 0
    label = STATUS_LABEL.get(status, status)
    when = (datetime.utcnow() + timedelta(hours=8)).strftime("%m-%d %H:%M")
    tail = f"\n\n{label}（{when}{f' · 管理员 {by}' if by else ''}）"
    bot = _bot()
    done = 0
    for chat_id, message_id, kind, body in notes:
        try:
            text = (body or "")[: (1024 if kind == "caption" else 4096) - len(tail)] + tail
            if kind == "caption":
                await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=text, reply_markup=None)
            else:
                await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=None)
            done += 1
        except Exception as exc:
            if "not modified" not in str(exc).lower():
                logger.warning("sync review notice %s/%s failed: %s", chat_id, message_id, str(exc)[:120])
    return done


def sync_soon(review_ids: Iterable[str], status: str, by: Optional[int] = None) -> None:
    try:
        t = asyncio.get_running_loop().create_task(sync_handled(list(review_ids), status, by))
        _TASKS.add(t)
        t.add_done_callback(_TASKS.discard)
    except RuntimeError:
        pass


async def backfill_pending(hours: int = 24) -> int:
    """Notify pending reviews from the last `hours` that never got a bot notice (each one once)."""
    since = datetime.utcnow() - timedelta(hours=hours)
    from bot.services.home_service import _review_to_dict
    async with session_scope() as s:
        rows = (await s.execute(
            select(Review).where(Review.status == ReviewStatus.PENDING.value, Review.created_at >= since)
            .where(~Review.review_id.in_(select(ReviewNotice.review_id)))
            .order_by(Review.created_at.asc()).limit(50)
        )).scalars().all()
        items = [_review_to_dict(r, admin=True) for r in rows]
    n = 0
    for it in items:
        if await notify_new_review(it):
            n += 1
    if items:
        logger.info("review notice backfill: %d pending, %d sent", len(items), n)
    return n
