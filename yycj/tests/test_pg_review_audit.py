"""评价需审核 switch: ON = pending queue (today), OFF = published directly; per-city override (YYCJ_TEST_PG)."""
import asyncio
import os

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_merge_ops_audit_default_on():
    from bot.services.home_feed import audit_on, merge_ops
    assert merge_ops({})["review_require_audit"] is True
    assert merge_ops({"review_require_audit": None})["review_require_audit"] is True
    assert merge_ops({"review_require_audit": "junk"})["review_require_audit"] is True
    assert merge_ops({"review_require_audit": False})["review_require_audit"] is False
    assert audit_on("false") is False and audit_on(True) is True


def test_review_audit_switch():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import Lamp, User
        from bot.services import admin_acl, home_service
        await db.connect_db()
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        SUPER, OP = 1, 700
        async with session_scope() as s:
            s.add(User(user_id=71, role="teacher"))
            for g in (81, 82, 83, 84, 85, 86):
                s.add(User(user_id=g, role="guest"))
            s.add(Lamp(lamp_id="RA-SZ", user_id=71, title="sz", city="深圳", status="active"))
            s.add(Lamp(lamp_id="RA-GZ", user_id=71, title="gz", city="广州", status="active"))
            s.add(Lamp(lamp_id="RA-NC", user_id=71, title="nocity", city="", status="active"))
        await home_service.update_settings(enabled_cities=["深圳", "广州", "东莞"],
                                           ops_config={"city_overrides": {}, "review_require_audit": True})
        await admin_acl.save([OP])
        await home_service.update_settings(ops_config={"operator_cities": {str(OP): ["广州"]}})
        m._ready = True
        t = httpx.ASGITransport(app=m.app)
        async with httpx.AsyncClient(transport=t, base_url="http://t") as c:
            async def review(guest, lamp):
                r = await c.post("/api/reviews", json={"lamp_id": lamp, "stars": 4, "text": "ok", "proofs": ["AgACAgUAAxkBAAIBprfA"]}, headers=tok(guest))
                assert r.status_code == 200, r.text
                return r.json()["review"]
            # ON (today): pending queue
            r1 = await review(81, "RA-SZ")
            assert r1["status"] == "pending"
            ids = [x["review_id"] for x in (await c.get("/api/admin/reviews/pending", headers=tok(SUPER))).json()["items"]]
            assert r1["review_id"] in ids
            # site-wide OFF: every review now carries 约课凭证 for the admins, so it still waits
            await home_service.update_settings(ops_config={"review_require_audit": False})
            r2 = await review(82, "RA-GZ")
            assert r2["status"] == "pending"
            ids = [x["review_id"] for x in (await c.get("/api/admin/reviews/pending", headers=tok(SUPER))).json()["items"]]
            assert r2["review_id"] in ids
            assert (await review(83, "RA-NC"))["status"] == "pending"
            # per-city override: 深圳 forces audit ON while site-wide is OFF
            r = await c.post("/api/admin/cities/settings", json={"city": "深圳", "overrides": {"review_require_audit": True}}, headers=tok(SUPER))
            assert r.status_code == 200 and r.json()["overrides"]["review_require_audit"] is True, r.text
            assert (await review(84, "RA-SZ"))["status"] == "pending"
            # site-wide back ON; the 广州 operator turns audit OFF for 广州 only (non-super key)
            await home_service.update_settings(ops_config={"review_require_audit": True})
            r = await c.post("/api/admin/cities/settings", json={"city": "广州", "overrides": {"review_require_audit": False}}, headers=tok(OP))
            assert r.status_code == 200, r.text
            assert (await review(85, "RA-GZ"))["status"] == "pending"
            assert (await review(86, "RA-SZ"))["status"] == "pending"
            # operator can't touch another city's switch
            r = await c.post("/api/admin/cities/settings", json={"city": "深圳", "overrides": {"review_require_audit": False}}, headers=tok(OP))
            assert r.status_code == 403
            # clearing the override falls back to site-wide (ON)
            r = await c.post("/api/admin/cities/settings", json={"city": "广州", "overrides": {"review_require_audit": None}}, headers=tok(SUPER))
            assert "review_require_audit" not in r.json()["overrides"]
            assert await home_service.review_needs_audit("广州") is True

    asyncio.run(run())
