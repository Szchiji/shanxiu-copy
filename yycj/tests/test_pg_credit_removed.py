"""Isolated Postgres: retired credit endpoints are gone; leftover shadow flags do not gate business; bans still work."""
import asyncio
import os
import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_retired_credit_endpoints_and_gates():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import CreditHistory, Lamp, User
        from sqlalchemy import func, select

        await db.connect_db()
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        async with session_scope() as s:
            s.add(User(user_id=18601, role="guest"))
            s.add(User(user_id=18602, role="teacher", is_shadowed=True, shadow_days=9, lanhua_score=50, tier="暗月"))
            s.add(User(user_id=18603, role="teacher", is_banned=True, ban_reason="test"))
            s.add(Lamp(
                lamp_id="credit-rm-L1",
                user_id=18602,
                title="残留遮蔽老师的资料",
                city="深圳",
                status="active",
                authenticity_score=91,
                credit_boost=7,
            ))
        m._ready = True
        t = httpx.ASGITransport(app=m.app)
        async with httpx.AsyncClient(transport=t, base_url="http://credit-removed.test") as c:
            r = await c.get("/api/me/credit")
            assert r.status_code == 401

            r = await c.get("/api/me/credit", headers=tok(18601))
            assert r.status_code == 410, r.text
            assert "兰花信用已停用" in (r.json().get("detail") or "")

            r = await c.post("/api/admin/credit/adjust", json={"user_id": 18602, "delta": 10, "note": "x"}, headers=tok(1))
            assert r.status_code == 410, r.text

            r = await c.get("/api/admin/shadow", headers=tok(1))
            assert r.status_code == 410, r.text

            r = await c.post("/api/admin/users/shadow", json={"user_id": 18602, "shadowed": True, "days": 3, "reason": "x"}, headers=tok(1))
            assert r.status_code == 410, r.text

            r = await c.get("/api/me", headers=tok(18602))
            assert r.status_code == 200, r.text
            user = r.json()["user"]
            for k in ("lanhua_score", "tier", "is_shadowed", "shadow_days", "shadow_reason", "total_earned", "total_deducted"):
                assert k not in user, k
            assert user["user_id"] == 18602 and user["role"] == "teacher"

            r = await c.get("/api/me", headers=tok(18603))
            assert r.status_code == 403, r.text

            # leftover is_shadowed must not block publish / edit
            r = await c.post("/api/posts", json={
                "city": "深圳", "title": "遮蔽残留仍可投", "description": "x",
                "tags": ["测"], "photos": [], "media": [],
            }, headers=tok(18602))
            assert r.status_code in (200, 201), r.text

            r = await c.post("/api/me/listings/credit-rm-L1/edit", json={
                "operation_id": "credit-rm-edit-18602-1",
                "city": "深圳", "title": "改稿不被遮蔽挡住", "tags": ["测"],
                "price": None, "price_text": "面议", "description": "updated",
                "photos": [], "media": [],
            }, headers=tok(18602))
            assert r.status_code == 200, r.text

            r = await c.get("/api/home?city=深圳", headers=tok(18601))
            assert r.status_code == 200, r.text
            assert "scoring_rules" not in r.json()

            r = await c.get("/api/lamps/credit-rm-L1", headers=tok(18601))
            assert r.status_code == 200, r.text
            body = r.json()
            lamp = body["lamp"]
            assert "authenticity_score" not in lamp and "credit_boost" not in lamp
            assert "rules" not in (body.get("reputation") or {})

            # leftover CreditHistory rows are untouched by retired APIs (no new writes)
            async with session_scope() as s:
                before = (await s.execute(select(func.count()).select_from(CreditHistory).where(CreditHistory.user_id == 18602))).scalar_one()
            r = await c.post("/api/admin/credit/adjust", json={"user_id": 18602, "delta": 99, "note": "no"}, headers=tok(1))
            assert r.status_code == 410
            async with session_scope() as s:
                after = (await s.execute(select(func.count()).select_from(CreditHistory).where(CreditHistory.user_id == 18602))).scalar_one()
            assert after == before

        await db.close_db()

    asyncio.run(run())
