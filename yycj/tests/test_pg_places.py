"""常用位置：地图选点（约500米网格）、老师资料同步、附近默认用常用位置、1km 内（YYCJ_TEST_PG）。"""
import asyncio
import os
import uuid

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_places():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import Lamp, Post, User
        from bot.services import home_service, places
        from sqlalchemy import select
        db._engine = None
        await db.connect_db()
        await db._ensure_columns(db._engine)
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        T1, T2, G = 9701, 9702, 9711
        l1, l2 = str(uuid.uuid4()), str(uuid.uuid4())
        async with session_scope() as s:
            s.add(User(user_id=T1, role="teacher"))
            s.add(User(user_id=T2, role="teacher"))
            s.add(User(user_id=G, role="guest"))
            s.add(Lamp(lamp_id=l1, user_id=T1, title="朝朝", city="北京", district="朝阳", approx_label="三里屯附近", price_text="900", status="active"))
            s.add(Lamp(lamp_id=l2, user_id=T2, title="海海", city="北京", district="海淀", price_text="900", status="active"))
        prev = list((await home_service.get_or_create_settings()).get("enabled_cities") or [])
        await home_service.update_settings(enabled_cities=prev + ["北京"] if "北京" not in prev else prev)
        m._ready = True
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
                g = tok(G)
                home = lambda qs="": c.get("/api/home?city=北京&limit=20&" + qs, headers=g)
                assert places.snap(39.9213, 116.4433) == (39.92, 116.445)
                assert places.snap("x", 1) is None and places.snap(0, 0) is None
                r = (await c.get("/api/me/place", headers=g)).json()
                assert r["place"]["set"] is False
                # no saved place: no distance at all
                r = await home()
                assert r.json()["filters"]["near_src"] == "" and not any(x.get("distance") for x in r.json()["items"])
                assert r.json()["filters"]["has_place"] is False
                # guest pins 朝阳 → rounded, used by default for 附近
                r = await c.post("/api/me/place", json={"lat": 39.92131, "lng": 116.44329}, headers=g)
                assert r.status_code == 200 and r.json()["place"]["lat"] == 39.92 and r.json()["place"]["lng"] == 116.445
                assert (await c.post("/api/me/place", json={"lat": 200, "lng": 1}, headers=g)).status_code == 400
                r = await home()
                f = r.json()["filters"]
                assert f["near_src"] == "place" and f["has_place"] is True
                by = {x["title"]: x for x in r.json()["items"]}
                assert by["朝朝"]["distance"] and by["海海"]["distance"]   # district-centre estimates
                assert "116.445" not in r.text and "approx_lat" not in r.text
                # place=0 → this session turned it off
                r = await home("place=0")
                assert r.json()["filters"]["near_src"] == "" and not any(x.get("distance") for x in r.json()["items"])
                facets = (await c.get("/api/home/facets?city=北京", headers=g)).json()
                assert 1 not in facets["radii"]
                # teacher pins → listing updated at once (no re-review), old text label replaced
                r = await c.post("/api/me/place", json={"lat": 39.9226, "lng": 116.4441}, headers=tok(T1))
                assert r.status_code == 200 and r.json()["place"]["listings"] == 1
                async with session_scope() as s:
                    lamp = (await s.execute(select(Lamp).where(Lamp.lamp_id == l1))).scalar_one()
                    assert (lamp.approx_lat, lamp.approx_lng) == (39.925, 116.445) and lamp.approx_label is None
                    assert lamp.status == "active"
                facets = (await c.get("/api/home/facets?city=北京", headers=g)).json()
                assert facets["radii"] == [1, 3, 5, 10]
                r = await home("radius=1")
                assert [x["title"] for x in r.json()["items"]] == ["朝朝"] and r.json()["items"][0]["distance"] == "1km内"
                assert "39.925" not in r.text
                # a picked 区 is only an estimate: no 「1km内」
                r = await home("at=朝阳")
                assert {x["title"]: x["distance"] for x in r.json()["items"]}["朝朝"] != "1km内"
                d = await c.get(f"/api/lamps/{l1}", headers=g)
                assert d.status_code == 200 and "39.925" not in d.text and "approx_lat" not in d.text
                # 我的 shows the teacher's point
                r = (await c.get("/api/me/place", headers=tok(T1))).json()["place"]
                assert r["set"] and r["source"] == "saved" and r["lat"] == 39.925
                # pending submission follows the pin too
                async with session_scope() as s:
                    s.add(Post(post_id="PL-1", user_id=T1, status="pending", lamp_data={"city": "北京", "title": "x", "approx_label": "旧"}))
                await places.set_point(T1, 39.93, 116.45)
                async with session_scope() as s:
                    p = (await s.execute(select(Post).where(Post.post_id == "PL-1"))).scalar_one()
                    assert p.lamp_data["approx_lat"] == 39.93 and p.lamp_data["approx_label"] is None
                    await s.delete(p)
                # clearing → falls back to the district centre
                assert (await c.delete("/api/me/place", headers=tok(T1))).status_code == 200
                async with session_scope() as s:
                    lamp = (await s.execute(select(Lamp).where(Lamp.lamp_id == l1))).scalar_one()
                    assert lamp.approx_lat is None
                assert (await c.get("/api/me/place", headers=tok(T1))).json()["place"]["set"] is False
                cen = (await c.get("/api/geo/centers?city=北京", headers=g)).json()
                assert "朝阳" in cen["districts"] and len(cen["center"]) == 2
        finally:
            async with session_scope() as s:
                for lid in (l1, l2):
                    x = (await s.execute(select(Lamp).where(Lamp.lamp_id == lid))).scalar_one_or_none()
                    if x:
                        await s.delete(x)
            await places.clear(G)
            await home_service.update_settings(enabled_cities=prev)
            await db.close_db()

    asyncio.run(run())
