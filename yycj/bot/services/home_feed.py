"""首页卡片流置顶与运营配置。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from bot.db import session_scope
from bot.models import HomepagePin, Lamp, LampStatus

DEFAULT_OPS = {
    "home_feed_page_size": 3,
    "chat_cta_label": "想聊聊",
    "show_chat_cta": True,
    "popup_announcement_enabled": False,
    "popup_announcement_title": "本城公告",
    "popup_announcement_text": "",
    "bot_welcome_text": "",
    "bot_welcome_enabled": True,
    "approval_notify_enabled": True,
    "approval_buttons": {"mode":"default","items":[]},
    "bot_welcome_buttons": [],
    "approve_attach_detail": True,
    "approve_attach_channel_btn": True,
    "community_discussion_url": "",
    "community_backup_url": "",
    "community_location_url": "",
    "community_location_label": "位置",
    "community_enabled": True,
    "media_max_count": 6,
    "review_require_audit": True,
    "listing_days": 30,
    "carousel_interval_sec": 4,
    "show_bot_link": True,
    "show_admin_link": True,
    "bot_btn_label": "机器人",
    "admin_btn_label": "管理员",
    "admin_contact": "",
    "required_chats": [],
    "extra_admin_ids": [],
    # {"<operator user_id>": ["深圳", ...]}: operators listed here only see / act on those cities.
    "operator_cities": {},
    # {"深圳": {"text": "...", "enabled": true}}: overrides the global announcement on that city's home.
    "city_announcements": {},
    # {"深圳": {"broadcast_channel": "@sz_ch", ...}}: per-city overrides; missing/empty = site-wide value.
    "city_overrides": {},
    "approve_promo_text": "你的资料已上架。\n欢迎把月影车姬介绍给朋友：在 Telegram 搜索同名机器人，点左下角「首页」开始。",
    "broadcast_channel": "",
    "media_channel_id": "",
    "broadcast_template": "🌙 月影车姬 · 新上架\n{称呼}\n📍 {地点}\n💰 {价位}\n{标签}\n{简介}\n{链接}",
    "broadcast_reviews": False,  # 精选评价推送到频道（默认关）
    "review_channel": "",  # 精选评价发到哪个频道；空 = 用推送频道
    "channel_directory": False,  # 频道按区目录（每天 12:00 刷新同一条，默认关）
    "coupons_enabled": True,  # 老师在「我的」发限时优惠
    "broadcast_coupons": True,  # 新优惠推送到频道（默认开；结束/过期/停用时把帖子改成「已结束」）
    "coupon_channel": "",  # 优惠推送到哪个频道；空 = 用推送频道
    "coupon_display": "badge,filter,list",  # 首页入口: badge = 卡片「惠」字 / filter = 筛选里「有优惠」 / list = 「我的 · 本城优惠」; 可多选逗号分隔
    "directory_channel": "",  # 按区目录发到哪个频道；空 = 用推送频道
    # 频道帖子末尾三个深链文案（空 = 不显示这一项）；城市可覆盖
    "link_label_detail": "查看详情",
    "link_label_reviews": "查看评价",
    "link_label_write": "写评价",
    "link_label_location": "",
    # 老师「我的上架」是否显示三项统计（全站；默认开）
    "teacher_show_views": True,
    "teacher_show_favs": True,
    "teacher_show_contacts": True,
    "listing_fields": [
        {"key": "称呼", "label": "称呼", "form": True},
        {"key": "城市", "label": "城市", "form": True},
        {"key": "简介", "label": "简介", "form": True},
        {"key": "价位", "label": "价位", "form": True},
        {"key": "区域", "label": "区域", "form": True},
        {"key": "大致位置", "label": "大致位置", "form": True},
        {"key": "标签", "label": "标签", "form": True},
        {"key": "地点", "label": "地点", "form": False},
        {"key": "链接", "label": "链接", "form": False},
        {"key": "聊天按钮", "label": "聊天按钮", "form": False},
    ],
}


# Settings a city may override (key, label, type, extra). Everything else stays site-wide.
# "super": only the super admin may set it (a scoped operator sees it read-only).
CITY_KEYS = [
    {"key":"approval_notify_enabled","label":"发送老师上架成功通知","type":"bool","hint":"后续审核、续期和媒体审核成功通知；关闭不影响续期频道重发。"},
    {"key":"approval_buttons","label":"老师成功通知按钮","type":"buttons","hint":"空城市设置沿用全站；自定义空列表只隐藏按钮，不关闭通知。"},
    {"key": "popup_announcement_enabled", "label": "启用进入小程序弹窗公告", "type": "bool", "hint": "独立于普通公告；关闭本城开关可明确停用本城弹窗"},
    {"key": "popup_announcement_title", "label": "弹窗公告标题", "type": "text", "max": 40},
    {"key": "popup_announcement_text", "label": "弹窗公告内容", "type": "textarea", "max": 3000, "hint": "纯文字，最多3000字；空城市内容沿用全站。未启用或无内容不弹出"},
    *[{"key": "community_" + k + "_url", "label": label, "type": "text", "max": 512, "hint": "https:// 或 t.me/ 链接；空 = 沿用全站。关闭本城社区入口可隐藏。"} for k, label in (("discussion", "官方讨论群"), ("backup", "防失联导航"), ("location", "本城位置链接"))],
    {"key": "community_location_label", "label": "位置入口文案", "type": "text", "max": 16},
    {"key": "community_enabled", "label": "显示本城社区外链", "type": "bool", "hint": "关闭后隐藏本城群、防失联、位置入口，不影响官方频道"},
    {"key": "approve_attach_detail", "label": "附带资料详情链接和小程序按钮", "type": "bool"},
    {"key": "approve_attach_channel_btn", "label": "附带查看频道帖按钮（有相册时）", "type": "bool"},
    {"key": "broadcast_channel", "label": "推送频道", "type": "text", "max": 128, "super": True,
     "hint": "新上架资料推送到这个频道（@频道名 或 -100…）；空 = 用全站频道"},
    {"key": "broadcast_template", "label": "推送文案模板", "type": "textarea", "max": 2000},
    {"key": "link_label_detail", "label": "频道链接 · 详情", "type": "text", "max": 16, "allow_blank": True,
     "hint": "频道帖末尾「详情」链接文字；默认「查看详情」。留空 = 不显示这一项"},
    {"key": "link_label_reviews", "label": "频道链接 · 评价", "type": "text", "max": 16, "allow_blank": True,
     "hint": "默认「查看评价」。留空 = 不显示"},
    {"key": "link_label_location", "label": "频道链接 · 位置", "type": "text", "max": 16, "allow_blank": True, "hint": "本城位置链接有有效目标时才显示；留空隐藏"},
    {"key": "link_label_write", "label": "频道链接 · 写评价", "type": "text", "max": 16, "allow_blank": True,
     "hint": "默认「写评价」。留空 = 不显示"},
    {"key": "channel_directory", "label": "频道按区目录", "type": "bool",
     "hint": "开 = 每天 12:00 更新一条本城的按区目录（只列本城的区和在架资料），花名直达详情；开关和换频道都在下一次 12:00 生效，关掉会删掉旧目录（默认关）"},
    {"key": "directory_channel", "label": "按区目录发到的频道", "type": "text", "max": 128, "super": True,
     "hint": "@频道名 或 -100…；空 = 用本城的推送频道。机器人必须是该频道管理员"},
    {"key": "broadcast_reviews", "label": "精选评价推送到频道", "type": "bool",
     "hint": "开 = 审核通过的 4–5 星评价（只发文字，不发凭证）发到下面的频道（默认关）"},
    {"key": "review_channel", "label": "精选评价发到的频道", "type": "text", "max": 128, "super": True,
     "hint": "@频道名 或 -100…；空 = 用本城的推送频道。机器人必须是该频道管理员"},
    {"key": "broadcast_coupons", "label": "优惠推送到频道", "type": "bool",
     "hint": "开 = 老师发优惠时在频道发一条文字帖（查看详情 · 领取优惠）；结束 / 过期 / 停用时改成「已结束」（默认开）"},
    {"key": "coupon_channel", "label": "优惠发到的频道", "type": "text", "max": 128, "super": True,
     "hint": "@频道名 或 -100…；空 = 用本城的推送频道。机器人必须是该频道管理员"},
    {"key": "approve_promo_text", "label": "上架通过后发给老师的话", "type": "textarea", "max": 2000},
    {"key": "chat_cta_label", "label": "聊天按钮文字", "type": "text", "max": 32},
    {"key": "admin_contact", "label": "管理员联系链接", "type": "text", "max": 128,
     "hint": "首页「管理员」按钮打开的链接，https://t.me/用户名 或 @用户名"},
    {"key": "admin_btn_label", "label": "管理员按钮文字", "type": "text", "max": 16},
    {"key": "show_admin_link", "label": "首页显示管理员按钮", "type": "bool"},
    {"key": "review_require_audit", "label": "评价需审核", "type": "bool",
     "hint": "关 = 这个城市的新评价直接发布，不进待审"},
    {"key": "home_feed_page_size", "label": "首页每次加载卡片数", "type": "int", "min": 1, "max": 50},
    {"key": "carousel_interval_sec", "label": "轮播间隔（秒）", "type": "int", "min": 2, "max": 15},
    {"key": "listing_days", "label": "上架天数", "type": "int", "min": 1, "max": 365, "super": True},
]
CITY_KEY_MAP = {x["key"]: x for x in CITY_KEYS}


def audit_on(value: Any) -> bool:
    """评价需审核: only an explicit off turns it off; missing / null / junk keeps today's behaviour (ON)."""
    if value is False or value == 0:
        return False
    if isinstance(value, str) and value.strip().lower() in ("false", "0", "off", "no"):
        return False
    return True


