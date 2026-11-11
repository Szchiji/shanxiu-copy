"""注册路由。Bot 薄门：session + /start + /admin + 开课提醒。"""

from __future__ import annotations

from aiogram import Dispatcher

from bot.handlers import admin, session, shift, start

_registered = False


def register_handlers(dp: Dispatcher) -> None:
    global _registered
    if _registered:
        return
    if getattr(start.router, "parent_router", None) is not None:
        _registered = True
        return

    dp.include_router(session.router)
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(shift.router)
    _registered = True
