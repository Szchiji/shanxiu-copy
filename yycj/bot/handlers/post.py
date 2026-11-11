"""发布引导：投稿请走 Mini App（旧键盘兼容）。"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from bot.keyboards import remove_kb

router = Router(name="post")


@router.message(F.text.in_({"✨ 点亮灯笼", "✨ 发布"}))
async def post_redirect(message: Message) -> None:
    await message.answer(
        "✨ 发布请点左下角「首页」→ Mini App「发布」页。\n"
        "支持相册选图/视频（最多 9 个），提交后管理员审核。",
        reply_markup=remove_kb(),
    )
