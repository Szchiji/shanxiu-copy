"""投稿通知管理员、审核结果通知老师。"""
from __future__ import annotations

from html import escape as _h

import logging
from typing import Any, Dict, List

from aiogram.types import InputMediaPhoto, InputMediaVideo

from bot.config import get_settings
from bot.keyboards import admin_post_kb
from bot.services import home_service, users
from bot.services.media_urls import is_http

logger = logging.getLogger(__name__)


def _role_label(role: str) -> str:
    return {"teacher": "老师", "guest": "客人", "merchant": "商家"}.get(role or "", role or "-")


async def notify_admins_new_post(post_id: str, user_id: int, lamp_data: Dict[str, Any]) -> None:
    from bot.main import bot

    u = await users.ensure_user(user_id)
    uname = u.get("username") or "-"
    nick = u.get("full_name") or "-"
    role = _role_label(lamp_data.get("publisher_role") or u.get("role") or "")
    media = list(lamp_data.get("media") or [])
    photos = list(lamp_data.get("photos") or [])
    caption = (
        f"🆕 新投稿 <code>{post_id[:8]}</code>\n"
        f"用户 ID：<code>{user_id}</code>\n"
        f"用户名：@{_h(str(uname))} · 昵称：{_h(str(nick))}\n"
        f"身份：{_h(str(role))}\n"
        f"城市：{_h(str(lamp_data.get('city') or ''))} {_h(str(lamp_data.get('district') or ''))}\n"
        f"称呼：{_h(str(lamp_data.get('title') or ''))}\n"
        f"价位：{_h(str(lamp_data.get('price_text') or '面议'))}\n"
        f"{_h(str(lamp_data.get('description') or '')[:500])}\n"
        f"媒体：{len(media) or len(photos)} 个"
    )
    kb = admin_post_kb(post_id)
    items: List[Dict[str, str]] = media or [{"type": "image", "url": p} for p in photos]
    sent = False
    group = []
    for i, m in enumerate(items[:9]):
        raw = (m.get("file_id") or m.get("url") or "").strip()
        if not raw or is_http(raw):
            continue
        cap = caption if i == 0 else None
        if (m.get("type") or "image") == "video":
            group.append(InputMediaVideo(media=raw, caption=cap))
        else:
            group.append(InputMediaPhoto(media=raw, caption=cap))
    for admin_id in get_settings().admin_id_list:
        try:
            if group:
                await bot.send_media_group(admin_id, media=group)
                await bot.send_message(admin_id, "点下方审核。", reply_markup=kb)
                sent = True
            else:
                await bot.send_message(admin_id, caption, reply_markup=kb)
                sent = True
        except Exception:
            logger.exception("notify admin %s failed", admin_id)
    if not sent:
        logger.warning("no admin received post %s", post_id)


async def notify_publisher_approved(user_id: int, title: str, album_url: str | None = None, city: str | None = None, lamp_id: str | None = None, *, raise_errors: bool = False, listing_active: bool = True, operation_id: str | None = None, actor_id: int | None = None) -> bool | None:
    from bot.main import bot
    from bot.services import bot_info

    from bot.services.home_feed import ops_for_city
    site = ops_for_city(await home_service.get_or_create_settings(), city)
    from bot.services import execution_log,feature_registry
    record=await execution_log.begin('approval_notification','notification',city=city,listing_id=lamp_id,operation_id=operation_id,actor_id=actor_id,revision=site.get('config_revision'),target='private')
    if not feature_registry.enabled(site,'approval_notification'):
        await execution_log.finish(record,'skipped','disabled')
        return None
    promo = (site.get("approve_promo_text") or "").strip()
    ident = await bot_info.get_bot_identity()
    uname = ident.get("username") or ""
    link = bot_info.bot_tme_url(uname)
    text = f"你的资料「{_h(title)}」已通过审核并上架。" if listing_active else f"你的资料「{_h(title)}」修改已通过审核，仍保持当前未上架状态；有效期不变。"
    if promo:
        text = f"{text}\n\n{promo}"
    if album_url:
        text += f"\n\n频道相册：{album_url}"
    if link:
        text += f"\n\n机器人：{link}"
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from bot.services.share_gate import open_card_kb
    from bot.services.broadcast import start_url
    from bot.services.message_buttons import approval_items,resolve,keyboard
    from bot.config import get_settings
    detail=start_url(uname,lamp_id) if lamp_id else ''
    if detail and site.get('approve_attach_detail',True):text+=f"\n\n资料详情：{detail}"
    kb=open_card_kb(lamp_id) if lamp_id else None
    detail_app=kb.inline_keyboard[0][0].web_app.url if kb and kb.inline_keyboard[0][0].web_app else ''
    markup=keyboard(resolve(approval_items(site),'approval',base=get_settings().normalized_webapp_url,
        detail_url=detail,detail_webapp=detail_app,album=album_url or ''))
    try:
        await bot.send_message(user_id, text, reply_markup=markup)
        await execution_log.finish(record,"sent")
        return True
    except Exception as exc:
        await execution_log.finish(record,"failed" if execution_log.reason(exc)!="delivery_unknown" else "unknown",execution_log.reason(exc))
        logger.exception("notify publisher approved failed")
        if raise_errors:
            raise
        return False
