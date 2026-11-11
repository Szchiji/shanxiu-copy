"""Telegram Mini App initData HMAC 校验与会话 token。"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl


class WebAppAuthError(ValueError):
    """initData / token 无效。"""


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_sec: int = 86400,
) -> Dict[str, Any]:
    """
    按 Telegram WebApp 规范校验 initData。
    secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
    """
    if not init_data or not bot_token:
        raise WebAppAuthError("缺少 initData 或 BOT_TOKEN")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise WebAppAuthError("initData 缺少 hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise WebAppAuthError("initData 签名无效")

    auth_date_raw = parsed.get("auth_date")
    if auth_date_raw:
        try:
            auth_date = int(auth_date_raw)
        except ValueError as exc:
            raise WebAppAuthError("auth_date 无效") from exc
        if max_age_sec > 0 and time.time() - auth_date > max_age_sec:
            raise WebAppAuthError("initData 已过期")

    user: Optional[Dict[str, Any]] = None
    if "user" in parsed and parsed["user"]:
        try:
            user = json.loads(parsed["user"])
        except json.JSONDecodeError as exc:
            raise WebAppAuthError("user 字段无法解析") from exc
    if not user or "id" not in user:
        raise WebAppAuthError("initData 无用户信息")

    return {
        "user": user,
        "auth_date": int(auth_date_raw) if auth_date_raw else None,
        "query_id": parsed.get("query_id"),
        "raw": parsed,
    }


def issue_access_token(user_id: int, bot_token: str, *, ttl_sec: int = 86400) -> str:
    """轻量 HMAC token：user_id:exp:sig（无第三方 JWT 依赖）。"""
    exp = int(time.time()) + max(60, ttl_sec)
    payload = f"{int(user_id)}:{exp}"
    sig = hmac.new(bot_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[
        :32
    ]
    return f"{payload}:{sig}"


def verify_access_token(token: str, bot_token: str) -> int:
    if not token or not bot_token:
        raise WebAppAuthError("缺少 token")
    parts = token.strip().split(":")
    if len(parts) != 3:
        raise WebAppAuthError("token 格式错误")
    user_id_s, exp_s, sig = parts
    try:
        user_id = int(user_id_s)
        exp = int(exp_s)
    except ValueError as exc:
        raise WebAppAuthError("token 无效") from exc
    if exp < int(time.time()):
        raise WebAppAuthError("token 已过期")
    payload = f"{user_id}:{exp}"
    expect = hmac.new(
        bot_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    if not hmac.compare_digest(expect, sig):
        raise WebAppAuthError("token 签名无效")
    return user_id
