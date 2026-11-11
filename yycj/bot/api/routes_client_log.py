"""Mini App client error reports -> server log.

The Mini App posts a short event when a detail page fails to load or a script throws, so real
failures on phones show up in the Railway logs. No auth needed (a 401 is one of the things being
reported). Only whitelisted short fields are kept; no token, user id, name or free text from the user.
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from typing import Any, Deque, Dict

from fastapi import Request

from bot.api.routes_core import router

logger = logging.getLogger("yycj.client")

_FIELDS = {
    "ev": 32,       # event: detail_fail / detail_blank / detail_slow / js_error / auth_refresh
    "lamp": 40,     # listing id (public)
    "status": 8,    # http status or "net" / "timeout"
    "ms": 8,        # elapsed ms
    "via": 16,      # feed / pin / fav / deep / other
    "try": 4,       # attempt number
    "v": 16,        # asset version
    "plat": 16,     # Telegram platform (ios / android / tdesktop ...)
    "tgv": 8,       # Telegram WebApp version
    "msg": 160,     # js error message
    "src": 48,      # js file basename:line
}
_SAFE = re.compile(r"[^\w\-.:/ ()\[\]'\"#,=+?!@%&*<>{}\u4e00-\u9fff]")
# drop anything that looks like a credential (bot token / bearer token / long hex or base64 blobs)
_SECRETISH = re.compile(r"\d{6,}:[\w-]{20,}|[A-Za-z0-9_\-]{32,}")
_RATE: Deque[float] = deque()
_RATE_MAX = 120  # events per minute across all clients


def _clean(v: Any, n: int, secrets: bool = True) -> str:
    s = str(v if v is not None else "")[: n * 2]
    if secrets:
        s = _SECRETISH.sub("[x]", s)
    s = _SAFE.sub("", s).replace("\n", " ")
    return s[:n]


def sanitize(body: Any) -> Dict[str, str]:
    if not isinstance(body, dict):
        return {}
    out = {}
    for k, n in _FIELDS.items():
        if k in body and body[k] not in (None, ""):
            out[k] = _clean(body[k], n, secrets=(k != "lamp"))
    if "lamp" in out and not re.fullmatch(r"[0-9a-fA-F-]{8,40}|L?\d{1,12}", out["lamp"]):
        out["lamp"] = "?"
    return out


def _allow() -> bool:
    now = time.monotonic()
    while _RATE and now - _RATE[0] > 60:
        _RATE.popleft()
    if len(_RATE) >= _RATE_MAX:
        return False
    _RATE.append(now)
    return True


@router.post("/client-log")
async def api_client_log(request: Request) -> Dict[str, Any]:
    try:
        raw = await request.body()
        if len(raw) > 4096:
            return {"ok": False}
        import json

        body = json.loads(raw or b"{}")
    except Exception:
        return {"ok": False}
    ev = sanitize(body)
    if ev and _allow():
        logger.warning("client %s", " ".join(f"{k}={v}" for k, v in ev.items()))
    return {"ok": True}
