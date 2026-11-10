"""同一 Dispatcher 上的处理器：/start 菜单指向同一应用。不发送真实外部消息除非 Bot 已连接且被显式调用。"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

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
    if not settings.bot_connected:
        text += "\n当前未连接真实 Telegram Bot，不会向外发送消息。"
    buttons = []
    if app_url:
        buttons.append({"text": "打开合台", "kind": "webapp", "url": f"{app_url}/"})
        if is_admin:
            buttons.append({"text": "管理", "kind": "webapp", "url": f"{app_url.replace('/app', '')}/admin"})
    else:
        buttons.append({"text": "应用尚未配置 WEBAPP_URL", "kind": "noop"})
    return {
        "text": text,
        "buttons": buttons,
        "commands": ["/start", "/help"],
        "single_bot": True,
    }


@start_router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    from hetai.config import get_settings

    settings = get_settings()
    plan = start_reply_plan(settings, is_admin=False)
    if not settings.bot_connected:
        return
    await message.answer(plan["text"])


@help_router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    from hetai.config import get_settings

    settings = get_settings()
    if not settings.bot_connected:
        return
    await message.answer("合台帮助：点菜单打开同一个应用。群管与目录都在里面。")


@menu_router.callback_query(F.data == "hetai:open")
async def cb_open(query: CallbackQuery) -> None:
    from hetai.config import get_settings

    settings = get_settings()
    if not settings.bot_connected:
        await query.answer("未连接真实 Bot", show_alert=False)
        return
    await query.answer()
