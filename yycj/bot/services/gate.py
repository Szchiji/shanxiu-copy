"""强制订阅检查。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from bot.services import home_service

logger = logging.getLogger(__name__)


async def required_chat_list() -> List[Dict[str, Any]]:
    settings = await home_service.get_or_create_settings()
    raw = settings.get("required_chats") or []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        chat_id = item.get("chat_id")
        if chat_id in (None, ""):
            continue
        out.append(
            {
                "chat_id": chat_id,
                "title": item.get("title") or "官方频道/群",
                "url": item.get("url") or "",
                "required": bool(item.get("required", True)),
            }
        )
    return out


async def check_subscriptions(user_id: int) -> Dict[str, Any]:
    chats = await required_chat_list()
    if not chats:
        return {"ok": True, "required": False, "missing": [], "chats": []}
    missing = []
    try:
        from bot.main import bot
    except Exception:
        return {"ok": False, "required": True, "missing": chats, "chats": chats, "error": "bot unavailable"}
    for ch in chats:
        if not ch.get("required"):
            continue
        try:
            cid = ch["chat_id"]
            try:
                cid = int(cid)
            except (TypeError, ValueError):
                pass
            member = await bot.get_chat_member(cid, user_id)
            status = getattr(member, "status", None)
            status = status.value if hasattr(status, "value") else str(status)
            if status in ("left", "kicked", "restricted") or status == "ChatMemberStatus.LEFT":
                missing.append(ch)
        except Exception:
            logger.exception("getChatMember failed chat=%s user=%s", ch.get("chat_id"), user_id)
            missing.append(ch)
    return {"ok": not missing, "required": True, "missing": missing, "chats": chats}
