"""公开预览访问门：口令会话、登录限流、CSRF/Origin 校验。"""

from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from hetai.config import PREVIEW_ACCESS_MIN_LENGTH, Settings, get_settings, is_strong_secret

COOKIE_SESSION = "hetai_preview"
COOKIE_CSRF = "hetai_csrf"
SESSION_TTL = 12 * 3600
LOGIN_WINDOW = 600
LOGIN_MAX_FAILURES = 5
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
OPEN_EXACT = {"/health", "/preview/login"}


class LoginRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)

    def too_many(self, key: str) -> bool:
        now = time.time()
        recent = [t for t in self._hits[key] if now - t < LOGIN_WINDOW]
        self._hits[key] = recent
        return len(recent) >= LOGIN_MAX_FAILURES

    def fail(self, key: str) -> None:
        self._hits[key].append(time.time())

    def clear(self, key: str) -> None:
        self._hits.pop(key, None)


limiter = LoginRateLimiter()


def _client_key(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def public_origin(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    host = (host or "").split(",")[0].strip()
    return f"{proto}://{host}"


def _sign(payload: str, secret: str) -> str:
    sig = hmac.new(secret.encode(), payload.encode(), "sha256").hexdigest()[:32]
    return f"{payload}:{sig}"


def _verify(token: str, secret: str) -> str | None:
    if not token or not secret or token.count(":") < 1:
        return None
    payload, sig = token.rsplit(":", 1)
    expect = hmac.new(secret.encode(), payload.encode(), "sha256").hexdigest()[:32]
    if not hmac.compare_digest(expect, sig):
        return None
    return payload


def issue_preview_session(settings: Settings) -> str:
    exp = int(time.time()) + SESSION_TTL
    nonce = secrets.token_hex(8)
    return _sign(f"preview:{exp}:{nonce}", settings.session_secret)


def valid_preview_session(request: Request, settings: Settings) -> bool:
    token = request.cookies.get(COOKIE_SESSION) or ""
    payload = _verify(token, settings.session_secret)
    if not payload:
        return False
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "preview":
        return False
    try:
        exp = int(parts[1])
    except ValueError:
        return False
    return exp >= int(time.time())


def issue_csrf_token(settings: Settings) -> str:
    nonce = secrets.token_hex(16)
    return _sign(f"csrf:{nonce}", settings.session_secret)


def valid_csrf_token(token: str, settings: Settings) -> bool:
    payload = _verify(token or "", settings.session_secret)
    return bool(payload and payload.startswith("csrf:"))


def origin_allowed(request: Request) -> bool:
    expected = public_origin(request)
    origin = (request.headers.get("origin") or "").strip()
    if origin:
        return origin == expected
    referer = (request.headers.get("referer") or "").strip()
    if not referer:
        return False
    parsed = urlparse(referer)
    return f"{parsed.scheme}://{parsed.netloc}" == expected


async def _extract_csrf(request: Request) -> str:
    header = request.headers.get("x-csrf-token") or ""
    if header:
        return header
    if request.cookies.get(COOKIE_CSRF):
        # form field preferred; cookie alone is not enough (need double submit)
        pass
    ctype = request.headers.get("content-type") or ""
    if "application/x-www-form-urlencoded" in ctype or "multipart/form-data" in ctype:
        form = await request.form()
        return str(form.get("csrf_token") or "")
    if "application/json" in ctype:
        try:
            body = await request.json()
        except Exception:
            return ""
        if isinstance(body, dict):
            return str(body.get("csrf_token") or "")
    return ""


def is_open_path(path: str) -> bool:
    if path in OPEN_EXACT:
        return True
    if path.startswith("/static/"):
        return True
    return False


def set_auth_cookies(resp: Response, request: Request, settings: Settings, session_token: str, csrf_token: str) -> None:
    secure = public_origin(request).startswith("https://")
    resp.set_cookie(
        COOKIE_SESSION,
        session_token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=SESSION_TTL,
        path="/",
    )
    resp.set_cookie(
        COOKIE_CSRF,
        csrf_token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=SESSION_TTL,
        path="/",
    )


def clear_auth_cookies(resp: Response) -> None:
    resp.delete_cookie(COOKIE_SESSION, path="/")
    resp.delete_cookie(COOKIE_CSRF, path="/")


def safe_next(value: str | None) -> str:
    dest = (value or "/app").strip() or "/app"
    if not dest.startswith("/") or dest.startswith("//"):
        return "/app"
    if dest.startswith("/preview/login"):
        return "/app"
    return dest


def verify_preview_password(provided: str, settings: Settings) -> bool:
    expected = settings.preview_access_secret
    if not is_strong_secret(expected, min_length=PREVIEW_ACCESS_MIN_LENGTH) or not provided:
        return False
    if len(provided) != len(expected):
        # still compare hashes to avoid leaking length via exception
        left = hmac.new(b"hetai-preview", provided.encode(), "sha256").digest()
        right = hmac.new(b"hetai-preview", expected.encode(), "sha256").digest()
        hmac.compare_digest(left, right)
        return False
    return hmac.compare_digest(provided, expected)


class PreviewAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if not settings.is_preview:
            return await call_next(request)
        path = request.url.path
        open_path = is_open_path(path)
        mutating = request.method not in SAFE_METHODS
        if mutating and not origin_allowed(request):
            if path == "/preview/login":
                return RedirectResponse("/preview/login?error=csrf", status_code=303)
            return JSONResponse({"detail": "跨站请求已拒绝"}, status_code=403)
        if open_path:
            return await call_next(request)
        if valid_preview_session(request, settings):
            return await call_next(request)
        if "text/html" in (request.headers.get("accept") or "") or request.method == "GET":
            nxt = request.url.path
            if request.url.query:
                nxt = nxt + "?" + request.url.query
            return RedirectResponse(f"/preview/login?next={nxt}", status_code=303)
        return JSONResponse({"detail": "需要预览口令"}, status_code=401)
