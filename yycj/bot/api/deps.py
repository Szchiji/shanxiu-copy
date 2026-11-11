"""FastAPI 依赖：当前用户 / 管理员 / 超管。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException

from bot.api.telegram_webapp import WebAppAuthError, verify_access_token
from bot.config import get_settings
from bot.services.admin_acl import is_admin, is_super, warm


async def get_current_user_id(
    authorization: Annotated[str | None, Header()] = None,
    x_yycj_token: Annotated[str | None, Header(alias="X-Yycj-Token")] = None,
) -> int:
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    elif x_yycj_token:
        raw = x_yycj_token.strip()
    if not raw:
        raise HTTPException(status_code=401, detail="需要登录（Bearer token）")
    settings = get_settings()
    try:
        user_id = verify_access_token(raw, settings.bot_token)
    except WebAppAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    await warm()
    if not is_admin(user_id):
        from bot.services.ban_cache import is_banned
        if await is_banned(user_id):
            raise HTTPException(status_code=403, detail="账号已被封禁")
    return user_id


async def get_admin_user_id(user_id: Annotated[int, Depends(get_current_user_id)]) -> int:
    await warm()
    if not is_admin(user_id):
        raise HTTPException(status_code=403, detail="需要管理员权限（ADMIN_IDS）")
    return user_id


async def get_super_user_id(user_id: Annotated[int, Depends(get_admin_user_id)]) -> int:
    if not is_super(user_id):
        raise HTTPException(status_code=403, detail="只有超管能改站点设置")
    return user_id
