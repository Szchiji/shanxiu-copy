"""自定义上架栏 + 频道原帖引用。改 extras 不冲掉 bc / 开课状态。"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, Tuple

from sqlalchemy import text

from bot.db import session_scope
from bot.services import listing_flow, search_service

logger = logging.getLogger(__name__)

_KEEP_PREFIX = ("_bc_", "_hours", "_open_", "_remind")


def _keep_internal(key: str) -> bool:
    k = str(key)
    return k.startswith("_")


def _clean_incoming(extras: Dict[str, Any] | None) -> Dict[str, Any]:
    incoming: Dict[str, Any] = {}
    for k, v in (extras or {}).items():
        if not k:
            continue
        if _keep_internal(str(k)):
            if v not in (None, ""):
                incoming[str(k)] = v if not isinstance(v, (dict, list)) else v
            continue
        if v not in (None, ""):
            incoming[str(k)] = str(v)
    return incoming


async def load_extras(lamp_id: str) -> Dict[str, Any]:
    if not lamp_id:
        return {}
    async with session_scope() as s:
        try:
            row = (await s.execute(text("SELECT extras FROM lamps WHERE lamp_id = :id"), {"id": lamp_id})).first()
            raw = row[0] if row else {}
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}


async def load_extras_bulk(lamp_ids) -> Dict[str, Dict[str, Any]]:
    """One query for many lamps (avoids N+1 in the shift scheduler)."""
    ids = [str(x) for x in (lamp_ids or []) if x]
    if not ids:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    async with session_scope() as s:
        try:
            rows = (
                await s.execute(text("SELECT lamp_id, extras FROM lamps WHERE lamp_id = ANY(:ids)"), {"ids": ids})
            ).all()
        except Exception:
            logger.exception("bulk load extras failed")
            return {}
    for lid, raw in rows:
        out[str(lid)] = raw if isinstance(raw, dict) else {}
    return out


async def save_extras(lamp_id: str, extras: Dict[str, Any] | None, drop: Iterable[str] = ()) -> None:
    """Replace user fields; internal "_" keys survive unless overwritten or named in `drop`."""
    if not lamp_id:
        return
    gone = {str(k) for k in drop}
    incoming = _clean_incoming(extras)
    current = await load_extras(lamp_id)
    for key, val in current.items():
        if _keep_internal(str(key)) and key not in incoming:
            incoming[key] = val
    for key in gone:
        incoming.pop(key, None)
    async with session_scope() as s:
        try:
            await s.execute(
                text("UPDATE lamps SET extras = CAST(:j AS jsonb) WHERE lamp_id = :id"),
                {"j": json.dumps(incoming, ensure_ascii=False), "id": lamp_id},
            )
        except Exception:
            logger.exception("save extras failed")


async def remember_broadcast(lamp_id: str, chat: str, mid: int, *, card_mid: int | None = None) -> None:
    if not lamp_id or not mid:
        return
    payload = {"_bc_chat": str(chat), "_bc_mid": str(int(mid))}
    payload["_bc_card_mid"] = str(int(card_mid)) if card_mid else ""
    current = await load_extras(lamp_id)
    current.update(payload)
    async with session_scope() as s:
        try:
            await s.execute(
                text(
                    "UPDATE lamps SET bc_chat = :c, bc_mid = :m, extras = CAST(:j AS jsonb) WHERE lamp_id = :id"
                ),
                {
                    "c": str(chat),
                    "m": str(int(mid)),
                    "j": json.dumps(current, ensure_ascii=False),
                    "id": lamp_id,
                },
            )
        except Exception:
            logger.exception("remember broadcast columns failed")
            await save_extras(lamp_id, current)


async def load_broadcast(lamp_id: str) -> Tuple[str, str]:
    if not lamp_id:
        return "", ""
    async with session_scope() as s:
        try:
            row = (
                await s.execute(
                    text("SELECT bc_chat, bc_mid, extras FROM lamps WHERE lamp_id = :id"),
                    {"id": lamp_id},
                )
            ).first()
        except Exception:
            row = None
    if not row:
        extras = await load_extras(lamp_id)
        return str(extras.get("_bc_chat") or ""), str(extras.get("_bc_mid") or "")
    chat = str(row[0] or "")
    mid = str(row[1] or "")
    extras = row[2] if isinstance(row[2], dict) else {}
    if not chat:
        chat = str(extras.get("_bc_chat") or "")
    if not mid:
        mid = str(extras.get("_bc_mid") or "")
    return chat, mid


async def load_broadcast_card(lamp_id: str) -> int | None:
    """Message id of the follow-up text card (album posts only); None for single-photo posts."""
    if not lamp_id:
        return None
    extras = await load_extras(lamp_id)
    raw = extras.get("_bc_card_mid")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


_orig_get = search_service.get_lamp
_orig_apply = listing_flow.apply_edit
_orig_admin = listing_flow.apply_admin_edit
_orig_list = listing_flow.list_my_lamps


async def get_lamp(lamp_id: str):
    lamp = await _orig_get(lamp_id)
    if lamp:
        extras = await load_extras(lamp_id)
        if extras:
            lamp["extras"] = extras
    return lamp


def _user_extras(data: Dict[str, Any] | None) -> Dict[str, str]:
    """Only user-visible fields; any client-sent "_" key (e.g. _open_today) is dropped."""
    from bot.services.extras_clean import clean_user_extras
    raw = (data or {}).get("extras")
    return clean_user_extras(raw if isinstance(raw, dict) else {})


async def save_user_fields(lamp_id: str, data: Dict[str, Any] | None) -> None:
    """Save user extras + (if the caller sent `hours`) the canonical _hours."""
    from bot.services.open_shift import normalize_hours
    data = data or {}
    extras: Dict[str, Any] = dict(_user_extras(data))
    drop: Tuple[str, ...] = ()
    if "hours" in data and data.get("hours") is not None:
        hours = normalize_hours(data.get("hours"))
        if hours:
            extras["_hours"] = hours
        else:
            drop = ("_hours",)
    await save_extras(lamp_id, extras, drop=drop)


async def apply_edit(lamp_id: str, data: Dict[str, Any], *, owner_id: int, admin: bool = False):
    lamp = await _orig_apply(lamp_id, data, owner_id=owner_id, admin=admin)
    await save_user_fields(lamp_id, data)
    return lamp


async def apply_admin_edit(lamp_id: str, data: Dict[str, Any]):
    lamp = await _orig_admin(lamp_id, data)
    await save_user_fields(lamp_id, data)
    return lamp


async def list_my_lamps(user_id: int):
    items = await _orig_list(user_id)
    for it in items:
        it["extras"] = await load_extras(it.get("lamp_id") or "")
    return items


search_service.get_lamp = get_lamp  # type: ignore[assignment]
listing_flow.apply_edit = apply_edit  # type: ignore[assignment]
listing_flow.apply_admin_edit = apply_admin_edit  # type: ignore[assignment]
listing_flow.list_my_lamps = list_my_lamps  # type: ignore[assignment]