def clean_override_value(key: str, value: Any) -> Any:
    """Return the cleaned override value, or None when it means 'use the site-wide value'."""
    meta = CITY_KEY_MAP.get(key)
    if meta is None or value is None:
        return None
    t = meta["type"]
    if t == "buttons":
        if not isinstance(value,dict) or not value:return None
        from bot.services.message_buttons import approval_config
        return approval_config(value)
    if t == "bool":
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("", "inherit"):
                return None
            return v in ("1", "true", "on", "yes")
        return bool(value)
    if t == "int":
        if isinstance(value, str) and not value.strip():
            return None
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        return max(meta["min"], min(meta["max"], n))
    if key.endswith("_url") and key.startswith("community_"):
        from bot.services.community import safe_url
        return safe_url(value) or None
    text = str(value)
    if t in ("text", "textarea"):
        text = text.strip() if t == "text" else text
    text = text[: meta.get("max", 2000)]
    if meta.get("allow_blank"):
        return text  # "" = hide this link for the city (not inherit)
    return text if text.strip() else None


def clean_city_overrides(raw: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for city, vals in list(raw.items())[:50]:
        c = str(city or "").strip()[:32]
        if not c or not isinstance(vals, dict):
            continue
        clean = {}
        for k, v in vals.items():
            cv = clean_override_value(str(k), v)
            if cv is not None:
                clean[str(k)] = cv
        if clean:
            out[c] = clean
    return out


FEATURE_CHANNEL = {"channel_directory": "directory_channel", "broadcast_reviews": "review_channel",
                   "broadcast_coupons": "coupon_channel"}


def feature_chat(site: Dict[str, Any], feature: str) -> str:
    """Target chat of 按区目录 / 精选评价 for already city-merged settings: own channel, else 推送频道."""
    from bot.services.broadcast import normalize_chat
    own = str(site.get(FEATURE_CHANNEL.get(feature, "")) or "").strip()
    return normalize_chat(own or str(site.get("broadcast_channel") or ""))


def ops_for_city(settings: Dict[str, Any], city: Optional[str]) -> Dict[str, Any]:
    """Site settings with that city's overrides applied. No city / unknown city = site-wide."""
    over = (settings.get("city_overrides") or {}).get(str(city or "").strip()) if city else None
    if not over:
        return settings
    out = dict(settings)
    for k, v in over.items():
        if k in CITY_KEY_MAP and v is not None:
            out[k] = v
    return out


def _sanitize_listing_fields(fields, fallback=None):
    src = fields if isinstance(fields, list) else list(fallback or [])
    out = []
    seen = set()
    for item in src:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("label") or "").strip()[:16]
        if not key or key in seen:
            continue
        seen.add(key)
        form = False if key in ("地点", "链接", "聊天按钮") else bool(item.get("form", True))
        out.append({"key": key, "label": str(item.get("label") or key)[:16], "form": form})
    return out or list(fallback or [])


