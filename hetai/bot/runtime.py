"""构建并持有唯一 Dispatcher / 可选唯一 Bot。"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from hetai.bot.handlers import help_router, menu_router, start_router
from hetai.config import Settings

_dispatcher: Dispatcher | None = None
_bot: Bot | None = None


def build_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is not None:
        return _dispatcher
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(start_router)
    dp.include_router(help_router)
    dp.include_router(menu_router)
    _dispatcher = dp
    return dp


def reset_runtime() -> None:
    global _dispatcher, _bot
    from hetai.bot.handlers import help_router, menu_router, start_router

    if _dispatcher is not None:
        for router in (start_router, help_router, menu_router):
            if router in _dispatcher.sub_routers:
                _dispatcher.sub_routers.remove(router)
            router._parent_router = None
    _dispatcher = None
    _bot = None


def get_dispatcher() -> Dispatcher:
    return build_dispatcher()


def registered_router_names() -> list[str]:
    dp = get_dispatcher()
    return [r.name for r in dp.sub_routers]


async def setup_bot(settings: Settings) -> Bot | None:
    global _bot
    if not settings.bot_connected:
        _bot = None
        return None
    _bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    return _bot


async def close_bot() -> None:
    global _bot
    if _bot is not None:
        await _bot.session.close()
        _bot = None


def bot_status(settings: Settings) -> dict:
    return {
        "connected": settings.bot_connected,
        "dispatcher_routers": registered_router_names(),
        "ingress": settings.webhook_path or "/telegram/webhook",
        "single_bot": True,
        "frameworks": ["aiogram3"],
    }
