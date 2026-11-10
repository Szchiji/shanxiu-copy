"""构建并持有唯一 Dispatcher / 唯一 Bot（演示与 dry-run 使用本地无发送会话）。"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from hetai.bot.handlers import help_router, menu_router, start_router
from hetai.bot.session import LocalNoSendSession
from hetai.config import Settings

_dispatcher: Dispatcher | None = None
_bot: Bot | None = None
_session: LocalNoSendSession | None = None


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
    global _dispatcher, _bot, _session
    from hetai.bot.handlers import help_router, menu_router, start_router

    if _dispatcher is not None:
        for router in (start_router, help_router, menu_router):
            if router in _dispatcher.sub_routers:
                _dispatcher.sub_routers.remove(router)
            router._parent_router = None
    _dispatcher = None
    _bot = None
    _session = None


def get_dispatcher() -> Dispatcher:
    return build_dispatcher()


def get_bot() -> Bot | None:
    return _bot


def get_outbound_session() -> LocalNoSendSession | None:
    return _session


def registered_router_names() -> list[str]:
    dp = get_dispatcher()
    return [r.name for r in dp.sub_routers]


async def setup_bot(settings: Settings) -> Bot:
    """始终构造唯一 Bot。local/dry-run 使用无 HTTP 会话；live 才使用真实 API 会话。

    不会调用 setWebhook / deleteWebhook / getMe。
    """
    global _bot, _session
    transport = settings.resolved_transport
    token = settings.bot_token_for_runtime()
    if transport == "live":
        _session = None
        _bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    else:
        _session = LocalNoSendSession()
        _bot = Bot(
            token=token,
            session=_session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


async def close_bot() -> None:
    global _bot, _session
    if _bot is not None:
        await _bot.session.close()
        _bot = None
    _session = None


def bot_status(settings: Settings) -> dict:
    return {
        "connected": settings.bot_connected,
        "dispatcher_routers": registered_router_names(),
        "ingress": settings.webhook_path or "/telegram/webhook",
        "single_bot": True,
        "frameworks": ["aiogram3"],
        "transport": settings.resolved_transport,
        "outbound_live": settings.outbound_live,
    }
