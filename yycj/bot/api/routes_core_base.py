"""月影车姬 API 核心路由（auth/home/lamps/sessions）。"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from bot.api.deps import get_current_user_id
from bot.api.telegram_webapp import WebAppAuthError, issue_access_token, validate_init_data
from bot.config import get_settings
from bot.models import UserRole
from bot.services import home_service, user_admin, users
from bot.services.admin_ops import AdminActionError
from bot.services.media_urls import pick_cover

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])

_FILE_ID_RE = re.compile(r"^(?:tg:file_id:)?[A-Za-z0-9_\-]{10,256}$")
_ROLE_LABEL = {
    UserRole.TEACHER.value: "老师",
    UserRole.GUEST.value: "客人",
    UserRole.MERCHANT.value: "商家",
}


class AuthBody(BaseModel):
    init_data: str = Field(..., alias="initData", description="Telegram.WebApp.initData")
    model_config = {"populate_by_name": True}

class SessionRequestBody(BaseModel):
    lamp_id: str
    guest_alias: Optional[str] = None

class MediaItem(BaseModel):
    type: str = "image"
    url: Optional[str] = None
    file_id: Optional[str] = None

class PostCreateBody(BaseModel):
    city: str
    title: str
    price: Optional[int] = None
    price_text: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    description: str = ""
    photos: List[str] = Field(default_factory=list)
    media: List[MediaItem] = Field(default_factory=list)
    district: Optional[str] = None
    approx_lat: Optional[float] = None
    approx_lng: Optional[float] = None
    approx_label: Optional[str] = None

class ReportCreateBody(BaseModel):
    lamp_id: str
    reason: str
    description: str = ""

class CreditAdjustBody(BaseModel):
    user_id: int
    delta: int
    note: str = ""

class RoleBody(BaseModel):
    role: str

class ReviewSections(BaseModel):
    diff: str = ""   # 人照课表差异
    pros: str = ""   # 优点
    cons: str = ""   # 不足
    extra: str = ""  # 补充（选填）


class ReviewCreateBody(BaseModel):
    lamp_id: str
    stars: int = 5
    text: str = ""
    photos: List[str] = Field(default_factory=list)   # 公开晒图（≤3）
    proofs: List[str] = Field(default_factory=list)   # 约课凭证（1–5，仅管理员可见）
    sections: Optional[ReviewSections] = None
    session_id: Optional[str] = None

class AnnounceBody(BaseModel):
    text: str = ""
    enabled: bool = True

class CitiesBody(BaseModel):
    cities: List[str]

class PinBody(BaseModel):
    lamp_id: str
    sort_order: int = 0
    expires_hours: Optional[int] = 72

class PinReorderBody(BaseModel):
    ordered_ids: List[int]

class ApprovePinBody(BaseModel):
    expires_hours: Optional[int] = 72
    sort_order: int = 0

class FeedPinBody(BaseModel):
    lamp_id: str
    pinned: bool = True

class OpsSettingsBody(BaseModel):
    announcement_text: Optional[str] = None
    announcement_enabled: Optional[bool] = None
    cities: Optional[List[str]] = None
    home_feed_page_size: Optional[int] = None
    chat_cta_label: Optional[str] = None
    bot_welcome_text: Optional[str] = None
    media_max_count: Optional[int] = None
    review_require_audit: Optional[bool] = None
    listing_days: Optional[int] = None
    carousel_interval_sec: Optional[int] = None
    show_bot_link: Optional[bool] = None
    show_admin_link: Optional[bool] = None
    bot_btn_label: Optional[str] = None
    admin_btn_label: Optional[str] = None
    admin_contact: Optional[str] = None
    required_chats: Optional[List[Dict[str, Any]]] = None

class BanBody(BaseModel):
    user_id: int
    banned: bool = True
    reason: str = ""

class AliasBody(BaseModel):
    alias: str = ""

class LampOpBody(BaseModel):
    operation_id: Optional[str] = None
    reason: str = "admin"
    days: Optional[int] = None


def _ser_dt(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v

def _ser_user(u: Dict[str, Any]) -> Dict[str, Any]:
    out = users.public_user(dict(u))
    for k in ("created_at", "updated_at"):
        if k in out:
            out[k] = _ser_dt(out[k])
    role = out.get("role")
    out["role_label"] = _ROLE_LABEL.get(role or "", None)
    return out

def _ser_lamp(lamp: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(lamp)
    for k in ("created_at", "updated_at", "expires_at"):
        if k in out:
            out[k] = _ser_dt(out[k])
    out.pop("user_id", None)
    out.pop("_distance_km", None)
    out.pop("approx_lat", None)
    out.pop("approx_lng", None)
    out.pop("authenticity_score", None)
    out.pop("credit_boost", None)
    try:
        out["cover_url"] = pick_cover(out)
    except Exception:
        out["cover_url"] = out.get("cover_url") or ""
    return out

def _normalize_media(media: List[MediaItem] | None, photos: List[str] | None) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for m in media or []:
        t = (m.type or "image").lower().strip()
        if t not in ("image", "video"):
            raise HTTPException(status_code=400, detail="media.type 仅支持 image/video")
        url = (m.file_id or m.url or "").strip()
        if not url:
            continue
        items.append({"type": t, "url": _normalize_one_url(url)})
    for p in photos or []:
        s = (p or "").strip()
        if s:
            items.append({"type": "image", "url": _normalize_one_url(s)})
    if len(items) > 9:
        raise HTTPException(status_code=400, detail="媒体合计最多 9 个（图+视频）")
    return items

def _normalize_one_url(s: str) -> str:
    if s.lower().startswith("data:"):
        raise HTTPException(status_code=400, detail="不支持 base64 内嵌，请用公网 URL 或 file_id")
    if s.startswith("http://") or s.startswith("https://"):
        parsed = urlparse(s)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(status_code=400, detail=f"无效 URL：{s[:64]}")
        if len(s) > 1024:
            raise HTTPException(status_code=400, detail="URL 过长")
        return s
    if _FILE_ID_RE.match(s):
        return s.split(":", 2)[-1] if s.lower().startswith("tg:file_id:") else s
    raise HTTPException(status_code=400, detail="仅支持 https URL 或 Telegram file_id")

def _admin_http_error(exc: AdminActionError) -> HTTPException:
    code = 404 if exc.code == "not_found" else 409
    return HTTPException(status_code=code, detail=str(exc))


@router.post("/auth")
async def api_auth(body: AuthBody) -> Dict[str, Any]:
    settings = get_settings()
    try:
        parsed = validate_init_data(body.init_data, settings.bot_token)
    except WebAppAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    tg_user = parsed["user"]
    user_id = int(tg_user["id"])
    username = tg_user.get("username")
    full_name = " ".join(x for x in [tg_user.get("first_name"), tg_user.get("last_name")] if x).strip() or None
    user = await users.ensure_user(user_id, username=username, full_name=full_name)
    if user.get("is_banned") and not settings.is_admin(user_id):
        raise HTTPException(status_code=403, detail="当前账号无法使用")
    token = issue_access_token(user_id, settings.bot_token)
    return {"ok": True, "token": token, "user": _ser_user(user), "is_admin": settings.is_admin(user_id), "needs_role": not bool(user.get("role"))}


@router.get("/me")
async def api_me(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    user = await users.ensure_user(user_id)
    my_reviews = await home_service.list_my_reviews(user_id, limit=20)
    for r in my_reviews:
        r["created_at"] = _ser_dt(r.get("created_at"))
        r["reviewed_at"] = _ser_dt(r.get("reviewed_at"))
    return {
        "ok": True,
        "user": _ser_user(user),
        "is_admin": get_settings().is_admin(user_id),
        "needs_role": not bool(user.get("role")),
        "reviews": my_reviews,
        "tips": {"media_limit": "上架媒体图+视频合计最多 6 个；支持相册上传或 URL"},
    }


@router.post("/me/role")
async def api_set_role(body: RoleBody, user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    try:
        user = await users.set_user_role(user_id, body.role.strip().lower())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "user": _ser_user(user), "role": user.get("role"), "role_label": _ROLE_LABEL.get(user.get("role") or "", "")}


@router.post("/me/alias")
async def api_set_alias(body: AliasBody, user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    user = await user_admin.set_guest_alias(user_id, body.alias)
    return {"ok": True, "user": _ser_user(user)}


@router.get("/gate")
async def api_gate(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    from bot.services import gate
    settings = get_settings()
    if settings.is_admin(user_id):
        return {"ok": True, "required": False, "missing": [], "chats": []}
    return await gate.check_subscriptions(user_id)


@router.get("/me/credit")
async def api_me_credit(user_id: int = Depends(get_current_user_id)) -> Dict[str, Any]:
    raise HTTPException(status_code=410, detail="兰花信用已停用")