def merge_ops(raw) -> Dict[str, Any]:
    out = dict(DEFAULT_OPS)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in DEFAULT_OPS:
                out[k] = v
    try:
        out["home_feed_page_size"] = max(1, min(50, int(out.get("home_feed_page_size") or 3)))
    except (TypeError, ValueError):
        out["home_feed_page_size"] = 3
    try:
        out["media_max_count"] = max(1, min(9, int(out.get("media_max_count") or 9)))
    except (TypeError, ValueError):
        out["media_max_count"] = 9
    out["chat_cta_label"] = str(out.get("chat_cta_label") or "想聊聊")[:32]
    out["show_chat_cta"] = bool(out.get("show_chat_cta", True))
    from bot.services.community import clean_welcome_buttons, safe_url
    out["bot_welcome_buttons"] = clean_welcome_buttons(out.get("bot_welcome_buttons"))
    from bot.services.message_buttons import approval_config
    out["approval_buttons"] = approval_config(out.get("approval_buttons"))
    for key in ("bot_welcome_enabled","approval_notify_enabled"):
        out[key]=out.get(key) is not False
    for key in ("approve_attach_detail", "approve_attach_channel_btn", "community_enabled"):
        out[key] = audit_on(out.get(key))
    for key in ("community_discussion_url", "community_backup_url", "community_location_url"):
        out[key] = safe_url(out.get(key))
    out["community_location_label"] = str(out.get("community_location_label") or "位置")[:16]
    out["popup_announcement_enabled"] = out.get("popup_announcement_enabled") is True
    out["popup_announcement_title"] = str(out.get("popup_announcement_title") or "本城公告")[:40]
    out["popup_announcement_text"] = str(out.get("popup_announcement_text") or "")[:3000]
    out["bot_welcome_text"] = str(out.get("bot_welcome_text") or "")[:2000]
    out["approve_promo_text"] = str(out.get("approve_promo_text") or DEFAULT_OPS["approve_promo_text"])[:2000]
    out["broadcast_channel"] = str(out.get("broadcast_channel") or "").strip()[:128]
    out["media_channel_id"] = str(out.get("media_channel_id") or "").strip()[:128]
    out["broadcast_template"] = str(out.get("broadcast_template") or DEFAULT_OPS["broadcast_template"])[:2000]
    out["broadcast_reviews"] = bool(out.get("broadcast_reviews"))
    out["channel_directory"] = bool(out.get("channel_directory"))
    out["coupons_enabled"] = bool(out.get("coupons_enabled", True))
    out["broadcast_coupons"] = bool(out.get("broadcast_coupons", True))
    out["coupon_channel"] = str(out.get("coupon_channel") or "").strip()[:128]
    disp = [x for x in str(out.get("coupon_display") or "").replace(" ", "").split(",") if x in ("badge", "filter", "list")]
    out["coupon_display"] = ",".join(dict.fromkeys(disp)) or "badge,filter,list"
    out["review_channel"] = str(out.get("review_channel") or "").strip()[:128]
    out["directory_channel"] = str(out.get("directory_channel") or "").strip()[:128]
    for _lk, _ld in (("link_label_detail", "查看详情"), ("link_label_reviews", "查看评价"), ("link_label_write", "写评价"), ("link_label_location", "")):
        if isinstance(raw, dict) and _lk in raw:
            out[_lk] = str(raw.get(_lk) if raw.get(_lk) is not None else "")[:16]
        else:
            out[_lk] = str(out.get(_lk) if out.get(_lk) is not None else _ld)[:16]
    out["teacher_show_views"] = bool(out.get("teacher_show_views", True))
    out["teacher_show_favs"] = bool(out.get("teacher_show_favs", True))
    out["teacher_show_contacts"] = bool(out.get("teacher_show_contacts", True))
    raw_fields = raw.get("listing_fields") if isinstance(raw, dict) and "listing_fields" in raw else out.get("listing_fields")
    out["listing_fields"] = _sanitize_listing_fields(raw_fields, fallback=DEFAULT_OPS["listing_fields"])
    out["review_require_audit"] = audit_on(out.get("review_require_audit"))
    try:
        out["listing_days"] = max(1, min(365, int(out.get("listing_days") or 30)))
    except (TypeError, ValueError):
        out["listing_days"] = 30
    try:
        out["carousel_interval_sec"] = max(2, min(15, int(out.get("carousel_interval_sec") or 4)))
    except (TypeError, ValueError):
        out["carousel_interval_sec"] = 4
    out["show_bot_link"] = bool(out.get("show_bot_link", True))
    out["show_admin_link"] = bool(out.get("show_admin_link", True))
    out["bot_btn_label"] = str(out.get("bot_btn_label") or "机器人")[:16]
    out["admin_btn_label"] = str(out.get("admin_btn_label") or "管理员")[:16]
    out["admin_contact"] = str(out.get("admin_contact") or "").strip()[:128]
    ids = []
    for x in out.get("extra_admin_ids") or []:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n and n not in ids:
            ids.append(n)
    out["extra_admin_ids"] = ids[:200]
    chats = out.get("required_chats") or []
    out["required_chats"] = chats if isinstance(chats, list) else []
    from bot.services.city_scope import clean_operator_cities
    out["operator_cities"] = clean_operator_cities(out.get("operator_cities"))
    anns = {}
    raw_anns = out.get("city_announcements")
    if isinstance(raw_anns, dict):
        for city, v in list(raw_anns.items())[:50]:
            c = str(city or "").strip()[:32]
            if not c or not isinstance(v, dict):
                continue
            text = str(v.get("text") or "")[:2000]
            if text.strip() or v.get("enabled"):
                anns[c] = {"text": text, "enabled": bool(v.get("enabled")) and bool(text.strip())}
    out["city_announcements"] = anns
    out["city_overrides"] = clean_city_overrides(out.get("city_overrides"))
    return out


