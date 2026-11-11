"""搜索灯笼（替代原 AI 媒婆）。"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.types import CallbackQuery, Message

from bot.keyboards import lamp_actions_kb, search_filters_kb
from bot.services import anti_brush, search_service, session_service, users

router = Router(name="search")

MENU_TEXTS = {
    "🔍 搜索灯笼",
    "🔍 搜索",
    "🌕 我的月影",
    "🌕 我的",
    "✨ 点亮灯笼",
    "✨ 发布",
    "📝 月影报告",
    "📝 报告",
    "❓ 帮助",
    "📱 打开首页",
    "📱 打开月影 Mini App",
    "取消",
}


class SearchState(StatesGroup):
    waiting_query = State()


class FreeTextSearchFilter(BaseFilter):
    """默认状态下、无活跃会话、非菜单的自由文本 → 搜索。"""

    async def __call__(self, message: Message, state: FSMContext) -> bool:
        if not message.text or len(message.text.strip()) < 2:
            return False
        if message.text.startswith("/") or message.text in MENU_TEXTS:
            return False
        current = await state.get_state()
        if current is not None:
            return False
        user = message.from_user
        if user:
            active = await session_service.get_active_for_user(user.id)
            if active:
                return False
        return True


def _filters_from_data(data: dict) -> dict:
    return {
        "city": data.get("city"),
        "price_min": data.get("price_min"),
        "price_max": data.get("price_max"),
    }


async def _run_search(message: Message, query: str, filters: dict | None = None) -> None:
    user = message.from_user
    if user and not await anti_brush.check_search_rate(user.id):
        await message.answer("搜索太频繁，请稍后再试。")
        return

    filters = filters or {}
    lamps = await search_service.search_lamps(
        keyword=query or None,
        city=filters.get("city"),
        price_min=filters.get("price_min"),
        price_max=filters.get("price_max"),
        limit=12,
    )
    if not lamps:
        await message.answer(
            "没有找到符合的灯笼。\n可换关键词，或点下方筛选城市/价位。",
            reply_markup=search_filters_kb(),
        )
        return

    await message.answer(f"🔍 找到 <b>{len(lamps)}</b> 盏灯笼：")
    for lamp in lamps:
        await message.answer(
            search_service.format_lamp_card(lamp),
            reply_markup=lamp_actions_kb(lamp["lamp_id"]),
        )


@router.message(F.text.in_({"🔍 搜索灯笼", "🔍 搜索"}))
async def search_redirect(message: Message) -> None:
    from bot.keyboards import remove_kb
    await message.answer(
        "🔍 搜索请点左下角「首页」打开 Mini App。\n"
        "也可直接发送关键词，例如 <code>台北 大学生</code>。",
        reply_markup=remove_kb(),
    )


@router.message(Command("search"))
async def search_entry(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchState.waiting_query)
    await message.answer(
        "请输入关键词，例如：\n"
        "• <code>台北 大学生</code>\n"
        "• <code>深圳 5000</code>\n"
        "• <code>香港 KH</code>\n\n"
        "也可先点筛选：",
        reply_markup=search_filters_kb(),
    )


@router.callback_query(F.data.startswith("search_city:"))
async def filter_city(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.data:
        await cb.answer()
        return
    city = cb.data.split(":", 1)[1]
    await state.update_data(city=city)
    await state.set_state(SearchState.waiting_query)
    await cb.answer(f"已选城市：{city}")
    if cb.message:
        await cb.message.answer(f"已筛选城市 <b>{city}</b>，请输入关键词或再选价位。")


@router.callback_query(F.data.startswith("search_price:"))
async def filter_price(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.data:
        await cb.answer()
        return
    rng = cb.data.split(":", 1)[1]
    lo, hi = rng.split("-")
    await state.update_data(price_min=int(lo), price_max=int(hi))
    await state.set_state(SearchState.waiting_query)
    await cb.answer(f"价位 {lo}-{hi}")
    if cb.message:
        await cb.message.answer(f"已筛选价位 <b>{lo} ~ {hi}</b>，请输入关键词继续搜索。")


@router.callback_query(F.data == "search_clear")
async def filter_clear(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer("已清除筛选")
    if cb.message:
        await cb.message.answer("筛选已清空，请重新输入关键词。", reply_markup=search_filters_kb())


@router.message(SearchState.waiting_query, F.text)
async def search_query(message: Message, state: FSMContext) -> None:
    if not message.text or message.text in MENU_TEXTS:
        await state.clear()
        return
    data = await state.get_data()
    await _run_search(message, message.text.strip(), _filters_from_data(data))
    await state.clear()


@router.message(StateFilter(default_state), FreeTextSearchFilter())
async def quick_search(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user:
        await users.ensure_user(user.id, username=user.username, full_name=user.full_name)
    await _run_search(message, (message.text or "").strip())
