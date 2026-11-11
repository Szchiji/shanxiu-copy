"""Public city navigation and privacy-safe district result aggregation."""

from __future__ import annotations

import re
from urllib.parse import urlsplit


def safe_url(value) -> str:
    raw = str(value or "").strip()[:512]
    if raw.startswith("t.me/"):
        raw = "https://" + raw
    try:
        u = urlsplit(raw)
        if (
            u.scheme != "https"
            or not u.hostname
            or u.username
            or u.password
            or any(ord(c) < 33 for c in raw)
        ):
            return ""
        return raw
    except ValueError:
        return ""


def clean_welcome_buttons(raw) -> list:
    from bot.services.message_buttons import clean
    return clean(raw,'welcome')


def channel_url(chat, message_id=0):
    raw = str(chat or "").strip()
    if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", raw):
        base = "https://t.me/" + raw[1:]
    elif re.fullmatch(r"-100\d+", raw):
        base = "https://t.me/c/" + raw[4:]
    else:
        base = safe_url(raw)
    return base + ("/" + str(int(message_id)) if message_id and base else "")


def public_links(site):
    """Unconfigured links are absent; never invent a target or borrow another city's settings."""
    from bot.services.home_feed import feature_chat

    entries = []

    def add(key, label, url):
        if url:
            entries.append({"key": key, "label": label, "url": url})

    add("channel", "官方频道", channel_url(site.get("broadcast_channel")))
    if site.get("broadcast_reviews"):
        add("reviews", "精选评价", channel_url(feature_chat(site, "broadcast_reviews")))
    if site.get("broadcast_coupons") and site.get("coupons_enabled", True):
        add(
            "coupons",
            "本城优惠频道",
            channel_url(feature_chat(site, "broadcast_coupons")),
        )
    if site.get("community_enabled", True):
        for key, label in (
            ("discussion", "官方讨论群"),
            ("backup", "防失联导航"),
            ("location", site.get("community_location_label") or "位置"),
        ):
            add(key, label, safe_url(site.get("community_" + key + "_url")))
    return entries


def district_groups(items, city):
    """Only public administrative centres leave the API, NEVER listing/user coordinates."""
    from bot.services.feed_filters import center_of
    from bot.services.search_service import district_key

    groups = {}
    for card in items:
        area = district_key(card.get("district")) or "其他"
        group = groups.setdefault(area, {"district": area, "count": 0, "lamp_ids": []})
        group["count"] += 1
        group["lamp_ids"].append(card["lamp_id"])
    for area, group in groups.items():
        pt = center_of(city, area)
        if pt:
            group["lat"], group["lng"] = pt
    return list(groups.values())
