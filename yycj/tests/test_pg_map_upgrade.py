"""Only disposable local PG: combined public map filters and their bounded candidate window."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_map_combined_filters_privacy_expiry_and_regions(monkeypatch):
    async def run():
        import httpx
        from fastapi import FastAPI
        from bot import db
        from bot.api import routes_core_home as rh
        from bot.api.deps import get_current_user_id
        from bot.db import session_scope
        from bot.models import Lamp, User
        from bot.services import extras_store, home_service, open_shift, places, pin_query, listing_stats
        db._engine = None
        await db.connect_db()
        await db._ensure_columns(db._engine)
        monkeypatch.setattr(open_shift, "now_sh", lambda: datetime(2026, 11, 11, 12, tzinfo=ZoneInfo("Asia/Shanghai")))
        monkeypatch.setattr(places, "get_point", AsyncMock(return_value=None))
        monkeypatch.setattr(pin_query, "list_active_pins", AsyncMock(return_value=[]))
        monkeypatch.setattr(rh, "_home_contacts", AsyncMock(return_value={}))
        monkeypatch.setattr(home_service, "reputation_for_lamp", AsyncMock(return_value={}))
        monkeypatch.setattr(home_service, "list_reviews_for_lamp", AsyncMock(return_value=[]))
        monkeypatch.setattr(listing_stats, "record_view_soon", lambda *a: None)
        await home_service.update_settings(enabled_cities=["深圳", "广州"], ops_config={"coupons_enabled": False})
        names = ["open", "rest", "daily", "wait", "closed", "unset", "missing", "custom", "hidden", "unlisted", "pending", "expired", "other"]
        ids = {n: str(uuid.uuid4()) for n in names}
        now = datetime.utcnow()
        async with session_scope() as s:
            s.add(User(user_id=99171, role="teacher"))
            for name, lid in ids.items():
                s.add(Lamp(lamp_id=lid, user_id=99171, title=name, city="广州" if name=="other" else "深圳", district=None if name=="missing" else "特殊片区" if name=="custom" else "天河" if name=="other" else "南山区", status=name if name in ("hidden", "unlisted", "pending") else "active", expires_at=now-timedelta(days=1) if name=="expired" else now+timedelta(days=3), approx_lat=22.123456, approx_lng=113.123456, tags=["温柔"], price=800))
            # These newer ineligible records must be removed in SQL before LIMIT600.
            for i in range(605):
                s.add(Lamp(lamp_id=str(uuid.uuid4()), user_id=99171, title="excluded", city="深圳", district="南山", status="hidden" if i%2 else "active", expires_at=now-timedelta(days=1), created_at=now+timedelta(seconds=i)))
        for name,lid in ids.items():
            extra={"_hours":[{"start":"09:00","end":"18:00"}]}
            if name in ("rest","missing","custom"): extra={"_lasting_rest":"1"}
            if name=="daily": extra.update(_open_date="2026-11-11",_open_today="0")
            if name=="wait": extra={"_hours":[{"start":"18:00","end":"23:00"}]}
            if name=="closed": extra={"_hours":[{"start":"00:00","end":"01:00"}]}
            if name=="unset": extra={}
            await extras_store.save_extras(lid,extra)
        app=FastAPI();app.include_router(rh.router);app.dependency_overrides[get_current_user_id]=lambda:99171
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url="http://test") as c:
            async def get(**kw):
                r=await c.get("/api/home",params={"city":"深圳","map_results":1,"service":"all",**kw});assert r.status_code==200,r.text;return r.json()
            all_data=await get()
            public={"open","rest","daily","wait","closed","unset","missing","custom"}
            assert {x["lamp_id"] for x in all_data["items"]}=={ids[n] for n in public}
            assert all_data["map_scanned_count"]==8 and not all_data["map_truncated"]
            assert "approx_lat" not in str(all_data) and "22.123456" not in str(all_data) and "_lasting_rest" not in str(all_data)
            assert {x["open_code"] for x in all_data["items"]}=={"open","rest","wait","closed","unset"}
            assert {x["lamp_id"] for x in (await get(service="rest"))["items"]}=={ids[n] for n in ("rest","daily","missing","custom")}
            assert [x["lamp_id"] for x in (await get(service="open",district="南山",price="600-1000",tags="温柔"))["items"]]==[ids["open"]]
            assert [x["lamp_id"] for x in (await get(service="rest",district="__missing__"))["items"]]==[ids["missing"]]
            custom=await get(service="rest",district="特殊片区");assert len(custom["items"])==1 and all(g.get("lat") is None for g in custom["map_groups"])
            r=await c.get("/api/home",params={"city":"深圳","service":"rest","district":"南山"});assert {x["lamp_id"] for x in r.json()["items"]}=={ids["rest"],ids["daily"]}
            for params,code in [({"city":"未开放城市","map_results":1},400),({"city":"深圳","district":"天河","map_results":1},400),({"city":"深圳","service":"closed"},422),({"city":"深圳","service":"rest","open":1},400)]:
                assert (await c.get("/api/home",params=params)).status_code==code
            for name in ("hidden","unlisted","pending","expired"):
                assert (await c.get("/api/lamps/"+ids[name])).status_code==404
            assert (await c.get("/api/lamps/"+ids["rest"])).status_code==200
        await db.close_db()
    asyncio.run(run())


def test_map_service_computation_keeps_honest_600_candidate_cap(monkeypatch):
    async def run():
        from bot import db
        from bot.db import session_scope
        from bot.models import Lamp, User
        from bot.services import search_service, extras_store
        from bot.services.feed_filters import Filter
        from bot.api.routes_core_home import _prefill_extras, _with_shop
        db._engine=None;await db.connect_db();await db._ensure_columns(db._engine)
        now=datetime.utcnow();rest=str(uuid.uuid4())
        async with session_scope() as s:
            s.add(User(user_id=99172,role="teacher"))
            s.add(Lamp(lamp_id=rest,user_id=99172,title="older rest",city="深圳",district="南山",status="active",tags=["稀有"],expires_at=now+timedelta(days=3),created_at=now-timedelta(days=1)))
            for i in range(605):
                s.add(Lamp(lamp_id=str(uuid.uuid4()),user_id=99172,title="newer",city="深圳",district="南山",status="active",expires_at=now+timedelta(days=3),created_at=now+timedelta(seconds=i)))
        await extras_store.save_extras(rest,{"_lasting_rest":"1"})
        f=Filter(service="rest",public_only=True)
        rows=await search_service.search_lamps(city="深圳",district="南山",limit=600,flt=f)
        assert len(rows)==600 and all(x["lamp_id"]!=rest for x in rows)
        # Compatible city/region/tag predicates are SQL-side, before the bounded scan.
        f=Filter(service="rest",tags="稀有",public_only=True)
        rows=await search_service.search_lamps(city="深圳",district="南山",limit=600,flt=f)
        await _prefill_extras(rows)
        assert [x["lamp_id"] for x in rows if f.match_python_side(await _with_shop(x))]==[rest]
        await db.close_db()
    asyncio.run(run())
