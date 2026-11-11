"""分享深链：先加机器人、强制订阅，再进对应资料。"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from bot.config import get_settings
from bot.services import home_service

logger = logging.getLogger(__name__)

_UUID_DASH = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


OPEN_KINDS = {"s": "打开资料", "r": "查看评价", "w": "写评价", "q": "领取优惠"}


def parse_start_payload(arg: str) -> tuple[str, Optional[str]]:
    """``s_|r_|w_|q_<uuid or 32hex>`` → (kind, lamp_id). s=详情, r=评价区, w=写评价, q=领取优惠. Bare id = s."""
    raw = (arg or "").strip()
    kind = "s"
    if len(raw) > 2 and raw[1] == "_" and raw[0].lower() in OPEN_KINDS:
        kind, raw = raw[0].lower(), raw[2:]
    return kind, parse_share_payload(raw)


def parse_share_payload(arg: str) -> Optional[str]:
    raw = (arg or "").strip()
    if len(raw) > 2 and raw[1] == "_" and raw[0].lower() in "srw":
        raw = raw[2:]
    if _UUID_DASH.match(raw):
        return raw.lower()
    compact = raw.replace("-", "")
    if len(compact) == 32 and re.fullmatch(r"[0-9a-fA-F]{32}", compact):
        compact = compact.lower()
        return f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:32]}"
    return None


def card_page_url(lamp_id: str, kind: str = "s") -> str:
    webapp = (get_settings().normalized_webapp_url or "").rstrip("/")
    if not webapp:
        return ""
    if webapp.endswith(".html"):
        webapp = webapp.rsplit("/", 1)[0]
    extra = f"&open={kind}" if kind in ("r", "w", "q") else ""
    return f"{webapp}/go.html?lamp={lamp_id}{extra}"


def _norm_chat(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        cid = str(raw.get("chat_id") or raw.get("id") or raw.get("username") or "").strip()
        title = str(raw.get("title") or raw.get("name") or "").strip()
        url = str(raw.get("url") or "").strip()
    else:
        cid = str(raw or "").strip()
        title, url = "", ""
    if cid.startswith("https://t.me/"):
        url = cid
        cid = cid.split("t.me/", 1)[-1].split("?")[0].strip("/")
        cid = "@" + cid if not cid.startswith("@") and not cid.lstrip("-").isdigit() else cid
    if cid.startswith("@"):
        url = url or f"https://t.me/{cid[1:]}"
        title = title or cid
    elif cid.lstrip("-").isdigit():
        title = title or cid
    return {"chat_id": cid, "title": title or cid, "url": url, "required": True}


async def resolve_chat(c: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(c)
    cid = out.get("chat_id") or ""
    if out.get("url") and out.get("title") and not str(out.get("title")).lstrip("-").isdigit():
        return out
    try:
        from bot.main import bot
        chat = await bot.get_chat(cid)
        uname = getattr(chat, "username", None) or ""
        title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or uname or cid
        out["title"] = title
        if uname:
            out["url"] = f"https://t.me/{uname}"
        elif not out.get("url") and str(cid).lstrip("-").isdigit():
            out["url"] = f"https://t.me/c/{str(cid).replace('-100', '')}"
    except Exception:
        logger.warning("resolve chat failed %s", cid)
        if cid.startswith("@") and not out.get("url"):
            out["url"] = f"https://t.me/{cid[1:]}"
    return out


async def missing_required(user_id: int) -> List[Dict[str, Any]]:
    site = await home_service.get_or_create_settings()
    raw = list(site.get("required_chats") or [])
    chats = [_norm_chat(x) for x in raw if _norm_chat(x).get("chat_id")]
    if not chats:
        return []
    from bot.main import bot

    missing: List[Dict[str, Any]] = []
    for c in chats:
        chat_id = c.get("chat_id")
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            status = getattr(member, "status", "") or ""
            if status in ("left", "kicked"):
                missing.append(await resolve_chat(c))
        except Exception:
            logger.warning("membership check failed %s", chat_id)
            missing.append(await resolve_chat(c))
    return missing


def join_kb(chats: List[Dict[str, Any]], lamp_id: str) -> InlineKeyboardMarkup:
    rows = []
    for c in chats:
        title = c.get("title") or c.get("chat_id") or "频道"
        url = c.get("url") or ""
        if url:
            rows.append([InlineKeyboardButton(text=f"加入 {title}", url=url)])
    rows.append([InlineKeyboardButton(text="我已关注", callback_data=f"share_go:{lamp_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def open_card_kb(lamp_id: str, kind: str = "s") -> Optional[InlineKeyboardMarkup]:
    url = card_page_url(lamp_id, kind)
    if not url:
        return None
    label = OPEN_KINDS.get(kind, "打开资料")
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, web_app=WebAppInfo(url=url))]]
    )
