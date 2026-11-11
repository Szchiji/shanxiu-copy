"""Upgrades #3–#7 (YYCJ_TEST_PG): sort=reviews, 浏览计数/我的上架数据, 到期 3天+1天提醒, 评价批量审核, 数据看板."""
import asyncio
import os
from datetime import datetime, timedelta

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


@needs_pg
def test_upgrades():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import Lamp, Review, Session, User
        from bot.models_extra import Favorite
        from bot.services import admin_acl, home_service
        await db.connect_db()
        await db._ensure_columns(db._engine)
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        SUPER, OP, T = 1, 761, 9481
        now = datetime.utcnow()
        async with session_scope() as s:
            s.add(User(user_id=T, role="teacher"))
            for g in (9491, 9492, 9493):
                s.add(User(user_id=g, role="guest"))
            s.add(Lamp(lamp_id="U-A", user_id=T, title="a", city="杭州", status="active", created_at=now - timedelta(days=3)))
            s.add(Lamp(lamp_id="U-B", user_id=T, title="b", city="杭州", status="active", created_at=now - timedelta(days=2)))
            s.add(Lamp(lamp_id="U-C", user_id=T, title="c", city="杭州", status="active", created_at=now - timedelta(days=1),
                       expires_at=now + timedelta(hours=20)))
            s.add(Lamp(lamp_id="U-D", user_id=T, title="d", city="苏州", status="active", expires_at=now + timedelta(days=2, hours=5)))
            # U-A: 2 approved reviews, U-B: 1 approved + 1 pending, U-C: none
            for i, (lid, st) in enumerate([("U-A", "approved"), ("U-A", "approved"), ("U-B", "approved"), ("U-B", "pending"), ("U-C", "pending")]):
                s.add(Review(review_id=f"UR{i}", lamp_id=lid, guest_id=9491, stars=5, status=st))
            s.add(Favorite(user_id=9491, lamp_id="U-A"))
            s.add(Favorite(user_id=9492, lamp_id="U-A"))
            s.add(Session(session_id="US1", lamp_id="U-A", user_a_id=9491, user_b_id=T))
        await home_service.update_settings(enabled_cities=["杭州", "苏州"], ops_config={"city_overrides": {}})
        await admin_acl.save([OP])
        await home_service.update_settings(ops_config={"operator_cities": {str(OP): ["苏州"]}})
        m._ready = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
            # #3 sort
            new = [x["lamp_id"] for x in (await c.get("/api/home?city=杭州&limit=20", headers=tok(9491))).json()["items"]]
            assert new == ["U-C", "U-B", "U-A"], new
            r = (await c.get("/api/home?city=杭州&limit=20&sort=reviews", headers=tok(9491))).json()
            assert [x["lamp_id"] for x in r["items"]] == ["U-A", "U-B", "U-C"] and r["sort"] == "reviews"
            assert [x["review_count"] for x in r["items"]] == [2, 1, 0]
            r = (await c.get("/api/home?city=杭州&limit=20&sort=reviews&q=b", headers=tok(9491))).json()
            assert [x["lamp_id"] for x in r["items"]] == ["U-B"]
            assert (await c.get("/api/home?sort=hot", headers=tok(9491))).status_code == 422
            # #4 views: guests count once per day; owner never
            for uid in (9491, 9491, 9492, T):
                assert (await c.get("/api/lamps/U-A", headers=tok(uid))).status_code == 200
            items = {x["lamp_id"]: x for x in (await c.get("/api/me/listings", headers=tok(T))).json()["items"]}
            await asyncio.sleep(0.3)  # views are counted fire-and-forget
            items = {x["lamp_id"]: x for x in (await c.get("/api/me/listings", headers=tok(T))).json()["items"]}
            assert items["U-A"]["stats"] == {"views": 3, "views_7d": 3, "favs": 2, "favs_7d": 2, "contacts": 1, "contacts_7d": 1}, items["U-A"]["stats"]
            assert items["U-B"]["stats"]["views"] == 0
            # #6 every new review needs 约课凭证 and waits for an admin, even with 评价需审核 off;
            # public photos are no longer accepted, but an old review's photos still show
            await home_service.update_settings(ops_config={"review_require_audit": False})
            r = await c.post("/api/reviews", json={"lamp_id": "U-B", "stars": 5, "text": "plain"}, headers=tok(9493))
            assert r.status_code == 400 and "约课凭证" in r.text
            r = await c.post("/api/reviews", json={"lamp_id": "U-B", "stars": 4, "text": "pics", "photos": ["AgACAgUAAxkBAAIBb2Vk1Zq9xQw"],
                                                   "proofs": ["AgACAgUAAxkBAAIBb2Vk1Zq9xP1", "AgACAgUAAxkBAAIBb2Vk1Zq9xP2"]}, headers=tok(9493))
            assert r.status_code == 200, r.text
            rv = r.json()["review"]
            assert rv["status"] == "pending" and rv["photos"] == [] and rv["proof_count"] == 2, r.text
            pend = {x["review_id"]: x for x in (await c.get("/api/admin/reviews/pending", headers=tok(SUPER))).json()["items"]}
            assert pend[rv["review_id"]]["proofs"] == ["AgACAgUAAxkBAAIBb2Vk1Zq9xP1", "AgACAgUAAxkBAAIBb2Vk1Zq9xP2"]
            await home_service.update_settings(ops_config={"review_require_audit": True})
            r = await c.post("/api/admin/reviews/bulk", json={"ids": [rv["review_id"]], "action": "approve"}, headers=tok(SUPER))
            assert r.json()["done"] == [rv["review_id"]]
            async with session_scope() as s2:
                from bot.models import Review as _R
                s2.add(_R(review_id="UR-OLD", lamp_id="U-B", guest_id=9494, stars=5, text="老评价", photos=["AgACAgUAAxkBAAIBOLD1"], status="approved",
                                  created_at=datetime.utcnow() - timedelta(days=90)))
            det = (await c.get("/api/lamps/U-B", headers=tok(9493))).json()
            assert any(x.get("photos") == ["AgACAgUAAxkBAAIBOLD1"] for x in det["reviews"])
            assert "xP1" not in str(det)   # 约课凭证 never reach the public detail
            # #6 bulk
            r = await c.post("/api/admin/reviews/bulk", json={"ids": ["UR3", "UR4", "nope"], "action": "approve"}, headers=tok(SUPER))
            assert r.status_code == 200 and sorted(r.json()["done"]) == ["UR3", "UR4"] and "nope" in r.json()["failed"], r.text
            r = await c.post("/api/admin/reviews/bulk", json={"ids": ["UR0"], "action": "reject"}, headers=tok(OP))
            assert r.json()["done"] == [] and "UR0" in r.json()["failed"], r.text  # operator of 苏州 can't touch 杭州
            assert (await c.post("/api/admin/reviews/bulk", json={"ids": [], "action": "approve"}, headers=tok(SUPER))).status_code == 422
            assert (await c.post("/api/admin/reviews/bulk", json={"ids": ["UR0"], "action": "approve"}, headers=tok(9491))).status_code in (401, 403)
            # #7 dashboard
            d = (await c.get("/api/admin/stats/daily?days=7", headers=tok(SUPER))).json()
            assert len(d["days"]) == 7 and d["totals"]["杭州"]["listings"] == 3 and d["totals"]["杭州"]["views"] == 4
            assert d["totals"]["杭州"]["contacts"] == 1 and d["totals"]["杭州"]["reviews"] == 6, d["totals"]  # the no-凭证 review is refused now
            d = (await c.get("/api/admin/stats/daily?days=7", headers=tok(OP))).json()
            assert set(d["cities"]) <= {"苏州"}
            # #5 expiring list
            ex = [x for x in (await c.get("/api/admin/listings/expiring?days=7", headers=tok(SUPER))).json()["items"] if x["lamp_id"].startswith("U-")]
            assert [x["lamp_id"] for x in ex] == ["U-C", "U-D"] and ex[0]["hours_left"] < 24
            ex = (await c.get("/api/admin/listings/expiring?days=7", headers=tok(OP))).json()["items"]
            assert {x["city"] for x in ex} <= {"苏州"} and "U-D" in [x["lamp_id"] for x in ex]
        # #5 nudges: U-C gets the 1-day one, U-D the 3-day one; a second run sends nothing
        import sys, types
        sent = []

        class FakeBot:
            async def send_message(self, uid, text, reply_markup=None):
                sent.append((uid, text))
        fake = types.ModuleType("bot.main"); fake.bot = FakeBot()
        real = sys.modules["bot.main"]; sys.modules["bot.main"] = fake
        try:
            from bot.services.expiry_nudge import nudge_expiring
            n1 = await nudge_expiring(3, 1)
            n2 = await nudge_expiring(3, 1)
        finally:
            sys.modules["bot.main"] = real
        assert n2 == 0 and n1 >= 2, (n1, n2, sent)
        assert any(("明天到期" in t or "今天到期" in t) and "「c」" in t for _, t in sent) and any("「d」还有" in t and "天到期" in t for _, t in sent), sent
        await db.close_db()

    asyncio.run(run())


