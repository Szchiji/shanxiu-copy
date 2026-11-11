"""月影报告（Bot 侧兼容入口；主流程在 Mini App）。"""

from __future__ import annotations

from html import escape as _h

import uuid

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.db import session_scope
from bot.keyboards import admin_report_kb, remove_kb
from bot.models import Report, ReportStatus
from bot.services import anti_brush, search_service

router = Router(name="report")


class ReportForm(StatesGroup):
    lamp_id = State()
    reason = State()
    description = State()


@router.message(F.text.in_({"📝 月影报告", "📝 报告"}))
async def report_start(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user and not await anti_brush.check_report_rate(user.id):
        await message.answer("报告过于频繁，请稍后再试。", reply_markup=remove_kb())
        return
    await state.set_state(ReportForm.lamp_id)
    await message.answer(
        "请输入要报告的灯笼 ID（或在 Mini App 详情页点「举报」）。\n发送「取消」可退出。",
        reply_markup=remove_kb(),
    )


@router.callback_query(F.data.startswith("report_lamp:"))
async def report_from_card(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.data:
        return
    lamp_id = cb.data.split(":", 1)[1]
    await state.update_data(lamp_id=lamp_id)
    await state.set_state(ReportForm.reason)
    await cb.answer()
    if cb.message:
        await cb.message.answer(
            "请输入原因（如：虚假信息、盗图、态度恶劣、其他）。\n发送「取消」可退出。",
            reply_markup=remove_kb(),
        )


@router.message(ReportForm.lamp_id, F.text == "取消")
@router.message(ReportForm.reason, F.text == "取消")
@router.message(ReportForm.description, F.text == "取消")
async def cancel_report(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("已取消。", reply_markup=remove_kb())


@router.message(ReportForm.lamp_id, F.text)
async def report_lamp_id(message: Message, state: FSMContext) -> None:
    await state.update_data(lamp_id=(message.text or "").strip())
    await state.set_state(ReportForm.reason)
    await message.answer("请输入原因（如：虚假信息、盗图、态度恶劣）：")


@router.message(ReportForm.reason, F.text)
async def report_reason(message: Message, state: FSMContext) -> None:
    await state.update_data(reason=(message.text or "").strip()[:64])
    await state.set_state(ReportForm.description)
    await message.answer("请补充说明（可附细节，发送「无」跳过）：")


@router.message(ReportForm.description, F.text)
async def report_done(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    user = message.from_user
    if not user:
        return
    desc = (message.text or "").strip()
    if desc == "无":
        desc = ""

    lamp_id = data.get("lamp_id") or ""
    lamp = await search_service.get_lamp(lamp_id)
    report_id = str(uuid.uuid4())
    async with session_scope() as s:
        s.add(
            Report(
                report_id=report_id,
                lamp_id=lamp_id,
                reporter_id=user.id,
                reason=data.get("reason") or "未说明",
                description=desc[:2000],
                status=ReportStatus.PENDING.value,
            )
        )

    await message.answer("报告已提交，管理员将审核。", reply_markup=remove_kb())
    settings = get_settings()
    title = (lamp or {}).get("title") if lamp else "-"
    card = (
        f"⚠️ 新报告 <code>{report_id[:8]}</code>\n"
        f"灯笼：<code>{_h(str(lamp_id[:8]))}</code>… {_h(str(title or ''))}\n"
        f"举报人：{user.id}\n"
        f"原因：{_h(str(data.get('reason') or ''))}\n"
        f"{_h(desc[:300])}"
    )
    for admin_id in settings.admin_id_list:
        try:
            await bot.send_message(admin_id, card, reply_markup=admin_report_kb(report_id))
        except Exception:
            pass
