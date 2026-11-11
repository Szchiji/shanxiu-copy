"""City scope: per-city filters, counts and operator enforcement (throwaway Postgres, YYCJ_TEST_PG)."""
import asyncio
import os

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_city_scope():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import HomepagePin, Lamp, Post, Report, Review, User
        from bot.services import admin_acl, home_service
        await db.connect_db()
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        SUPER, OP, OP_ALL = 1, 500, 501
        async with session_scope() as s:
            for uid in (11, 12, 13, 21, 22):
                s.add(User(user_id=uid, role="teacher"))
            s.add(Lamp(lamp_id="SZ1", user_id=11, title="sz1", city="深圳", status="active"))
            s.add(Lamp(lamp_id="SZ2", user_id=12, title="sz2", city="深圳", status="active", feed_pinned=True))
            s.add(Lamp(lamp_id="GZ1", user_id=13, title="gz1", city="广州", status="active"))
            s.add(Post(post_id="PSZ", user_id=11, lamp_data={"city": "深圳", "title": "a"}, status="pending"))
            s.add(Post(post_id="PGZ", user_id=13, lamp_data={"city": "广州", "title": "b"}, status="pending"))
            s.add(Post(post_id="PGZ2", user_id=13, lamp_data={"city": "广州", "title": "c"}, status="pending"))
            s.add(Review(review_id="RSZ", lamp_id="SZ1", guest_id=21, stars=5, status="pending"))
            s.add(Review(review_id="RGZ", lamp_id="GZ1", guest_id=22, stars=4, status="pending"))
            s.add(Report(report_id="XGZ", lamp_id="GZ1", reporter_id=22, reason="r", status="pending"))
            s.add(HomepagePin(lamp_id="SZ1", sort_order=0))
            s.add(HomepagePin(lamp_id="GZ1", sort_order=1))
        await admin_acl.save([OP, OP_ALL])
        await home_service.update_settings(ops_config={"operator_cities": {str(OP): ["广州"]}})
        m._ready = True
        t = httpx.ASGITransport(app=m.app)
        async with httpx.AsyncClient(transport=t, base_url="http://t") as c:
            g = lambda path, uid: c.get(path, headers=tok(uid))
            ids = lambda r, k: sorted(x[k] for x in r.json()["items"])
            # super: everything, ?city filters
            assert ids(await g("/api/admin/posts/pending", SUPER), "post_id") == ["PGZ", "PGZ2", "PSZ"]
            assert ids(await g("/api/admin/posts/pending?city=深圳", SUPER), "post_id") == ["PSZ"]
            r = await g("/api/admin/reviews/pending?city=广州", SUPER)
            assert ids(r, "review_id") == ["RGZ"] and r.json()["items"][0]["city"] == "广州"
            assert ids(await g("/api/admin/listings?city=深圳", SUPER), "lamp_id") == ["SZ1", "SZ2"]
            h = (await g("/api/admin/homepage?city=广州", SUPER)).json()
            assert [p["lamp_id"] for p in h["pins"]] == ["GZ1"] and h["feed_pins"] == []
            sm = (await g("/api/admin/cities/summary", SUPER)).json()
            by = {x["city"]: x for x in sm["cities"]}
            assert by["深圳"]["pending"] == 2 and by["广州"]["pending"] == 4 and sm["total"]["pending"] == 6, sm
            assert by["深圳"]["feed_pins"] == 1 and by["广州"]["carousel"] == 1 and sm["scope"] is None
            # scoped operator: only 广州, other city -> 403
            assert ids(await g("/api/admin/posts/pending", OP), "post_id") == ["PGZ", "PGZ2"]
            assert (await g("/api/admin/posts/pending?city=深圳", OP)).status_code == 403
            assert ids(await g("/api/admin/listings", OP), "lamp_id") == ["GZ1"]
            sm = (await g("/api/admin/cities/summary", OP)).json()
            assert sm["scope"] == ["广州"] and [x["city"] for x in sm["cities"]] == ["广州"]
            r = await c.post("/api/admin/posts/PSZ/reject", headers=tok(OP))
            assert r.status_code == 403, r.text
            r = await c.post("/api/admin/reviews/RSZ/approve", headers=tok(OP))
            assert r.status_code == 403, r.text
            r = await c.post("/api/admin/listings/SZ1/unlist", json={}, headers=tok(OP))
            assert r.status_code == 403, r.text
            r = await c.post("/api/admin/homepage/feed-pin", json={"lamp_id": "SZ1"}, headers=tok(OP))
            assert r.status_code == 403, r.text
            r = await c.post("/api/admin/users/ban", json={"user_id": 11, "banned": True}, headers=tok(OP))
            assert r.status_code == 403, r.text
            r = await c.post("/api/admin/reviews/RGZ/approve", headers=tok(OP))
            assert r.status_code == 200, r.text
            r = await c.post("/api/admin/cities/announcement", json={"city": "深圳", "text": "x"}, headers=tok(OP))
            assert r.status_code == 403
            r = await c.post("/api/admin/cities/announcement", json={"city": "广州", "text": "广州公告"}, headers=tok(OP))
            assert r.status_code == 200 and r.json()["city_announcements"]["广州"]["enabled"], r.text
            # unscoped operator still sees everything (backwards compatible), can't set city announcement
            assert ids(await g("/api/admin/posts/pending", OP_ALL), "post_id") == ["PGZ", "PGZ2", "PSZ"]
            r = await c.post("/api/admin/cities/announcement", json={"city": "深圳", "text": "x"}, headers=tok(OP_ALL))
            assert r.status_code == 403
            # only super sets operator cities; must be an operator
            r = await c.post("/api/admin/operators/cities", json={"operator_cities": {str(OP): ["东莞"]}}, headers=tok(OP))
            assert r.status_code == 403
            r = await c.post("/api/admin/operators/cities", json={"operator_cities": {"999": ["东莞"]}}, headers=tok(SUPER))
            assert r.status_code == 400
            # home: 广州 gets its own announcement, 深圳 keeps the global one
            a_gz = (await g("/api/home?city=广州", 21)).json()["announcement"]
            a_sz = (await g("/api/home?city=深圳", 21)).json()["announcement"]
            assert a_gz["text"] == "广州公告" and (a_sz or {}).get("text") != "广州公告", (a_gz, a_sz)
        await db.close_db()

    asyncio.run(run())
