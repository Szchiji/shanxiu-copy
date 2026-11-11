"""今日开课 / 休息 回调。"""
from __future__ import annotations

from html import escape as _h

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.services.open_shift import apply_choice

router = Router(name="shift")


@router.callback_query(F.data.startswith("shift:"))
async def cb_shift(query: CallbackQuery) -> None:
    user = query.from_user
    parts = (query.data or "").split(":")
    if not user or len(parts) < 4:
        await query.answer()
        return
    _, action, lamp_id, day = parts[0], parts[1], parts[2], parts[3]
    msg = await apply_choice(lamp_id, user.id, action, day)
    await query.answer(msg[:180], show_alert=True)
    try:
        if query.message:
            await query.message.edit_reply_markup(reply_markup=None)
            await query.message.answer(_h(msg))
    except Exception:
        pass
