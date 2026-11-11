"""匿名会话。"""

from __future__ import annotations

from html import escape as _h

import logging
from typing import Any, Dict, Union

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from bot.keyboards import remove_kb, session_accept_kb, session_end_kb
from bot.services import anti_brush, search_service, session_service

logger = logging.getLogger(__name__)

router = Router(name="session")

# Old + new reply-keyboard labels so menu taps never hit the DB filter.
MENU_TEXTS = {
    # search
    "🔍 搜索灯笼",
    "🔍 搜索",
    # mine
    "🌕 我的月影",
    "🌕 我的",
    # publish
    "✨ 点亮灯笼",
    "✨ 发布",
    # report
    "📝 月影报告",
    "📝 报告",
    # help
    "❓ 帮助",
    # homepage webapp button variants
    "📱 打开首页",
    "打开首页",
    # cancel
    "取消",
}


class ActiveSessionFilter(BaseFilter):
    """仅当用户有进行中的会话时匹配，并把会话注入 handler。"""

    async def __call__(self, message: Message) -> Union[bool, Dict[str, Any]]:
        user = message.from_user
        if not user:
            return False
        text = message.text or ""
        # Keep startswith('/') for slash commands; Command objects also carry text.
        if text and (text in MENU_TEXTS or text.startswith("/")):
            return False
        try:
            sess = await session_service.get_active_for_user(user.id)
        except Exception:
            logger.exception(
                "ActiveSessionFilter: get_active_for_user failed for user_id=%s", user.id
            )
            return False
        if not sess:
            return False
        return {"active_session": sess}


@router.callback_query(F.data.startswith("session_request:"))
async def request_session(cb: CallbackQuery, bot: Bot) -> None:
    if not cb.from_user or not cb.data:
        return
    lamp_id = cb.data.split(":", 1)[1]
    lamp = await search_service.get_lamp(lamp_id)
    if not lamp or lamp.get("status") != "active":
        await cb.answer("资料不存在或未上架", show_alert=True)
        return
    if lamp["user_id"] == cb.from_user.id:
        await cb.answer("不能与自己发起会话", show_alert=True)
        return
    if not await anti_brush.check_session_request_rate(cb.from_user.id):
        await cb.answer("请求过于频繁", show_alert=True)
        return

    existing = await session_service.get_active_for_user(cb.from_user.id)
    if existing:
        await cb.answer("你已有进行中的会话，请先结束", show_alert=True)
        return

    sess = await session_service.create_request(lamp_id, cb.from_user.id, lamp["user_id"], lamp_title=lamp.get("title"))
    await cb.answer("已发送邀请")
    if cb.message:
        await cb.message.answer(
            f"已向对方发送会话邀请。\n会话码：<code>{sess['session_id'][:8]}</code>",
            reply_markup=remove_kb(),
        )
    try:
        await bot.send_message(
            lamp["user_id"],
            f"🌕 有人想就你的资料 <b>{_h(str(lamp.get('title') or ''))}</b> 发起匿名会话。\n"
            f"对方身份已遮蔽，接受后由机器人中转消息（24h 内有效）。",
            reply_markup=session_accept_kb(sess["session_id"]),
        )
    except Exception:
        pass


def _is_participant(sess, user_id: int) -> bool:
    return bool(sess) and user_id in (sess.get("user_a_id"), sess.get("user_b_id"))


@router.callback_query(F.data.startswith("session_accept:"))
async def accept_session(cb: CallbackQuery, bot: Bot) -> None:
    if not cb.from_user or not cb.data:
        return
    sid = cb.data.split(":", 1)[1]
    sess = await session_service.get_session(sid)
    if not sess or sess["user_b_id"] != cb.from_user.id:
        await cb.answer("无效请求", show_alert=True)
        return
    sess = await session_service.accept(sid)
    if not sess:
        await cb.answer("会话已失效", show_alert=True)
        return
    await cb.answer("已接受")
    text = (
        "🌕 会话已开启。\n"
        "直接在此对话框发消息即可匿名中转。\n"
        "可用按钮结束会话或好评。"
    )
    kb = session_end_kb(sid)
    if cb.message:
        await cb.message.answer(text, reply_markup=kb)
    try:
        await bot.send_message(sess["user_a_id"], text, reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data.startswith("session_reject:"))
