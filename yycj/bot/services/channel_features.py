"""频道附加推送：按区目录 / 精选评价 的开关 + 频道（全站默认 + 每城覆盖），保存前校验机器人能发。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from bot.services import home_service
from bot.services.home_feed import FEATURE_CHANNEL, feature_chat, ops_for_city

logger = logging.getLogger(__name__)

LABELS = {"channel_directory": "频道按区目录", "broadcast_reviews": "精选评价推送到频道", "broadcast_coupons": "优惠推送到频道"}
CHANNEL_KEYS = ("broadcast_channel", "directory_channel", "review_channel", "coupon_channel")
GLOBAL_KEYS = ("channel_directory", "directory_channel", "broadcast_reviews", "review_channel",
               "broadcast_coupons", "coupon_channel")


def _apply(site: Dict[str, Any], changes: Dict[str, Any], city: Optional[str]) -> Dict[str, Any]:
    """Effective settings after ``changes`` (site-wide when city is None, else that city's overrides)."""
    base = dict(site)
    if city is None:
        for k, v in changes.items():
            base[k] = v
        return ops_for_city(base, None)
    over = dict(((site.get("city_overrides") or {}).get(city)) or {})
    for k, v in changes.items():
        if v is None or (isinstance(v, str) and not v.strip()):
            over.pop(k, None)
        else:
            over[k] = v
    base["city_overrides"] = dict(site.get("city_overrides") or {}, **{city: over})
    return ops_for_city(base, city)


async def validate(changes: Dict[str, Any], *, city: Optional[str] = None) -> Dict[str, Any]:
    """Raise ValueError(中文原因) when a channel can't be used. Returns cleaned changes."""
    from bot.services.broadcast import check_post_rights, valid_chat
    clean = dict(changes)
    for k in CHANNEL_KEYS:
        if k in clean and clean[k] not in (None, ""):
            c = valid_chat(str(clean[k]))
            if not c:
                raise ValueError(f"频道格式不对：{str(clean[k])[:40]}（示例 @频道名 或 -100…）")
            clean[k] = c
    site = await home_service.get_or_create_settings()
    before = ops_for_city(site, city)
    after = _apply(site, clean, city)
    checked = set()
    for feat in FEATURE_CHANNEL:
        if not after.get(feat):
            continue
        # 优惠推送 is on by default: only check rights when it (or its channel) is touched in this save,
        # so moving a city's 推送频道 doesn't suddenly need a Telegram round-trip
        if feat == "broadcast_coupons" and "broadcast_coupons" not in clean and not clean.get("coupon_channel"):
            continue
        chat = feature_chat(after, feat)
        where = f"（{city}）" if city else ""
        if not chat:
            # only an explicit 「开」 in this save is an error (优惠推送 is on by default: no channel = no post)
            if clean.get(feat):
                raise ValueError(f"「{LABELS[feat]}」{where}开着，但没有频道：请填它的频道，或先设推送频道")
            continue
        # only ask Telegram when something relevant changed (switch turned on / channel moved)
        if before.get(feat) and feature_chat(before, feat) == chat:
            continue
        if chat in checked:
            continue
        err = await check_post_rights(chat)
        if err:
            raise ValueError(f"「{LABELS[feat]}」{where}：{err}")
        checked.add(chat)
    for k in ("directory_channel", "review_channel", "coupon_channel"):
        v = clean.get(k)
        if v and v not in checked:
            err = await check_post_rights(v)
            if err:
                raise ValueError(err)
            checked.add(v)
    return clean


def overview(site: Dict[str, Any]) -> Dict[str, Any]:
    """Site-wide defaults + each enabled city's effective / own values (for the 全站默认 table)."""
    g = {k: site.get(k) for k in GLOBAL_KEYS + ("broadcast_channel",)}
    rows: List[Dict[str, Any]] = []
    for city in site.get("enabled_cities") or []:
        eff = ops_for_city(site, city)
        own = (site.get("city_overrides") or {}).get(city) or {}
        row = {"city": city}
        for feat, ck in FEATURE_CHANNEL.items():
            row[feat] = {
                "on": bool(eff.get(feat)),
                "own_switch": feat in own,
                "chat": feature_chat(eff, feat),
                "own_chat": bool(own.get(ck)),
                "via": "own" if own.get(ck) else ("city_push" if own.get("broadcast_channel") else ("site" if site.get(ck) else "site_push")),
            }
        rows.append(row)
    return {"global": g, "cities": rows}


def applies_at(now=None) -> str:
    """按区目录 changes are applied by the daily 12:00 (Asia/Shanghai) refresh, not on save."""
    from datetime import datetime, timedelta, timezone
    bj = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=8)))
    return "今天 12:00" if bj.hour < 12 else "明天 12:00"


async def inherited_warnings(site: Dict[str, Any]) -> List[Dict[str, str]]:
    """After a 全站默认 save: check every enabled city's effective channel. Warn, never block."""
    from bot.services.broadcast import check_post_rights
    seen: Dict[str, str] = {}
    out: List[Dict[str, str]] = []
    for city in site.get("enabled_cities") or []:
        eff = ops_for_city(site, city)
        for feat in FEATURE_CHANNEL:
            if not eff.get(feat):
                continue
            chat = feature_chat(eff, feat)
            if not chat:
                if feat != "broadcast_coupons":   # 优惠推送 is on by default; no channel simply means no post
                    out.append({"city": city, "feature": LABELS[feat], "chat": "", "error": "没有频道"})
                continue
            if chat not in seen:
                try:
                    seen[chat] = await check_post_rights(chat)
                except Exception as exc:  # network etc. — still only a warning
                    seen[chat] = f"检查 {chat} 失败：{exc}"[:120]
            if seen[chat]:
                out.append({"city": city, "feature": LABELS[feat], "chat": chat, "error": seen[chat]})
    return out


def resync_soon(cities: Optional[List[str]] = None) -> None:
    """Refresh (or retire) the directory right after a settings change, in the background."""
    async def run():
        from bot.services import channel_directory
        try:
            if cities is None:
                await channel_directory.job_refresh_all()
            else:
                for c in cities:
                    await channel_directory.refresh_city(c)
        except Exception:
            logger.exception("directory resync failed")
    try:
        asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        pass
