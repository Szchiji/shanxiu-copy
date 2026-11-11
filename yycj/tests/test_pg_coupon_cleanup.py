"""「我的」优惠整理：客人只把可用的放上面，已用 / 已失效折叠且 7 天后不再显示；老师待使用 / 已核销 / 往期优惠（YYCJ_TEST_PG）。"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_coupon_cleanup_lists():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import Lamp, User
        from bot.models_extra import Coupon, CouponClaim
        from bot.services import coupons, home_service
        from sqlalchemy import delete
        db._engine = None
        await db.connect_db()
        await db._ensure_columns(db._engine)
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        T, G, G2 = 9871, 9881, 9882
        lid = str(uuid.uuid4())
        now = datetime.utcnow()
        ids = ["CC-NOW", "CC-OLD", "CC-ANCIENT", "CC-EARLY", "CC-STOP"]
        async with session_scope() as s:
            for uid, role in ((T, "teacher"), (G, "guest"), (G2, "guest")):
                s.add(User(user_id=uid, role=role))
            s.add(Lamp(lamp_id=lid, user_id=T, title="清理", city="杭州", district="西湖", price_text="900", status="active"))
            s.add(Coupon(coupon_id="CC-NOW", user_id=T, lamp_id=lid, city="杭州", text="现在", ends_at=now + timedelta(days=1), status="active", claimed=3))
            s.add(Coupon(coupon_id="CC-OLD", user_id=T, lamp_id=lid, city="杭州", text="上周", ends_at=now - timedelta(days=3), status="ended", claimed=2))
            s.add(Coupon(coupon_id="CC-ANCIENT", user_id=T, lamp_id=lid, city="杭州", text="很久", ends_at=now - timedelta(days=40), status="ended", claimed=1))
            s.add(Coupon(coupon_id="CC-EARLY", user_id=T, lamp_id=lid, city="杭州", text="提前结束", ends_at=now + timedelta(hours=5), status="ended", claimed=1))
            s.add(Coupon(coupon_id="CC-STOP", user_id=T, lamp_id=lid, city="杭州", text="停用", ends_at=now + timedelta(days=2), status="stopped", claimed=1))
            s.add(CouponClaim(coupon_id="CC-NOW", user_id=G, code="NOW001"))                                       # usable
            s.add(CouponClaim(coupon_id="CC-NOW", user_id=G2, code="NOW002", used_at=now - timedelta(hours=1)))   # used today
            s.add(CouponClaim(coupon_id="CC-OLD", user_id=G, code="OLD001", used_at=now - timedelta(days=2)))     # used 2d → shown
            s.add(CouponClaim(coupon_id="CC-OLD", user_id=G2, code="OLD002"))                                      # expired 3d → shown (void)
            s.add(CouponClaim(coupon_id="CC-ANCIENT", user_id=G, code="ANC001", used_at=now - timedelta(days=39)))  # → hidden
            s.add(CouponClaim(coupon_id="CC-EARLY", user_id=G, code="EAR001"))                                     # still usable
            s.add(CouponClaim(coupon_id="CC-STOP", user_id=G, code="STP001"))                                      # void, ends in future → shown
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
                d = (await c.get("/api/me/claims", headers=tok(G))).json()
                st = {x["my_code"]: x["state"] for x in d["items"]}
                assert st == {"NOW001": "usable", "EAR001": "usable", "OLD001": "used", "STP001": "void"}
                assert d["usable"] == 2 and d["done"] == 2 and d["hidden"] == 1 and d["hide_days"] == coupons.DONE_HIDE_DAYS == 7
                # usable first (soonest end first), then done
                assert [x["my_code"] for x in d["items"]][:2] == ["EAR001", "NOW001"]
                assert all(x["state"] != "usable" for x in d["items"][2:])
                d2 = (await c.get("/api/me/claims", headers=tok(G2))).json()
                assert {x["my_code"]: x["state"] for x in d2["items"]} == {"NOW002": "used", "OLD002": "void"} and d2["usable"] == 0
                # teacher: current claims carry state; history = recent ended/stopped, not the 40-day-old one
                t = (await c.get("/api/me/coupon", headers=tok(T))).json()
                assert {x["code"]: x["state"] for x in t["claims"]} == {"NOW001": "usable", "NOW002": "used"}
                hist = {h["coupon_id"]: h for h in t["history"]}
                assert set(hist) == {"CC-OLD", "CC-EARLY", "CC-STOP"} and t["history_days"] == coupons.HISTORY_DAYS
                assert hist["CC-OLD"]["used"] == 1 and hist["CC-OLD"]["claimed"] == 2 and hist["CC-OLD"]["pending"] == 0
                assert hist["CC-EARLY"]["pending"] == 1 and "提前结束" in hist["CC-EARLY"]["status"]
                assert hist["CC-STOP"]["status"] == "已停用"
                assert [x["can_undo"] for x in hist["CC-OLD"]["claims"]] == [False, False]
                # mark the early-ended code used from history, then 撤销 it
                r = await c.post("/api/me/coupon/use", json={"code": "EAR001", "used": True}, headers=tok(T))
                h2 = {h["coupon_id"]: h for h in r.json()["history"]}
                assert h2["CC-EARLY"]["used"] == 1 and h2["CC-EARLY"]["claims"][0]["can_undo"] is True
                r = await c.post("/api/me/coupon/use", json={"code": "EAR001", "used": False}, headers=tok(T))
                assert {h["coupon_id"]: h for h in r.json()["history"]}["CC-EARLY"]["pending"] == 1
        finally:
            async with session_scope() as s:
                await s.execute(delete(CouponClaim).where(CouponClaim.coupon_id.in_(ids)))
                await s.execute(delete(Coupon).where(Coupon.coupon_id.in_(ids)))
                await s.execute(delete(Lamp).where(Lamp.lamp_id == lid))
                await s.execute(delete(User).where(User.user_id.in_([T, G, G2])))
            await db.close_db()

    asyncio.run(run())
