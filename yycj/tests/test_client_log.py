"""Client error reports: whitelisted, short, no secrets, never fails, rate limited."""
import asyncio
import logging

import httpx
from fastapi import FastAPI

from bot.api import routes_client_log as cl
from bot.api.routes_core import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return app


def test_sanitize_keeps_whitelist_and_drops_secrets():
    ev = cl.sanitize({
        "ev": "detail_fail", "lamp": "e150eb99-998a-4da3-b627-ffa7d84e2cf5", "status": 503, "via": "feed",
        "msg": "boom 123456789:AAAbbbcccdddeeefffggghhhiiijjjkkk", "token": "secret", "user": "someone",
    })
    assert ev["lamp"] == "e150eb99-998a-4da3-b627-ffa7d84e2cf5" and ev["status"] == "503"
    assert "token" not in ev and "user" not in ev
    assert "AAAbbb" not in ev["msg"]
    assert cl.sanitize({"lamp": "<img onerror=x>"})["lamp"] == "?"
    assert cl.sanitize("nope") == {}
    assert len(cl.sanitize({"msg": "x" * 999})["msg"]) <= 160


def test_endpoint_logs_one_line_and_never_errors(caplog):
    cl._RATE.clear()

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://t") as c:
            url = "/api/client-log"
            with caplog.at_level(logging.WARNING, logger="yycj.client"):
                r = await c.post(url, json={"ev": "detail_blank", "lamp": "L1", "via": "fav", "ms": 120})
            assert r.status_code == 200 and r.json()["ok"] is True
            assert any("client ev=detail_blank" in m and "via=fav" in m for m in caplog.messages)
            r = await c.post(url, content=b"not json", headers={"Content-Type": "application/json"})
            assert r.status_code == 200
            r = await c.post(url, content=b"x" * 5000)
            assert r.status_code == 200 and r.json()["ok"] is False
    asyncio.run(go())


def test_rate_limit():
    cl._RATE.clear()
    assert all(cl._allow() for _ in range(cl._RATE_MAX))
    assert cl._allow() is False
    cl._RATE.clear()
