"""file_id → Mini App 可展示的预览地址。"""
from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import quote


def is_http(url: str) -> bool:
    u = (url or "").strip()
    return u.startswith("http://") or u.startswith("https://") or u.startswith("/")


def preview_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.lower().startswith("file_id:"):
        u = u.split(":", 1)[-1].strip()
    if is_http(u):
        return u
    return f"/api/media/file/{quote(u, safe='')}"


def guess_kind(raw: str, declared: str | None = None) -> str:
    kind = (declared or "image").lower()
    u = (raw or "").strip()
    if kind == "video" or u.startswith(("BAAC", "BQAC")) or u.lower().endswith((".mp4", ".mov", ".webm", ".mkv")):
        return "video"
    return "image" if kind not in ("image", "video") else kind


def pick_cover(lamp: Dict[str, Any] | None) -> str:
    if not lamp:
        return ""
    if lamp.get("cover_url"):
        return preview_url(str(lamp.get("cover_url") or ""))
    for m in lamp.get("media") or []:
        if not isinstance(m, dict):
            u = str(m).strip()
            if u and guess_kind(u) != "video":
                return preview_url(u)
            continue
        kind = guess_kind(str(m.get("file_id") or m.get("url") or ""), m.get("type"))
        thumb = str(m.get("thumb_file_id") or m.get("thumbnail") or "").strip()
        preview = str(m.get("preview_url") or "").strip()
        if kind == "video":
            if thumb and guess_kind(thumb) != "video":
                return preview_url(thumb)
            if preview and guess_kind(preview) != "video":
                return preview_url(preview)
            continue
        if thumb:
            return preview_url(thumb)
        if preview:
            return preview_url(preview)
        raw = str(m.get("file_id") or m.get("url") or "").strip()
        if raw:
            return preview_url(raw)
    for p in lamp.get("photos") or []:
        u = str(p).strip()
        if u and guess_kind(u) != "video":
            return preview_url(u)
    return ""


def enrich_media(media: List[Dict[str, Any]] | None, photos: List[str] | None = None) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for m in media or []:
        if not isinstance(m, dict):
            u = str(m).strip()
            if u:
                out.append({"type": guess_kind(u), "url": u, "preview_url": preview_url(u)})
            continue
        raw = (m.get("file_id") or m.get("url") or "").strip()
        if not raw:
            continue
        kind = guess_kind(raw, m.get("type"))
        thumb = str(m.get("thumb_file_id") or m.get("thumbnail") or "").strip()
        preview = str(m.get("preview_url") or "").strip()
        if thumb:
            preview = preview_url(thumb)
        elif not preview:
            preview = preview_url(raw)
        item = {"type": kind, "url": raw, "preview_url": preview}
        if m.get("file_id"):
            item["file_id"] = str(m["file_id"])
        elif not is_http(raw):
            item["file_id"] = raw
        if thumb:
            item["thumb_file_id"] = thumb
        out.append(item)
    if not out:
        for p in photos or []:
            u = str(p).strip()
            if u:
                out.append({"type": guess_kind(u), "url": u, "preview_url": preview_url(u)})
    return out
