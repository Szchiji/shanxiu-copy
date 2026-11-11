"""给没有封面的视频补 Telegram 缩略图并写回资料。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from sqlalchemy import select

from bot.db import session_scope
from bot.models import Lamp
from bot.services.media_urls import guess_kind, is_http

logger = logging.getLogger(__name__)


def _needs_thumb(m: Dict[str, Any]) -> bool:
    raw = str(m.get("file_id") or m.get("url") or "").strip()
    if not raw or is_http(raw):
        return False
    if m.get("thumb_file_id") or m.get("thumbnail"):
        return False
    return guess_kind(raw, m.get("type")) == "video"


async def fill_video_thumbs(lamp: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not lamp:
        return lamp
    media = list(lamp.get("media") or [])
    if not any(isinstance(m, dict) and _needs_thumb(m) for m in media):
        return lamp
    try:
        from bot.main import bot
        from bot.services.broadcast import resolve_media_chat
        chat = await resolve_media_chat()
    except Exception:
        return lamp
    if not chat:
        return lamp
    changed = False
    out: List[Any] = []
    for m in media:
        if not isinstance(m, dict) or not _needs_thumb(m):
            out.append(m)
            continue
        raw = str(m.get("file_id") or m.get("url") or "").strip()
        item = dict(m)
        try:
            msg = await bot.send_video(chat, raw, disable_notification=True)
            thumb = None
            if msg.video and getattr(msg.video, "thumbnail", None):
                thumb = msg.video.thumbnail.file_id
            if thumb:
                item["thumb_file_id"] = thumb
                item["preview_url"] = f"/api/media/file/{thumb}"
                changed = True
        except Exception:
            logger.exception("video thumb backfill failed")
        out.append(item)
    lamp["media"] = out
    if not changed:
        return lamp
    lid = str(lamp.get("lamp_id") or "")
    try:
        async with session_scope() as s:
            row = (await s.execute(select(Lamp).where(Lamp.lamp_id == lid))).scalar_one_or_none()
            if row is not None:
                row.media = out
    except Exception:
        logger.exception("persist video thumbs failed")
    return lamp
