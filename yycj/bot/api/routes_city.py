"""城市分区：后台城市切换器的计数、分城市公告、运营负责城市。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from bot.api.deps import get_admin_user_id, get_super_user_id
from bot.api.routes_core import router
from bot.services import city_scope, home_service
from bot.services.admin_acl import extra_ids, is_super, warm

logger = logging.getLogger(__name__)


class CityAnnounceBody(BaseModel):
    city: str
    text: str = ""
    enabled: bool = True


class CitySettingsBody(BaseModel):
    city: str
    # {key: value}; null = clear (use site-wide). "" clears too unless the key has allow_blank (then "" = hide).
    overrides: Dict[str, Any] = {}


class OperatorCitiesBody(BaseModel):
    operator_cities: Dict[str, List[str]] = {}


@router.get("/admin/cities/summary")
async def api_city_summary(admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    settings = await home_service.get_or_create_settings()
    enabled = list(settings.get("enabled_cities") or [])
    scope = await city_scope.scope_for(admin_id)
    data = await city_scope.summary(scope, enabled)
    return {
        "ok": True,
        "is_super": is_super(admin_id),
        "scope": sorted(scope) if scope is not None else None,
        "enabled_cities": enabled,
        "city_announcements": settings.get("city_announcements") or {},
        "global_announcement": settings.get("announcement_text") or "",
        "city_override_counts": {c: len(v or {}) for c, v in (settings.get("city_overrides") or {}).items()},
        **data,
    }


@router.post("/admin/cities/announcement")
async def api_city_announcement(
    body: CityAnnounceBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    city = (body.city or "").strip()[:32]
    if not city:
        raise HTTPException(status_code=400, detail="缺少城市")
    if not is_super(admin_id):
        # Unscoped operators can't edit the global announcement either; only an operator
        # explicitly assigned to this city may edit its announcement.
        scope = await city_scope.scope_for(admin_id)
        if scope is None or city not in scope:
            raise HTTPException(status_code=403, detail="只有超管或负责这个城市的运营能改城市公告")
    current = dict((await home_service.get_or_create_settings()).get("city_announcements") or {})
    text = (body.text or "")[:2000]
    if text.strip():
        current[city] = {"text": text, "enabled": bool(body.enabled)}
    else:
        current.pop(city, None)
    settings = await home_service.update_settings(ops_config={"city_announcements": current})
    return {"ok": True, "city_announcements": settings.get("city_announcements") or {}}


@router.post("/admin/operators/cities")
async def api_operator_cities(
    body: OperatorCitiesBody,
    _: int = Depends(get_super_user_id),
) -> Dict[str, Any]:
    await warm()
    ops = extra_ids()
    cleaned = city_scope.clean_operator_cities(body.operator_cities)
    unknown = [k for k in cleaned if int(k) not in ops]
    if unknown:
        raise HTTPException(status_code=400, detail="不是运营管理员：" + ", ".join(unknown))
    settings = await home_service.update_settings(ops_config={"operator_cities": cleaned})
    return {"ok": True, "operator_cities": settings.get("operator_cities") or {}}


async def _city_edit_rights(admin_id: int, city: str) -> Dict[str, bool]:
    """Super: everything. Operator explicitly assigned to the city: non-super keys. Others: read-only."""
    if is_super(admin_id):
        return {"can_edit": True, "can_edit_super": True}
    scope = await city_scope.scope_for(admin_id)
    mine = scope is not None and city in scope
    return {"can_edit": mine, "can_edit_super": False}


def _city_settings_payload(settings: Dict[str, Any], city: str, rights: Dict[str, bool]) -> Dict[str, Any]:
    from bot.services.home_feed import CITY_KEYS
    over = (settings.get("city_overrides") or {}).get(city) or {}
    keys = []
    for meta in CITY_KEYS:
        k = meta["key"]
        keys.append({**meta, "global": settings.get(k), "override": over.get(k)})
    return {"ok": True, "city": city, "keys": keys, "overrides": over, **rights}


@router.get("/admin/cities/settings")
async def api_city_settings(city: str, admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    city = (city or "").strip()[:32]
    if not city:
        raise HTTPException(status_code=400, detail="缺少城市")
    await city_scope.ensure_city(admin_id, city)
    settings = await home_service.get_or_create_settings()
    return _city_settings_payload(settings, city, await _city_edit_rights(admin_id, city))


@router.post("/admin/cities/settings")
async def api_city_settings_save(body: CitySettingsBody, admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    from bot.services.home_feed import CITY_KEY_MAP
    city = (body.city or "").strip()[:32]
    if not city:
        raise HTTPException(status_code=400, detail="缺少城市")
    settings = await home_service.get_or_create_settings()
    before = settings
    if city not in (settings.get("enabled_cities") or []):
        raise HTTPException(status_code=400, detail="不是已开通的城市")
    rights = await _city_edit_rights(admin_id, city)
    if not rights["can_edit"]:
        raise HTTPException(status_code=403, detail="只有超管或负责这个城市的运营能改城市设置")
    changes: Dict[str, Any] = {}
    for k, v in (body.overrides or {}).items():
        meta = CITY_KEY_MAP.get(k)
        if meta is None:
            raise HTTPException(status_code=400, detail=f"这一项不能按城市设置：{k}")
        if meta.get("super") and not rights["can_edit_super"]:
            raise HTTPException(status_code=403, detail=f"「{meta['label']}」只有超管能改")
        if k.startswith("community_") and k.endswith("_url") and v not in (None, ""):
            from bot.services.community import safe_url
            if not safe_url(v):
                raise HTTPException(status_code=400, detail="社区入口只支持 https:// 或 t.me/ 链接")
        if k == "admin_contact" and v not in (None, ""):
            from bot.services import bot_info
            norm = bot_info.normalize_contact(str(v).strip())
            if not norm:
                raise HTTPException(status_code=400, detail="管理员联系链接格式不对，示例 https://t.me/用户名 或 @用户名")
            v = norm
        if k == "broadcast_channel" and v not in (None, ""):
            from bot.services.broadcast import valid_chat
            if not valid_chat(str(v)):
                raise HTTPException(status_code=400, detail="推送频道格式不对，示例 @频道名 或 -100…")
        changes[k] = v
    from bot.services import channel_features
    if any(k in changes for k in ("channel_directory", "directory_channel", "broadcast_reviews", "review_channel",
                                  "broadcast_coupons", "coupon_channel", "broadcast_channel")):
        try:
            changes = await channel_features.validate(changes, city=city)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    settings = await home_service.update_city_overrides(city, changes,actor_id=admin_id)
    try:
        from bot.services import label_sync
        await label_sync.schedule_after_save(before, settings, city=city)
    except Exception:
        logger.exception("schedule label sync after city save failed")
    # 按区目录: no post / delete now — the daily 12:00 refresh applies it (posts, moves or retires the message)
    out = _city_settings_payload(settings, city, rights)
    out["directory_applies_at"] = channel_features.applies_at()
    return out


class ChannelFeaturesBody(BaseModel):
    channel_directory: Optional[bool] = None
    directory_channel: Optional[str] = None
    broadcast_reviews: Optional[bool] = None
    review_channel: Optional[str] = None
    broadcast_coupons: Optional[bool] = None
    coupon_channel: Optional[str] = None


@router.get("/admin/channel-features")
async def api_channel_features(_: int = Depends(get_super_user_id)) -> Dict[str, Any]:
    from bot.services import channel_features
    return {"ok": True, **channel_features.overview(await home_service.get_or_create_settings()),
            "directory_applies_at": channel_features.applies_at()}


@router.post("/admin/channel-features")
async def api_channel_features_save(body: ChannelFeaturesBody, _: int = Depends(get_super_user_id)) -> Dict[str, Any]:
    """全站默认：按区目录 / 精选评价 的开关和频道（城市单独设的沿用城市的）。"""
    from bot.services import channel_features
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    for k in ("directory_channel", "review_channel", "coupon_channel"):
        if k in changes:
            changes[k] = str(changes[k] or "").strip()
    try:
        changes = await channel_features.validate(changes, city=None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    settings = await home_service.update_settings(ops_config=changes or None)
    # applied at the next 12:00 refresh; cities inheriting these values are checked and only warned about
    warnings = await channel_features.inherited_warnings(settings)
    return {"ok": True, **channel_features.overview(settings), "warnings": warnings,
            "directory_applies_at": channel_features.applies_at()}


class LabelSyncBody(BaseModel):
    city: str


@router.get("/admin/label-sync")
async def api_label_sync_status(city: str, admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    city = (city or "").strip()[:32]
    if not city:
        raise HTTPException(status_code=400, detail="缺少城市")
    await city_scope.ensure_city(admin_id, city)
    from bot.services import label_sync
    return await label_sync.status(city)


@router.post("/admin/label-sync/run")
async def api_label_sync_run(body: LabelSyncBody, admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    city = (body.city or "").strip()[:32]
    if not city:
        raise HTTPException(status_code=400, detail="缺少城市")
    await city_scope.ensure_city(admin_id, city)
    from bot.services import label_sync
    await label_sync.queue_city(city, delay=0)
    return await label_sync.status(city)


@router.post("/admin/label-sync/retry")
async def api_label_sync_retry(body: LabelSyncBody, admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    city = (body.city or "").strip()[:32]
    if not city:
        raise HTTPException(status_code=400, detail="缺少城市")
    await city_scope.ensure_city(admin_id, city)
    from bot.services import label_sync
    return await label_sync.retry_failed(city)
