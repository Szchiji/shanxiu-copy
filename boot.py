"""把两套机器人装进同一个进程。

- 月影车姬：aiogram3 + FastAPI（bot.main 里的 bot / dp / api_router）
- 闪秀：python-telegram-bot 21 + Flask（app 包）

两边共用同一个 Bot Token，但只有整合层注册 webhook，谁也不许自己 polling，
否则 Telegram 会因为两个 getUpdates 互相踢掉。
"""

from __future__ import annotations

import contextvars
import logging
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
# 支持几种放置方式：yycj/shanxiu 放在 merged 里、放在 merged 旁边、放在 src/ 下、放在 vendor/ 下
_head = Path(__file__).resolve().parent
for _name in ("yycj", "shanxiu"):
    for _base in (_head, _head.parent, _head.parent / "src", _head / "vendor"):
        _cand = _base / _name
        if _cand.is_dir():
            if str(_cand) not in sys.path:
                sys.path.append(str(_cand))
            break

logger = logging.getLogger("merged.boot")

# 月影侧「没有一个处理器接住这条 update」的标记，由兜底 router 置位
_yy_fallback_hit: contextvars.ContextVar[bool] = contextvars.ContextVar("yy_fallback_hit", default=False)

# Telegram 全部 update 类型。合并后必须注册全集，否则闪秀的 channel_post、
# 成员变动会被 Telegram 直接丢掉（webhook 的 allowed_updates 是全局的）。
ALL_ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
    "message_reaction",
    "message_reaction_count",
    "inline_query",
    "chosen_inline_result",
    "callback_query",
    "shipping_query",
    "pre_checkout_query",
    "poll",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "chat_boost",
    "removed_chat_boost",
]


class Bridge:
    """两套系统的启动器与消息分发器。"""

    def __init__(self) -> None:
        self.yy = None            # bot.main 模块
        self.sx_app = None        # PTB Application
        self.flask_app = None
        self.sx_routes = None
        self.started = False

    # ---------- 启动 ----------

    async def start_yy(self, webapp_url: str) -> None:
        import bot.main as ycm  # noqa: PLC0415  必须在环境变量摆好之后 import

        self.yy = ycm
        self._install_yy_fallback(ycm)
        await ycm._startup()  # 连库、建表、调度器、菜单按钮、/admin 命令
        logger.info("月影车姬已就绪（Mini App=%s）", webapp_url or "未配置")

    def _install_yy_fallback(self, ycm) -> None:
        """在月影所有 router 之后挂一个兜底 router。

        aiogram 的匹配是「第一个命中的处理器处理完就停」，所以兜底只在一条 update
        没有任何处理器认领时才会执行——正好用来判断「这条要不要转给闪秀」。
        """
        from aiogram import Router

        if getattr(ycm, "_merged_fallback_installed", False):
            return
        router = Router(name="merged_yy_fallback")

        @router.message()
        async def _on_unhandled_message(message) -> None:  # noqa: ANN001
            _yy_fallback_hit.set(True)

        @router.callback_query()
        async def _on_unhandled_callback(query) -> None:  # noqa: ANN001
            _yy_fallback_hit.set(True)

        @router.edited_message()
        async def _on_unhandled_edit(message) -> None:  # noqa: ANN001
            _yy_fallback_hit.set(True)

        ycm.dp.include_router(router)
        ycm._merged_fallback_installed = True

    async def start_sx(self, flask_app=None) -> None:
        """启动闪秀：建表 → 建 PTB Application 与全部处理器与定时任务。

        flask_app 由整合层先建好（它要早于 HTTP 服务挂到 ASGI 上），这里不重复创建。
        """
        from telegram import Bot as PTGBot

        from sx_schema import fix_database_schema, init_database

        if flask_app is None:
            from app import create_app

            flask_app = create_app()
        self.flask_app = flask_app

        init_database(flask_app)
        fix_database_schema(flask_app)

        from app.modules.core import routes as sx_routes

        self.sx_routes = sx_routes

        # 闪秀自己会 setWebhook 到 <域名>/core/webhook，那会盖掉整合层的统一入口。
        # 启动期间把 setWebhook 变成空操作；整合层在最后统一注册一次。
        original_set_webhook = PTGBot.set_webhook

        async def _noop_set_webhook(self, *args, **kwargs):  # noqa: ANN001
            return True

        PTGBot.set_webhook = _noop_set_webhook  # type: ignore[assignment]
        try:
            await sx_routes.run_bot(flask_app)
        finally:
            PTGBot.set_webhook = original_set_webhook  # type: ignore[assignment]

        self.sx_app = sx_routes.global_ptb_app
        logger.info("闪秀已就绪（群管理 / 积分 / 抽奖 / 报告）")

    async def register_webhook(self, url: str, secret: str | None) -> bool:
        if not url:
            logger.warning("PUBLIC_URL 没配：webhook 没注册，机器人收不到消息")
            return False
        from aiogram.types import WebhookInfo  # noqa: F401

        await self.yy.bot.set_webhook(
            url=url,
            secret_token=secret or None,
            drop_pending_updates=False,
            allowed_updates=ALL_ALLOWED_UPDATES,
        )
        me = await self.yy.bot.get_me()
        logger.info("Webhook -> %s | Bot @%s", url, me.username)
        self.started = True
        return True

    # ---------- 分发 ----------

    async def feed_yy(self, data: Mapping[str, Any]) -> bool:
        """喂给月影，返回「有没有被接住」。"""
        from aiogram.types import Update as AIUpdate

        token = _yy_fallback_hit.set(False)
        try:
            update = AIUpdate.model_validate(data, context={"bot": self.yy.bot})
            await self.yy.dp.feed_update(self.yy.bot, update)
            return not _yy_fallback_hit.get()
        except Exception:
            logger.exception("月影处理 update 失败")
            # 出异常时算「没接住」会在闪秀那边再来一遍，容易双回复；这里算接住。
            return True
        finally:
            _yy_fallback_hit.reset(token)

    async def feed_sx(self, data: Mapping[str, Any]) -> None:
        from telegram import Update as TGUpdate

        if not self.sx_app:
            logger.warning("闪秀还没起来，丢弃一条 update")
            return
        try:
            update = TGUpdate.de_json(dict(data), self.sx_app.bot)
            await self.sx_app.process_update(update)
        except Exception:
            logger.exception("闪秀处理 update 失败")

    async def handle_update(self, data: Mapping[str, Any]) -> str:
        from dispatch import Target, decide

        decision = decide(data)
        if decision.target is Target.YY:
            await self.feed_yy(data)
            return f"月影({decision.reason})"
        if decision.target is Target.SX:
            await self.feed_sx(data)
            return f"闪秀({decision.reason})"

        handled = await self.feed_yy(data)
        if handled:
            return f"月影({decision.reason})"
        await self.feed_sx(data)
        return f"闪秀({decision.reason},月影未接住)"

    # ---------- 收尾 ----------

    async def shutdown(self) -> None:
        if self.sx_app is not None:
            try:
                await self.sx_app.stop()
                await self.sx_app.shutdown()
            except Exception:
                logger.exception("闪秀关闭失败")
        if self.yy is not None:
            try:
                await self.yy._shutdown()
            except Exception:
                logger.exception("月影关闭失败")


bridge = Bridge()