def test_expiry_stage_and_schedule():
    from datetime import datetime
    from bot.services.expiry_nudge import _days_left, _stage
    now = datetime(2026, 10, 31, 2, 0)  # 10:00 Beijing
    assert _days_left(datetime(2026, 11, 3, 15, 0), now) == 3 and _stage(3) == "3d"
    assert _days_left(datetime(2026, 11, 3, 17, 0), now) == 4 and _stage(4) is None   # 01:00 Nov 4 Beijing
    assert _stage(2) == "3d" and _stage(1) == "1d" and _stage(0) == "1d"

    import asyncio
    from bot.services import bot_info

    class Sched:
        running = True
        jobs = {}

        def add_job(self, fn, trigger, **kw):
            self.jobs[kw["id"]] = (trigger, kw)
    sc = Sched()
    calls = []

    async def fake():
        calls.append(1)
    orig = bot_info.job_expiry_nudge
    bot_info.job_expiry_nudge = fake
    try:
        asyncio.run(bot_info.startup_maintenance(sc))
    except Exception:
        pass
    finally:
        bot_info.job_expiry_nudge = orig
    trig, kw = sc.jobs["expiry_nudge"]
    assert trig == "cron" and kw["hour"] == 10 and kw["minute"] == 0 and kw["timezone"] == "Asia/Shanghai"
    assert calls == []  # not run at startup (a deploy never sends reminders)
