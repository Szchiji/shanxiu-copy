"""首页地区筛选（YYCJ_TEST_PG）。"""
import asyncio
import os

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_district_filter():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import Lamp, User
        await db.connect_db()
        await db._ensure_columns(db._engine)
        async with session_scope() as s:
            s.add(User(user_id=9201, role="guest"))
            for i, (city, d) in enumerate([("深圳", "南山区"), ("深圳", "南山"), ("深圳", "福田"), ("深圳", None), ("广州", "朝阳")]):
                s.add(Lamp(lamp_id=f"D{i}", user_id=9201, title=f"t{i}", city=city, district=d, status="active"))
        from bot.models import HomepagePin
        async with session_scope() as s:
            s.add(HomepagePin(lamp_id="D0", sort_order=1))   # 南山区
            s.add(HomepagePin(lamp_id="D2", sort_order=2))   # 福田
        m._ready = True
        tok = {"Authorization": "Bearer " + issue_access_token(9201, get_settings().bot_token)}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
            r = await c.get("/api/home?city=深圳&limit=20", headers=tok)
            data = r.json()
            assert data["city"] == "深圳", (data["city"], data["enabled_cities"])
            assert data["districts"]["total"] == 4, data["districts"]
            assert data["districts"]["items"] == [{"key": "南山", "count": 2}, {"key": "福田", "count": 1}]
            r = await c.get("/api/home?city=深圳&limit=20&district=南山", headers=tok)
            ids = sorted(x["lamp_id"] for x in r.json()["items"])
            assert ids == ["D0", "D1"] and r.json()["district"] == "南山"
            assert [p["lamp"]["lamp_id"] for p in r.json()["pins"]] == ["D0"]      # carousel filtered too
            r = await c.get("/api/home?city=深圳&limit=20", headers=tok)
            assert [p["lamp"]["lamp_id"] for p in r.json()["pins"]] == ["D0", "D2"]
            r = await c.get("/api/home?city=深圳&limit=20&district=宝安", headers=tok)
            assert r.json()["pins"] == [] and r.json()["items"] == []               # empty → carousel hidden client-side
            r = await c.get("/api/home?city=深圳&limit=20&offset=1&district=南山", headers=tok)
            assert r.json()["districts"] is None
            r = await c.get("/api/home?city=深圳&district=" + "x" * 40, headers=tok)
            assert r.status_code == 422
        await db.close_db()

    asyncio.run(run())
