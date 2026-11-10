"""同一 Dispatcher 上的处理器：/start、/help、菜单都指向同一应用。"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from hetai.config import Settings

start_router = Router(name="start")
help_router = Router(name="help")
menu_router = Router(name="menu")


def start_reply_plan(settings: Settings, *, is_admin: bool = False) -> dict:
    """纯函数：供 /start 与测试使用，描述统一菜单（指向同一应用）。"""
    app_url = (settings.webapp_url or "").rstrip("/")
    if settings.is_demo and not app_url:
        app_url = f"http://{settings.host}:{settings.port}/app"
    text = "欢迎使用合台。\n群管理、目录投稿、积分与信用都在同一个应用里。"
    if settings.resolved_transport == "dry-run":
        text += "\n当前为 dry-run：会处理更新并准备回复，但不会向 Telegram 发送消息。"
    elif not settings.bot_connected or settings.resolved_transport == "local":
        text += "\n当前未连接真实 Telegram Bot，不会向外发送消息。"
    buttons = []
    if app_url:
        buttons.append({"text": "打开合台", "kind": "webapp", "url": f"{app_url}/"})
        if is_admin:
            root = app_url[: -len("/app")] if app_url.endswith("/app") else app_url.replace("/app", "")
            buttons.append({"text": "管理", "kind": "webapp", "url": f"{root}/admin"})
    else:
        buttons.append({"text": "应用尚未配置 WEBAPP_URL", "kind": "noop"})
    return {
        "text": text,
        "buttons": buttons,
        "commands": ["/start", "/help"],
        "single_bot": True,
    }


def keyboard_from_plan(plan: dict) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    for button in plan.get("buttons") or []:
        kind = button.get("kind")
        if kind == "webapp" and button.get("url"):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=button.get("text") or "打开合台",
                        web_app=WebAppInfo(url=button["url"]),
                    )
                ]
            )
        elif kind == "callback":
            rows.append(
                [
                    InlineKeyboardButton(
                        text=button.get("text") or "菜单",
                        callback_data=button.get("data") or "hetai:open",
                    )
                ]
            )
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _plan_for(settings: Settings, telegram_id: int | None) -> dict:
    is_admin = bool(telegram_id) and int(telegram_id) in settings.admin_id_list
    return start_reply_plan(settings, is_admin=is_admin)


@start_router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    from hetai.config import get_settings

    settings = get_settings()
    uid = message.from_user.id if message.from_user else None
    plan = _plan_for(settings, uid)
    await message.answer(plan["text"], reply_markup=keyboard_from_plan(plan))


@help_router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    from hetai.config import get_settings

    settings = get_settings()
    uid = message.from_user.id if message.from_user else None
    plan = _plan_for(settings, uid)
    text = "合台帮助：点菜单打开同一个应用。群管与目录都在里面。"
    if not settings.outbound_live:
        text += "\n当前不会向 Telegram 外发真实消息。"
    await message.answer(text, reply_markup=keyboard_from_plan(plan))


@menu_router.callback_query(F.data == "hetai:open")
async def cb_open(query: CallbackQuery) -> None:
    from hetai.config import get_settings

    settings = get_settings()
    uid = query.from_user.id if query.from_user else None
    plan = _plan_for(settings, uid)
    await query.answer()
    if query.message and hasattr(query.message, "answer"):
        await query.message.answer(plan["text"], reply_markup=keyboard_from_plan(plan))
