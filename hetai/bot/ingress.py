"""唯一 webhook 入口：校验后将 Update 交给同一个 Dispatcher，并持久化处理结果。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

from aiogram.types import Update
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from hetai.bot.runtime import get_bot, get_dispatcher, get_outbound_session
from hetai.config import Settings
from hetai.db import session_scope
from hetai.models import ProcessedUpdate

_locks: dict[int, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


def secrets_equal(provided: str, expected: str) -> bool:
    """缺失或缺长度不一致时返回 False，不抛异常（避免 500）。"""
    if not expected or not provided:
        return False
    if not isinstance(provided, str) or not isinstance(expected, str):
        return False
    if len(provided) != len(expected):
        # 不同长度仍做一次填充比较，避免 compare_digest 抛错，也不提前暴露长度。
        digest_size = max(len(provided), len(expected), 1)
        left = hashlib.sha256(provided.encode()).digest()
        right = hashlib.sha256(expected.encode()).digest()
        hmac.compare_digest(left, right)
        return False
    return hmac.compare_digest(provided, expected)


async def _lock_for(update_id: int) -> asyncio.Lock:
    async with _locks_guard:
        lock = _locks.get(update_id)
        if lock is None:
            lock = asyncio.Lock()
            _locks[update_id] = lock
        return lock


def _infer_handler(payload: dict[str, Any]) -> str:
    message = payload.get("message") or payload.get("edited_message") or {}
    text = (message.get("text") or "") if isinstance(message, dict) else ""
    if text.startswith("/start"):
        return "start"
    if text.startswith("/help"):
        return "help"
    callback = payload.get("callback_query") or {}
    data = callback.get("data") if isinstance(callback, dict) else ""
    if data == "hetai:open":
        return "menu"
    if payload.get("callback_query"):
        return "callback"
    if payload.get("message"):
        return "message"
    return "other"


def _serialize_calls(session) -> list[dict[str, Any]]:
    if session is None:
        return []
    return list(session.calls)


def update_chat_id(payload: dict[str, Any]) -> int | None:
    for key in ("message", "edited_message", "callback_query", "my_chat_member", "chat_member"):
        block = payload.get(key) or {}
        if not isinstance(block, dict):
            continue
        chat = block.get("chat") or (block.get("message") or {}).get("chat") if key == "callback_query" else block.get("chat")
        if isinstance(chat, dict) and chat.get("id") is not None:
            try:
                return int(chat["id"])
            except (TypeError, ValueError):
                return None
    return None


async def dispatch_update(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    bot = get_bot()
    if bot is None:
        raise RuntimeError("Bot 运行时尚未初始化")
    session = get_outbound_session()
    before = len(getattr(session, "calls", []) or []) if session is not None else 0
    update = Update.model_validate(payload)
    from hetai.bot.guard import reset_update_chat_id, set_update_chat_id

    token = set_update_chat_id(update_chat_id(payload))
    try:
        await get_dispatcher().feed_update(bot, update)
    finally:
        reset_update_chat_id(token)
    new_calls = (getattr(session, "calls", []) or [])[before:] if session is not None else []
    handler = _infer_handler(payload)
    return {
        "handler": handler,
        "transport": settings.resolved_transport,
        "sent": bool(settings.outbound_live),
        "bot_connected": settings.bot_connected,
        "calls": list(new_calls),
        "summary": (
            f"dispatched:{handler};transport:{settings.resolved_transport};"
            f"sent={int(settings.outbound_live)};calls={len(new_calls)}"
        )[:256],
    }


async def process_telegram_payload(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("update_id") is None:
        raise HTTPException(status_code=400, detail="缺少 update_id")
    try:
        update_id = int(payload["update_id"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="update_id 无效") from exc

    lock = await _lock_for(update_id)
    async with lock:
        return await _process_locked(update_id, payload, settings)


async def _process_locked(
    update_id: int, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(ProcessedUpdate).where(ProcessedUpdate.update_id == update_id)
            )
        ).scalar_one_or_none()
        if row is not None and row.status == "processed":
            return {
                "ok": True,
                "replayed": True,
                "sent": False,
                "bot_connected": settings.bot_connected,
                "transport": settings.resolved_transport,
                "handler": None,
            }
        if row is None:
            session.add(
                ProcessedUpdate(
                    update_id=update_id,
                    handled=False,
                    status="processing",
                    detail="processing",
                    outcome="",
                )
            )
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                again = (
                    await session.execute(
                        select(ProcessedUpdate).where(ProcessedUpdate.update_id == update_id)
                    )
                ).scalar_one_or_none()
                if again is not None and again.status == "processed":
                    return {
                        "ok": True,
                        "replayed": True,
                        "sent": False,
                        "bot_connected": settings.bot_connected,
                        "transport": settings.resolved_transport,
                        "handler": None,
                    }
                if again is None:
                    raise HTTPException(status_code=500, detail="update 认领失败，可重试") from None
                again.status = "processing"
                again.handled = False
                again.detail = "processing"
        else:
            # failed 或残留 processing：持有进程内锁后再处理，允许重试，不把失败当成功。
            row.status = "processing"
            row.handled = False
            row.detail = "processing"

    try:
        outcome = await dispatch_update(payload, settings)
    except HTTPException:
        await _mark_failed(update_id, "http")
        raise
    except Exception as exc:
        await _mark_failed(update_id, f"{type(exc).__name__}: {exc}")
        raise HTTPException(status_code=500, detail="update 处理失败，可重试") from exc

    async with session_scope() as session:
        row = (
            await session.execute(
                select(ProcessedUpdate).where(ProcessedUpdate.update_id == update_id)
            )
        ).scalar_one()
        row.status = "processed"
        row.handled = True
        row.detail = outcome.get("summary") or "ok"
        row.outcome = json.dumps(outcome, ensure_ascii=False)

    return {
        "ok": True,
        "replayed": False,
        "sent": outcome["sent"],
        "bot_connected": settings.bot_connected,
        "transport": outcome["transport"],
        "handler": outcome["handler"],
        "calls": outcome["calls"],
    }


async def _mark_failed(update_id: int, reason: str) -> None:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(ProcessedUpdate).where(ProcessedUpdate.update_id == update_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return
        row.status = "failed"
        row.handled = False
        row.detail = f"failed:{reason}"[:256]
        row.outcome = json.dumps({"error": reason}, ensure_ascii=False)
