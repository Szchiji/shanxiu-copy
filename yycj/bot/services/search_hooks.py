"""审核通过后写入上架有效期；搜索结果补 preview_url。"""
from __future__ import annotations

import logging

from bot.services import listing_ops, search_service
from bot.services.media_urls import enrich_media

logger = logging.getLogger(__name__)

_orig_approve = search_service.approve_lamp
_orig_search = search_service.search_lamps
_orig_get = search_service.get_lamp
_orig_to_dict = search_service._lamp_to_dict


def _with_preview(d):
    if not d:
        return d
    try:
        out = dict(d)
        media = enrich_media(out.get("media"), out.get("photos"))
        out["media"] = media
        out["photos"] = [m.get("preview_url") or m.get("url") for m in media if m.get("type") == "image"]
        return out
    except Exception:
        logger.exception("preview attach failed")
        return d


async def approve_lamp(lamp_id: str, *, days=None):
    await _orig_approve(lamp_id)
    try:
        await listing_ops.set_listing_expiry(lamp_id, days=days)
    except Exception:
        logger.exception("set expiry after approve failed")
    try:   # 有上新 → refresh that city's 按区目录 (debounced, only if enabled there)
        from bot.services import channel_directory
        await channel_directory.on_listing_live(lamp_id)
    except Exception:
        logger.exception("directory auto-refresh hook failed")


async def search_lamps(**kwargs):
    try:
        items = await _orig_search(**kwargs)
    except Exception:
        logger.exception("search failed")
        return []
    out = []
    for x in items or []:
        try:
            out.append(_with_preview(x))
        except Exception:
            out.append(x)
    return out


async def get_lamp(lamp_id: str):
    lamp = await _orig_get(lamp_id)
    if lamp and not lamp.get("extras"):
        try:
            from bot.services.extras_store import load_extras
            lamp["extras"] = await load_extras(lamp_id)
        except Exception:
            lamp["extras"] = {}
    return _with_preview(lamp)


def _lamp_to_dict(lamp):
    try:
        return _with_preview(_orig_to_dict(lamp))
    except Exception:
        return _orig_to_dict(lamp)


search_service.approve_lamp = approve_lamp  # type: ignore[assignment]
search_service.search_lamps = search_lamps  # type: ignore[assignment]
search_service.get_lamp = get_lamp  # type: ignore[assignment]
search_service._lamp_to_dict = _lamp_to_dict  # type: ignore[assignment]
