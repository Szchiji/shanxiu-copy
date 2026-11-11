"""统一身份与权限。演示仅回环；生产失败关闭，校验 Telegram initData。"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request

from hetai.config import Settings
from hetai.models import User

DEMO_ACTORS = {
    "admin": {"telegram_id": 900001, "label": "示例管理员（超级）", "role": "super"},
    "operator": {"telegram_id": 900002, "label": "示例运营（仅华东运营群）", "role": "operator"},
    "member": {"telegram_id": 900003, "label": "示例成员（仅华东运营群）", "role": "member"},
    "guest": {"telegram_id": 900004, "label": "示例访客（仅测试交流群）", "role": "member"},
}

INITDATA_MAX_AGE = 86400
INITDATA_CLOCK_SKEW = 300
FIXTURE_TELEGRAM_IDS = {spec["telegram_id"] for spec in DEMO_ACTORS.values()}


@dataclass
class Actor:
    user_id: int
    telegram_id: int
    display_name: str
    role: str
    is_demo: bool
    source: str


def is_loopback(request: Request) -> bool:
    import os

    client = request.client.host if request.client else ""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    # Demo must not trust forwarded headers to escape loopback.
    if forwarded and forwarded not in {"127.0.0.1", "::1", "localhost"}:
        return False
    if client in {"127.0.0.1", "::1", "localhost"}:
        return True
    # Starlette TestClient uses host "testserver"; only allowed when tests opt in.
    if os.environ.get("HETAI_PYTEST") == "1" and client in {"testserver", "testclient"}:
        return True
    return False


def validate_init_data(init_data: str, bot_token: str, *, max_age: int = INITDATA_MAX_AGE) -> dict[str, Any]:
    if not init_data or not bot_token:
        raise HTTPException(status_code=401, detail="缺少 initData 或 Bot Token")
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received = parsed.pop("hash", None)
    if not received:
        raise HTTPException(status_code=401, detail="initData 缺少签名")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        raise HTTPException(status_code=401, detail="initData 签名无效")
    auth_date_raw = parsed.get("auth_date")
    if not auth_date_raw:
        raise HTTPException(status_code=401, detail="initData 缺少 auth_date")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="auth_date 无效") from exc
    now = time.time()
    if auth_date > now + INITDATA_CLOCK_SKEW:
        raise HTTPException(status_code=401, detail="initData 的 auth_date 超出允许的时钟偏差")
    if max_age > 0 and now - auth_date > max_age:
        raise HTTPException(status_code=401, detail="initData 已过期")
    user = None
    if parsed.get("user"):
        try:
            user = json.loads(parsed["user"])
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=401, detail="user 字段无法解析") from exc
    if not user or "id" not in user:
        raise HTTPException(status_code=401, detail="initData 无用户信息")
    # chat_instance is never treated as chat_id or as an access grant.
    chat = None
    if parsed.get("chat"):
        try:
            chat = json.loads(parsed["chat"]) if isinstance(parsed["chat"], str) else parsed["chat"]
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=401, detail="chat 字段无法解析") from exc
    return {
        "user": user,
        "auth_date": auth_date,
        "start_param": parsed.get("start_param") or parsed.get("startapp") or "",
        "chat": chat if isinstance(chat, dict) else None,
        "chat_instance": parsed.get("chat_instance"),
    }


def issue_access_token(telegram_id: int, secret: str, *, ttl: int = 86400) -> str:
    exp = int(time.time()) + max(60, ttl)
    payload = f"{int(telegram_id)}:{exp}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{sig}"


def verify_access_token(token: str, secret: str) -> int:
    if not token or not secret:
        raise HTTPException(status_code=401, detail="缺少令牌")
    parts = token.strip().split(":")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="令牌格式错误")
    try:
        telegram_id = int(parts[0])
        exp = int(parts[1])
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="令牌无效") from exc
    if exp < int(time.time()):
        raise HTTPException(status_code=401, detail="令牌已过期")
    expect = hmac.new(secret.encode(), f"{telegram_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expect, parts[2]):
        raise HTTPException(status_code=401, detail="令牌签名无效")
    return telegram_id


def can_admin(role: str) -> bool:
    return role in {"super", "operator"}


def can_super(role: str) -> bool:
    return role == "super"


def is_telegram_surface(request: Request) -> bool:
    path = request.url.path or ""
    return path == "/tg" or path.startswith("/tg/") or path == "/api/auth/telegram"


def has_telegram_session(request: Request) -> bool:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return True
    return bool(request.cookies.get("hetai_token"))


def real_role_for_telegram_id(settings: Settings, telegram_id: int, stored_role: str | None = None) -> str:
    if settings.is_configured_owner(telegram_id):
        return "super"
    role = (stored_role or "member").strip() or "member"
    if role == "super":
        return "member"
    return role


async def resolve_telegram_actor(request: Request, settings: Settings, session, *, require_admin: bool = False) -> Actor:
    if not settings.bot_connected:
        raise HTTPException(status_code=401, detail="未连接测试 Bot，无法使用 Telegram 登录")
    token = None
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.cookies.get("hetai_token")
    if not token:
        raise HTTPException(status_code=401, detail="需要在 Telegram Mini App 中打开并完成登录")
    telegram_id = verify_access_token(token, settings.session_secret)
    if telegram_id in FIXTURE_TELEGRAM_IDS:
        raise HTTPException(status_code=401, detail="不能使用示例身份访问真实测试入口")
    from sqlalchemy import select

    res = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = res.scalar_one_or_none()
    if not user or user.is_demo_fixture:
        raise HTTPException(status_code=401, detail="用户不存在")
    if user.is_banned:
        actor = Actor(
            user_id=user.id,
            telegram_id=user.telegram_id,
            display_name=user.display_name or user.username or str(user.telegram_id),
            role=real_role_for_telegram_id(settings, telegram_id, user.role),
            is_demo=False,
            source="telegram-token",
        )
        from hetai.services.context import attach_actor_shell

        await attach_actor_shell(request, session, actor)
        raise HTTPException(status_code=403, detail="账号已被封禁")
    role = real_role_for_telegram_id(settings, telegram_id, user.role)
    if user.role != role:
        user.role = role
    actor = Actor(
        user_id=user.id,
        telegram_id=user.telegram_id,
        display_name=user.display_name or user.username or str(user.telegram_id),
        role=role,
        is_demo=False,
        source="telegram-token",
    )
    from hetai.services.context import attach_actor_shell

    await attach_actor_shell(request, session, actor)
    if require_admin and not can_admin(actor.role):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return actor


async def resolve_actor(request: Request, settings: Settings, session, *, require_admin: bool = False) -> Actor:
    if is_telegram_surface(request):
        return await resolve_telegram_actor(request, settings, session, require_admin=require_admin)
    if settings.uses_fixtures:
        if settings.is_demo and not is_loopback(request):
            raise HTTPException(status_code=403, detail="演示模式仅允许本机回环访问")
        if settings.is_preview:
            from hetai.preview_auth import valid_preview_session

            if not valid_preview_session(request, settings):
                raise HTTPException(status_code=401, detail="需要预览口令")
        key = (request.query_params.get("as") or request.cookies.get("hetai_demo_as") or "admin").strip()
        if key not in DEMO_ACTORS:
            key = "admin"
        spec = DEMO_ACTORS[key]
        from sqlalchemy import select

        res = await session.execute(select(User).where(User.telegram_id == spec["telegram_id"]))
        user = res.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=500, detail="演示夹具用户未初始化")
        actor = Actor(
            user_id=user.id,
            telegram_id=user.telegram_id,
            display_name=user.display_name or spec["label"],
            role=user.role,
            is_demo=True,
            source="demo-fixture",
        )
        from hetai.services.context import attach_actor_shell

        await attach_actor_shell(request, session, actor)
        if require_admin and not can_admin(actor.role):
            raise HTTPException(status_code=403, detail="当前示例身份没有管理权限")
        return actor

    # production fail-closed
    token = None
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.cookies.get("hetai_token")
    if not token:
        raise HTTPException(status_code=401, detail="需要登录（Telegram initData 签发的令牌）")
    telegram_id = verify_access_token(token, settings.session_secret)
    from sqlalchemy import select

    res = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    role = user.role
    if user.telegram_id in settings.admin_id_list:
        role = "super"
    actor = Actor(
        user_id=user.id,
        telegram_id=user.telegram_id,
        display_name=user.display_name or user.username or str(user.telegram_id),
        role=role,
        is_demo=False,
        source="telegram-token",
    )
    from hetai.services.context import attach_actor_shell

    await attach_actor_shell(request, session, actor)
    if user.is_banned:
        raise HTTPException(status_code=403, detail="账号已被封禁")
    if require_admin and not can_admin(actor.role):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return actor
