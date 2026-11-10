"""本地 / dry-run 出站会话：记录 Bot API 调用，绝不发起 HTTP。"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType

from aiogram.client.default import Default


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, Default):
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if _plain(v) is not None}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


def _safe_method_payload(method: TelegramMethod[Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for name in (
        "chat_id",
        "text",
        "caption",
        "callback_query_id",
        "message_id",
        "data",
        "show_alert",
        "reply_markup",
    ):
        if not hasattr(method, name):
            continue
        value = _plain(getattr(method, name))
        if value is not None:
            payload[name] = value
    return payload



class LocalNoSendSession(BaseSession):
    """合台演示与 dry-run 传输使用。任何 make_request 都不得访问 Telegram 网络。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []

    def record(self, method: TelegramMethod[Any]) -> dict[str, Any]:
        entry = {"method": method.__api_method__, "payload": _safe_method_payload(method)}
        self.calls.append(entry)
        return entry

    async def close(self) -> None:
        return None

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        raise RuntimeError("local no-send session 不发起 HTTP")
        yield b""  # pragma: no cover

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        self.record(method)
        fake = self._fake_ok_payload(method)
        response = self.check_response(
            bot=bot,
            method=method,
            status_code=200,
            content=self.json_dumps(fake),
        )
        return response.result  # type: ignore[return-value]

    def _fake_ok_payload(self, method: TelegramMethod[Any]) -> dict[str, Any]:
        name = method.__api_method__
        if name in {"sendMessage", "editMessageText"}:
            chat_id = getattr(method, "chat_id", 0) or 0
            try:
                chat_id_int = int(chat_id)
            except (TypeError, ValueError):
                chat_id_int = 0
            text = getattr(method, "text", None) or ""
            return {
                "ok": True,
                "result": {
                    "message_id": len(self.calls),
                    "date": int(time.time()),
                    "chat": {"id": chat_id_int, "type": "private"},
                    "text": text or None,
                    "from": {"id": 1, "is_bot": True, "first_name": "HetaiLocal"},
                },
            }
        if name == "getMe":
            return {
                "ok": True,
                "result": {
                    "id": 1,
                    "is_bot": True,
                    "first_name": "HetaiLocal",
                    "username": "hetai_local_bot",
                },
            }
        return {"ok": True, "result": True}
