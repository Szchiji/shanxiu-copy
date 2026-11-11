"""Mini App static caching + compression.

- HTML (go.html / index.html / desk.html, /app, /app/): never cached, so a deploy shows up on the next open.
- .js / .css requested with a ``?v=`` cache-bust: cached for a year (``immutable``). Every page
  references its assets with ``?v=<build>`` and the build string is bumped on each release, so a
  changed file always gets a new URL.
- .js / .css without ``?v=``: ``no-cache`` — the browser keeps a copy but revalidates (ETag → 304).
- gzip for text responses (Mini App html/js/css and JSON API), never for /api/media (images, video
  ranges) or the Telegram webhook.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

NO_STORE = "no-store, no-cache, must-revalidate, max-age=0"
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"


def cache_control_for(path: str, query: str) -> str | None:
    """Cache-Control for a /app request, or None to leave the response alone."""
    path = path or ""
    if not path.startswith("/app"):
        return None
    if path in ("/app", "/app/") or path.endswith(".html"):
        return NO_STORE
    if path.endswith((".js", ".css")):
        has_v = any(part.split("=", 1)[0] == "v" and part.split("=", 1)[-1] for part in (query or "").split("&"))
        return IMMUTABLE if has_v else REVALIDATE
    return None


class MiniappCache(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        cc = cache_control_for(request.url.path, request.url.query)
        if cc and resp.status_code in (200, 304):
            resp.headers["Cache-Control"] = cc
            if cc == NO_STORE:
                resp.headers["Pragma"] = "no-cache"
                resp.headers["Expires"] = "0"
            else:
                for k in ("Pragma", "Expires"):
                    if k in resp.headers:
                        del resp.headers[k]
        elif cc == NO_STORE:
            resp.headers["Cache-Control"] = cc
        return resp


def should_compress(path: str) -> bool:
    path = path or ""
    if path.startswith("/api/media") or path.startswith("/webhook"):
        return False
    if path.startswith("/api/"):
        return True
    if path in ("/app", "/app/"):
        return True
    return path.startswith("/app/") and path.endswith((".html", ".js", ".css", ".json", ".svg"))


class SelectiveGZip:
    """gzip text responses only; media (images, video byte ranges) pass through untouched."""

    def __init__(self, app: ASGIApp, minimum_size: int = 1024, compresslevel: int = 6) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size, compresslevel=compresslevel)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and scope.get("method") in ("GET", "HEAD") and should_compress(scope.get("path", "")):
            await self.gzip(scope, receive, send)
            return
        await self.app(scope, receive, send)