async def reject_session(cb: CallbackQuery, bot: Bot) -> None:
    if not cb.data or not cb.from_user:
        return
    sid = cb.data.split(":", 1)[1]
    sess = await session_service.get_session(sid)
    if not sess or sess.get("user_b_id") != cb.from_user.id:
        await cb.answer("无效请求", show_alert=True)
        return
    await session_service.reject(sid)
    await cb.answer("已拒绝")
    if cb.message:
        await cb.message.answer("已拒绝该会话。")
    if sess:
        try:
            await bot.send_message(sess["user_a_id"], "对方拒绝了会话邀请。")
        except Exception:
            pass


@router.callback_query(F.data.startswith("session_end:"))
async def end_session_cb(cb: CallbackQuery, bot: Bot) -> None:
    if not cb.data or not cb.from_user:
        return
    sid = cb.data.split(":", 1)[1]
    if not _is_participant(await session_service.get_session(sid), cb.from_user.id):
        await cb.answer("无效请求", show_alert=True)
        return
    data = await session_service.end_session(sid, settle=True)
    await cb.answer("会话已结束")
    text = "🔚 会话已结束。"
    if cb.message:
        await cb.message.answer(text, reply_markup=remove_kb())
    if data and cb.from_user:
        peer = session_service.peer_id(data, cb.from_user.id)
        try:
            await bot.send_message(peer, text, reply_markup=remove_kb())
        except Exception:
            pass


@router.callback_query(F.data.startswith("session_praise:"))
async def praise_session(cb: CallbackQuery) -> None:
    if not cb.data or not cb.from_user:
        return
    sid = cb.data.split(":", 1)[1]
    sess = await session_service.get_session(sid)
    # Only the guest side (requester, user_a) may send a simple thank-you.
    if not sess or sess.get("user_a_id") != cb.from_user.id:
        await cb.answer("只有客人一方可以好评", show_alert=True)
        return
    await session_service.mark_praise(sid)
    await cb.answer("已记录好评")


@router.message(ActiveSessionFilter(), F.text | F.photo | F.video | F.document | F.voice | F.sticker)
async def relay_message(message: Message, bot: Bot, active_session: dict) -> None:
    """仅活跃会话中转，并落库完整消息。"""
    user = message.from_user
    if not user:
        return

    sess = active_session
    peer = session_service.peer_id(sess, user.id)
    name = session_service.anon_name(sess, user.id)
    role = session_service.role_for_user(sess, user.id)
    media = bool(message.photo or message.video or message.document or message.voice)
    await session_service.bump_activity(sess["session_id"], media=media)

    # 持久化
    if message.text:
        await session_service.save_message(
            sess["session_id"], role, content=message.text, media_type="text"
        )
    elif message.photo:
        await session_service.save_message(
            sess["session_id"],
            role,
            content=message.caption,
            media_type="photo",
            file_id=message.photo[-1].file_id,
        )
    elif message.video:
        await session_service.save_message(
            sess["session_id"],
            role,
            content=message.caption,
            media_type="video",
            file_id=message.video.file_id,
        )
    elif message.document:
        await session_service.save_message(
            sess["session_id"],
            role,
            content=message.caption,
            media_type="document",
            file_id=message.document.file_id,
        )
    elif message.voice:
        await session_service.save_message(
            sess["session_id"],
            role,
            content=None,
            media_type="voice",
            file_id=message.voice.file_id,
        )
    elif message.sticker:
        await session_service.save_message(
            sess["session_id"],
            role,
            content="[贴纸]",
            media_type="sticker",
            file_id=message.sticker.file_id,
        )

    prefix = f"👤 <b>{_h(str(name))}</b>：\n"
    try:
        if message.text:
            await bot.send_message(peer, prefix + _h(message.text))
        elif message.photo:
            await bot.send_photo(
                peer, message.photo[-1].file_id, caption=(prefix + _h(message.caption or ""))
            )
        elif message.video:
            await bot.send_video(
                peer, message.video.file_id, caption=(prefix + _h(message.caption or ""))
            )
        elif message.document:
            await bot.send_document(
                peer, message.document.file_id, caption=(prefix + _h(message.caption or ""))
            )
        elif message.voice:
            await bot.send_voice(peer, message.voice.file_id, caption=prefix)
        elif message.sticker:
            await bot.send_message(peer, prefix + "[贴纸]")
            await bot.send_sticker(peer, message.sticker.file_id)
    except Exception:
        await message.answer("对方暂时无法接收消息。")
