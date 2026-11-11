"""API-level checks (ASGI, no lifespan) against a throwaway Postgres (YYCJ_TEST_PG)."""
import asyncio
import os

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_api_guards():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import User, Lamp
        await db.connect_db()
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        async with session_scope() as s:
            s.add(User(user_id=9001, role="guest"))
            s.add(User(user_id=9002, role="teacher"))
            s.add(User(user_id=9003, role="teacher", is_banned=True))
            s.add(Lamp(lamp_id="LX", user_id=9002, title="t", city="c", status="active"))
        m._ready = True
        t = httpx.ASGITransport(app=m.app)
        async with httpx.AsyncClient(transport=t, base_url="http://t") as c:
            files = {"files": ("a.jpg", b"\xff\xd8\xff", "image/jpeg")}
            r = await c.post("/api/media/upload", files=files, headers=tok(9001))
            assert r.status_code == 403, r.text            # guest can't upload
            r = await c.post("/api/media/upload", files={"files": ("a.html", b"<x>", "text/html")}, headers=tok(9002))
            assert r.status_code == 400, r.text            # type whitelist
            r = await c.post("/api/media/upload", files=files)
            assert r.status_code == 401
            r = await c.get("/api/me", headers=tok(9003))
            assert r.status_code == 403, r.text            # banned
            r = await c.get("/api/me", headers=tok(9002))
            assert r.status_code == 200, r.text
            r = await c.get("/api/media/poster/BAACunknown", follow_redirects=False)
            assert r.status_code == 404
            m.settings.webhook_secret = "s3cret"
            wh = m.settings.webhook_path or "/webhook"
            r = await c.post(wh, json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
            assert r.status_code == 403, r.status_code
            r = await c.post(wh, json={"update_id": 1})
            assert r.status_code == 403, r.status_code
            r = await c.post(wh, json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
            assert r.status_code == 200, r.status_code
            # me edit: extras sanitized (internal / markup keys dropped)
            r = await c.post("/api/me/listings/LX/edit", json={"extras": {"_bc_mid": "9", "<b>": "x", "微信": "w"}}, headers=tok(9002))
            from bot.services.extras_store import load_extras
            ex = await load_extras("LX")
            assert "<b>" not in ex and ex.get("_bc_mid") != "9", (r.status_code, r.text, ex)
        await db.close_db()

    asyncio.run(run())
