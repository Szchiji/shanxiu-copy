"""审核通过后按模板推送到频道，尽量发相册；资料变更时同步编辑，下架时标记。

频道帖不带任何按钮（相册本来就挂不了按钮，单图也统一不挂）：文案末尾放文字链接
「查看详情 · 查看评价 · 写评价」，都是 t.me/<bot>?start=<s|r|w>_<id>，机器人先查必订，再发打开小程序的按钮。
模板里的用户内容一律 HTML 转义（花名里有 < & 也能发出去）。
"""
from __future__ import annotations

import html
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from aiogram.types import InputMediaPhoto, InputMediaVideo

from bot.services import home_service
from bot.services.media_urls import enrich_media

logger = logging.getLogger(__name__)

DEFAULT_TPL = "🌙 月影车姬 · 新上架\n{称呼}\n📍 {地点}\n💰 {价位}\n{标签}\n{简介}\n{链接}"
UNLIST_MARK = "\n\n——\n⚠️ 已下架"


def normalize_chat(chat: str) -> str:
    raw = (chat or "").strip()
    if not raw:
        return ""
    if raw.startswith("https://t.me/"):
        tail = raw.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        return tail if tail.lstrip("-").isdigit() else "@" + tail.lstrip("@")
    return raw


_CHAT_RE = re.compile(r"^(@[A-Za-z][A-Za-z0-9_]{3,31}|-?\d{5,20})$")


def valid_chat(raw: str) -> str:
    """Normalised @name / -100… id, or "" when it can't be a chat."""
    c = normalize_chat(raw)
    return c if c and _CHAT_RE.match(c) else ""


async def check_post_rights(chat: str) -> str:
    """"" when the bot can post in ``chat``; otherwise a plain Chinese reason for the admin."""
    c = valid_chat(chat)
    if not c:
        return f"频道格式不对：{str(chat)[:40]}（示例 @频道名 或 -100…）"
    from bot.main import bot
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(c, me.id)
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "chat not found" in msg:
            return f"找不到频道 {c}：检查频道名，或先把机器人拉进频道并设为管理员"
        if "forbidden" in msg or "not a member" in msg or "kicked" in msg:
            return f"机器人不在 {c} 里：请把机器人加为该频道管理员，并允许发消息"
        return f"检查 {c} 失败，请稍后再试"
    status = str(getattr(member, "status", "") or "").split(".")[-1].lower()
    if status not in ("administrator", "creator"):
        return f"机器人还不是 {c} 的管理员：请在频道里把机器人设为管理员，并允许发消息"
    if getattr(member, "can_post_messages", None) is False:
        return f"机器人在 {c} 没有「发消息」权限：请在频道管理员设置里打开"
    return ""


def album_link(chat: str, message_id: int) -> str:
    raw = normalize_chat(chat)
    if raw.startswith("@"):
        return f"https://t.me/{raw[1:]}/{message_id}"
    digits = raw.lstrip("-")
    if digits.startswith("100") and len(digits) > 6:
        digits = digits[3:]
    return f"https://t.me/c/{digits}/{message_id}"


def fill_template(tpl: str, lamp: Dict[str, Any], extras: Optional[Dict[str, Any]], link: str) -> str:
    extras = extras or {}
    city = str(lamp.get("city") or "")
    district = str(lamp.get("district") or "")
    loc = " · ".join(x for x in (city, district) if x)
    tags = " ".join("#" + str(t) for t in (lamp.get("tags") or []) if t)
    mapping = {
        "称呼": lamp.get("title") or "",
        "title": lamp.get("title") or "",
        "花名": lamp.get("title") or "",
        "城市": city,
        "简介": (lamp.get("description") or "").strip(),
        "desc": (lamp.get("description") or "").strip(),
        "价位": lamp.get("price_text") or "面议",
        "price": lamp.get("price_text") or "面议",
        "区域": district,
        "地区": loc or district or city,
        "大致位置": lamp.get("approx_label") or "",
        "标签": tags,
        "tags": tags,
        "地点": loc,
        "loc": loc,
        "链接": link or "",
        "link": link or "",
    }
    for key, val in extras.items():
        if key and not str(key).startswith("_"):
            mapping[str(key)] = "" if val is None else str(val)
    if mapping.get("联系") and not mapping.get("微信"):
        mapping["微信"] = mapping["联系"]
    if mapping.get("微信") and not mapping.get("联系"):
        mapping["联系"] = mapping["微信"]
    text = tpl or DEFAULT_TPL
    for key, val in mapping.items():
        text = text.replace("{" + key + "}", str(val))
    text = re.sub(r"\{[^}]{1,20}\}", "", text)
    cleaned = []
    for line in text.splitlines():
        s = line.rstrip()
        if s.endswith("：") or s.endswith(":"):
            continue
        cleaned.append(s)
    return "\n".join(cleaned).strip()[:1024]