async def set_feed_pin(lamp_id: str, *, pinned: bool = True, pin_order: int = 0) -> Dict[str, Any]:
    async with session_scope() as s:
        res = await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))
        lamp = res.scalar_one_or_none()
        if not lamp:
            raise ValueError("资料不存在")
        if lamp.status != LampStatus.ACTIVE.value:
            raise ValueError("仅已上架资料可置顶")
        lamp.feed_pinned = bool(pinned)
        lamp.feed_pin_order = int(pin_order) if pinned else 0
        lamp.updated_at = datetime.utcnow()
        await s.flush()
        return {"lamp_id": lamp.lamp_id, "feed_pinned": bool(lamp.feed_pinned), "feed_pin_order": int(lamp.feed_pin_order or 0), "title": lamp.title, "city": lamp.city}


async def list_feed_pins() -> List[Dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(
            select(Lamp)
            .where(Lamp.status == LampStatus.ACTIVE.value, Lamp.feed_pinned.is_(True))
            .order_by(Lamp.feed_pin_order.asc(), Lamp.updated_at.desc())
        )
        lamps = list(res.scalars().all())
    return [{"lamp_id": x.lamp_id, "title": x.title, "city": x.city, "feed_pinned": True, "feed_pin_order": int(x.feed_pin_order or 0)} for x in lamps]


async def list_approved_lamps_brief(limit: int = 100) -> List[Dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(
            select(Lamp).where(Lamp.status == LampStatus.ACTIVE.value).order_by(Lamp.updated_at.desc()).limit(max(1, min(limit, 300)))
        )
        lamps = list(res.scalars().all())
    return [{"lamp_id": x.lamp_id, "title": x.title, "city": x.city, "feed_pinned": bool(getattr(x, "feed_pinned", False))} for x in lamps]