def _media_items(lamp: Dict[str, Any]) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    for m in enrich_media(lamp.get("media"), lamp.get("photos"))[:10]:
        raw = str(m.get("file_id") or m.get("url") or "").strip()
        if not raw:
            continue
        items.append(((m.get("type") or "image"), raw))
    return items


def compact_id(lamp_id: str) -> str:
    return str(lamp_id or "").replace("-", "").lower()


LINK_LABELS = (("s", "查看详情"), ("r", "查看评价"), ("w", "写评价"))
# Telegram HTML (esp. iOS) collapses ordinary spaces between adjacent <a> tags, so the old
# " · ".join(...) looked stuck together as 查看详情查看评价写评价. Use a real U+00A0 NBSP;
# the named entity &nbsp; is not supported in Bot API HTML and shows up literally.
LINK_SEP_HTML = "\u00a0·\u00a0"


def link_labels_from_site(site: dict | None = None) -> list[tuple[str, str]]:
    """(kind, label) pairs; empty / whitespace label = that link is omitted."""
    site = site or {}
    out: list[tuple[str, str]] = []
    for kind, key, default in (
        ("s", "link_label_detail", "查看详情"),
        ("r", "link_label_reviews", "查看评价"),
        ("w", "link_label_write", "写评价"),
    ):
        raw = site[key] if key in site else default
        label = str(raw if raw is not None else default).strip()[:16]
        if label:
            out.append((kind, label))
    return out


def start_url(bot_username: str, lamp_id: str, kind: str = "s") -> str:
    """Bot deep link ``t.me/<bot>?start=<kind>_<32hex>`` — the bot runs the follow check, then sends a
    WebApp button to the right page. kind: s=详情, r=详情+评价, w=写评价. start 参数只允许 [A-Za-z0-9_-]，≤64。"""
    name = (bot_username or "").lstrip("@")
    lid = compact_id(lamp_id)
    if not name or len(lid) != 32:
        return ""
    prefix = kind if kind in ("s", "r", "w", "q") else "s"
    return f"https://t.me/{name}?start={prefix}_{lid}"


# kept for older callers / templates
startapp_url = start_url


def links_html(bot_username: str, lamp_id: str, *, labels=None, sep: str | None = None) -> str:
    """HTML deep-link line for channel captions. ``labels`` = iterable of (kind, label);
    omit / empty label hides that link. Separator is fixed NBSP·NBSP so iOS Telegram shows it."""
    parts = []
    for kind, label in (labels if labels is not None else LINK_LABELS):
        label = str(label or "").strip()[:16]
        if not label:
            continue
        u = start_url(bot_username, lamp_id, kind)
        if u:
            parts.append(f'<a href="{html.escape(u, quote=True)}">{html.escape(label)}</a>')
    joiner = LINK_SEP_HTML if sep is None else sep
    return joiner.join(parts)


def footer_text(site: dict | None = None, *, extra: str = "") -> str:
    """Plain footer using the same labels, hide-if-empty rules, and separator as channel HTML links.
    For admin preview (rendered as text, not HTML tags). ``extra`` is an optional trailing label
    such as the community location link text."""
    parts = [label for _, label in link_labels_from_site(site)]
    extra = str(extra or "").strip()[:16]
    if extra:
        parts.append(extra)
    return LINK_SEP_HTML.join(parts)


def caption_html(plain: str, links: str = "", *, limit: int = 1024) -> str:
    """Escape the filled template (user content may contain < & …) and append the HTML links.
    Telegram counts visible characters, so trim the plain text first, never the markup."""
    # visible budget ≈ labels + " · " between them (nbsp renders as one space each side)
    room = limit - (min(40, len(links) // 2) + 2 if links else 0)
    body = html.escape((plain or "").strip()[:max(0, room)], quote=False)
    return (body + ("\n\n" + links if links else "")).strip()


async def resolve_media_chat() -> Optional[str]:
    try:
        site = await home_service.get_or_create_settings()
        raw = normalize_chat(str(site.get("media_channel_id") or ""))
        if raw:
            return raw
    except Exception:
        logger.exception("resolve media chat from settings failed")
    from bot.config import get_settings
    chat = get_settings().media_storage_chat_id
    return str(chat) if chat else None


async def _remember(lamp_id: str, chat: str, mid: int, *, card_mid: Optional[int] = None) -> None:
    try:
        from bot.services.extras_store import remember_broadcast
        await remember_broadcast(lamp_id, chat, mid, card_mid=card_mid)
    except Exception:
        logger.exception("remember broadcast ref failed")


async def _caption(lamp: Dict[str, Any], extras: Optional[Dict[str, Any]], *, links: bool = True) -> tuple[str, str, str, None]:
    """(chat, caption_html, bot_username, None). No inline buttons on channel posts — the links
    「查看详情 · 查看评价 · 写评价」 sit at the end of the caption instead."""
    from bot.services.home_feed import ops_for_city
    site = ops_for_city(await home_service.get_or_create_settings(), lamp.get("city"))
    chat = normalize_chat(str(site.get("broadcast_channel") or ""))
    from bot.services import bot_info
    ident = await bot_info.get_bot_identity()
    bot_name = ident.get("username") or ""
    lid = str(lamp.get("lamp_id") or "")
    # {链接} is replaced by the 「查看详情 · 查看评价 · 写评价」 line at the end, so drop the bare URL
    # (falls back to the bot link only when we can't build the deep links)
    link = "" if (links and start_url(bot_name, lid, "s")) else (start_url(bot_name, lid, "s") or bot_info.bot_tme_url(bot_name))
    merged = {}
    if isinstance(lamp.get("extras"), dict):
        merged.update(lamp.get("extras") or {})
    merged.update(extras or {})
    merged.setdefault("聊天按钮", site.get("chat_cta_label") or "想聊聊")
    plain = fill_template(str(site.get("broadcast_template") or ""), lamp, merged, link)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    link_line = links_html(bot_name, lid, labels=link_labels_from_site(site)) if links else ""
    from bot.services.community import safe_url
    location = safe_url(site.get("community_location_url")) if site.get("community_enabled", True) else ""
    label = str(site.get("link_label_location") or "").strip()[:16]
    if links and location and label:
        location_link = f'<a href="{html.escape(location, quote=True)}">{html.escape(label)}</a>'
        link_line = LINK_SEP_HTML.join(x for x in (link_line, location_link) if x)
    caption = caption_html(plain, link_line)
    return chat, caption, bot_name, None


async def broadcast_listing(lamp: Dict[str, Any], extras: Optional[Dict[str, Any]] = None, *, force_new: bool = False) -> Optional[str]:
    """Post (or re-post) a listing card. Skip when this lamp already has a remembered channel message
    — only explicit future renewals use force_new=True; ordinary calls keep deduping."""
    from bot.services.extras_store import load_broadcast
    lid = str(lamp.get("lamp_id") or "")
    chat0, mid0 = await load_broadcast(lid)
    if not force_new and chat0 and str(mid0).isdigit():
        # already posted: keep the original, just refresh caption/buttons
        return await update_broadcast(lamp, extras)

    chat, caption, _bot, _kb = await _caption(lamp, extras)
    from bot.services import execution_log
    record=await execution_log.begin('listing_broadcast','repost' if force_new else 'new_post',city=lamp.get('city'),listing_id=lid,operation_id=lamp.get('_renew_operation_id'),target='channel')
    if not chat:
        await execution_log.finish(record,'not_configured','not_configured')
        return None
    from bot.main import bot
    items = _media_items(lamp)
    acknowledged=False
    try:
        msg = None
        card_mid = None
        if len(items) >= 2:
            # album: the first item's caption carries the links (no extra button card)
            group = []
            for i, (kind, raw) in enumerate(items):
                cap = caption if i == 0 else None
                if kind == "video":
                    group.append(InputMediaVideo(media=raw, caption=cap))
                else:
                    group.append(InputMediaPhoto(media=raw, caption=cap))
            msgs = await bot.send_media_group(chat, media=group)
            msg = msgs[0] if msgs else None
        elif len(items) == 1:
            kind, raw = items[0]
            if kind == "video":
                msg = await bot.send_video(chat, raw, caption=caption)
            else:
                msg = await bot.send_photo(chat, raw, caption=caption)
        else:
            msg = await bot.send_message(chat, caption, disable_web_page_preview=True)
        acknowledged=True
        mid = getattr(msg, "message_id", None)
        if mid:
            if force_new:
                # Renewal must retain the NEW reference before claiming full success.
                # Do not clear the old reference before sending, or delete any historical post.
                from bot.services.extras_store import remember_broadcast
                await remember_broadcast(lid, chat, int(mid), card_mid=None)
            else:
                await _remember(lid, chat, int(mid), card_mid=int(card_mid) if card_mid else None)
            await execution_log.finish(record,"sent")
            return album_link(chat, int(mid))
        await execution_log.finish(record,"unknown","delivery_unknown")
        return None
    except Exception as exc:
        code="delivery_unknown" if acknowledged else execution_log.reason(exc)
        await execution_log.finish(record,"unknown" if code=="delivery_unknown" else "failed",code)
        logger.exception("broadcast to %s failed", chat)
        return None


async def update_broadcast(lamp: Dict[str, Any], extras: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Refresh the original channel post's caption/text + buttons (price / info / renewal)."""
    from bot.services.extras_store import load_broadcast
    from bot.main import bot
    lid = str(lamp.get("lamp_id") or "")
    chat, mid = await load_broadcast(lid)
    if not chat or not str(mid).isdigit():
        return None
    _chat, caption, _bot, _kb = await _caption(lamp, extras)
    try:
        await bot.edit_message_caption(chat_id=chat, message_id=int(mid), caption=caption, reply_markup=None)
    except Exception as exc:
        if "not modified" not in str(exc).lower():
            try:
                await bot.edit_message_text(chat_id=chat, message_id=int(mid), text=caption, reply_markup=None,
                                            disable_web_page_preview=True)
            except Exception as exc2:
                if "not modified" not in str(exc2).lower():
                    logger.exception("edit original broadcast failed")
                    return None
    # posts made by the button version had a separate card: retire it
    card_mid = await _card_mid(lid)
    if card_mid:
        try:
            await bot.delete_message(chat_id=chat, message_id=int(card_mid))
        except Exception:
            try:
                await bot.edit_message_reply_markup(chat_id=chat, message_id=int(card_mid), reply_markup=None)
            except Exception:
                pass
    return album_link(chat, int(mid))


async def _card_mid(lamp_id: str) -> Optional[int]:
    try:
        from bot.services.extras_store import load_broadcast_card
        return await load_broadcast_card(lamp_id)
    except Exception:
        return None


async def mark_unlist(lamp_id: str, *, reason: str = "") -> None:
    """Append 已下架 to the channel post and drop the buttons. Never raises."""
    from bot.services.extras_store import load_broadcast
    from bot.main import bot
    chat, mid = await load_broadcast(lamp_id)
    if not chat or not str(mid).isdigit():
        return
    card_mid = await _card_mid(lamp_id)
    try:
        from bot.services import search_service
        lamp = await search_service.get_lamp(lamp_id) or {"lamp_id": lamp_id, "title": ""}
        _, caption, _, _ = await _caption(lamp, None, links=False)
        if "已下架" not in caption:
            caption += f"\n\n——\n⚠️ 已下架（{reason[:20]}）" if reason else UNLIST_MARK
        # photo / album head carries a caption; a text-only post needs edit_message_text
        for edit in (
            lambda text: bot.edit_message_caption(chat_id=chat, message_id=int(mid), caption=text, reply_markup=None),
            lambda text: bot.edit_message_text(chat_id=chat, message_id=int(mid), text=text, reply_markup=None),
        ):
            try:
                await edit(caption)
                break
            except Exception:
                continue
        if card_mid:
            try:
                await bot.edit_message_text(chat_id=chat, message_id=int(card_mid), text="—— 已下架 ——", reply_markup=None)
            except Exception:
                try:
                    await bot.edit_message_reply_markup(chat_id=chat, message_id=int(card_mid), reply_markup=None)
                except Exception:
                    pass
    except Exception:
        logger.exception("mark unlist on channel failed lamp=%s", str(lamp_id)[:8])


from bot.services import execution_trace as delivery

@delivery.traced("featured_review","publish",source="review_approval")
async def broadcast_review(review: Dict[str, Any], lamp: Dict[str, Any]) -> Optional[str]:
    """Optional 精选评价 post (ops setting broadcast_reviews, default off). Failures are logged only."""
    from bot.services import execution_trace as delivery
    from bot.services.home_feed import ops_for_city
    site = ops_for_city(await home_service.get_or_create_settings(), lamp.get("city"))
    await delivery.current().identify(city=lamp.get("city"),listing_id=lamp.get("lamp_id"),revision=site.get("config_revision"),target="channel")
    if not site.get("broadcast_reviews"):
        delivery.current().mark("skipped","disabled")
        return None
    from bot.services.home_feed import feature_chat
    chat = feature_chat(site, "broadcast_reviews")
    if not chat:
        delivery.current().mark("not_configured","not_configured")
        return None
    from bot.services import bot_info
    ident = await bot_info.get_bot_identity()
    bot_name = ident.get("username") or ""
    stars = int(review.get("stars") or 0)
    star = "★" * max(0, min(5, stars)) + "☆" * max(0, 5 - stars)
    title = html.escape(str(lamp.get("title") or "资料"))
    loc = html.escape(" · ".join(x for x in (lamp.get("city"), lamp.get("district")) if x))
    text = html.escape((review.get("text") or "").strip()[:400])
    body = f"🌟 精选评价 · {title}\n📍 {loc}\n{star}\n{text}".strip()
    detail = start_url(bot_name, str(lamp.get("lamp_id") or ""), "r")
    if detail:
        body += f'\n\n<a href="{html.escape(detail, quote=True)}">查看完整评价</a>'
    from bot.main import bot
    try:
        # text only: the public photo upload is gone and 约课凭证 never leave the admins
        msg = await delivery.current().call(lambda:bot.send_message(chat,body,disable_web_page_preview=True),"create",send=True)
        mid = getattr(msg, "message_id", None)
        if mid and review.get("review_id"):
            try:
                from bot.db import session_scope
                from bot.models import Review
                from sqlalchemy import select
                async with session_scope() as s:
                    row = (await s.execute(select(Review).where(Review.review_id == str(review["review_id"])))).scalar_one_or_none()
                    if row:
                        row.post_chat = str(chat)
                        row.post_mid = int(mid)
                        await s.flush()
            except Exception as exc:
                delivery.current().mark("unknown","delivery_unknown")
                logger.warning("remember review post failed (%s)",type(exc).__name__)
        return album_link(chat, int(mid)) if mid else None
    except Exception as exc:
        logger.warning("broadcast review failed (%s)",type(exc).__name__)
        return None
