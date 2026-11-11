"""优惠券：老师发 / 提前结束 / 限量 / 客人领取码 / 首页「惠」与「有优惠」筛选 / 后台按城市停用（YYCJ_TEST_PG）。"""
import asyncio
import os
import uuid

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_coupons():
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
        from bot.services import admin_acl, home_service
        from sqlalchemy import delete, select
        db._engine = None
        await db.connect_db()
        await db._ensure_columns(db._engine)
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        T1, T2, T3, G1, G2, G3, OP = 9801, 9802, 9803, 9811, 9812, 9813, 9890
        l1, l2, l3 = (str(uuid.uuid4()) for _ in range(3))
        async with session_scope() as s:
            for uid, role in ((T1, "teacher"), (T2, "teacher"), (T3, "teacher"), (G1, "guest"), (G2, "guest"), (G3, "guest"), (OP, "guest")):
                s.add(User(user_id=uid, role=role, guest_alias="小客" if uid == G1 else None))
            s.add(Lamp(lamp_id=l1, user_id=T1, title="上上", city="上海", district="静安", price_text="900", status="active"))
            s.add(Lamp(lamp_id=l2, user_id=T2, title="下下", city="上海", district="徐汇", price_text="900", status="active"))
            s.add(Lamp(lamp_id=l3, user_id=T3, title="北北", city="北京", district="朝阳", price_text="900", status="pending"))
        prev = await home_service.get_or_create_settings()
        prev_cities, prev_ops = list(prev.get("enabled_cities") or []), dict(prev.get("operator_cities") or {})
        await home_service.update_settings(enabled_cities=list(dict.fromkeys(prev_cities + ["上海", "北京"])),
                                           ops_config={"operator_cities": {str(OP): ["北京"]}})
        prev_admins = list(await admin_acl.warm())
        await admin_acl.save(prev_admins + [OP])
        m._ready = True
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
                post = lambda uid, path, js=None: c.post(path, json=js or {}, headers=tok(uid))
                # validation
                assert (await post(T1, "/api/me/coupon", {"text": "", "days": 2})).status_code == 400
                assert (await post(T1, "/api/me/coupon", {"text": "P100", "days": 5})).status_code == 400
                assert (await post(T1, "/api/me/coupon", {"text": "加我 @abc", "days": 2})).status_code == 400
                assert (await post(T1, "/api/me/coupon", {"text": "x" * 41, "days": 2})).status_code == 400
                assert (await post(T1, "/api/me/coupon", {"text": "P100", "days": 2, "quota": 999})).status_code == 400
                assert (await post(G1, "/api/me/coupon", {"text": "P100", "days": 2})).status_code == 403
                r = await post(T3, "/api/me/coupon", {"text": "P100", "days": 2})
                assert r.status_code == 400 and "上架后" in r.json()["detail"]
                # issue
                r = await post(T1, "/api/me/coupon", {"text": "P100  PP200", "days": 2, "quota": 2})
                assert r.status_code == 200, r.text
                cur = r.json()["current"]
                assert cur["text"] == "P100 PP200" and cur["quota"] == 2 and cur["remain"] == 2 and (cur["left"].startswith("还剩1天") or cur["left"] == "还剩2天")
                # home: 惠 data on the card, 有优惠 filter
                h = await c.get("/api/home?city=上海&limit=20", headers=tok(G1))
                by = {x["title"]: x for x in h.json()["items"]}
                assert by["上上"]["coupon"]["text"] == "P100 PP200" and "coupon" not in by["下下"]
                assert h.json()["coupon_display"] == "badge,filter,list"
                h = await c.get("/api/home?city=上海&limit=20&coupon=1", headers=tok(G1))
                assert [x["title"] for x in h.json()["items"]] == ["上上"] and h.json()["filters"]["active"] == 1
                lst = (await c.get("/api/coupons?city=上海", headers=tok(G1))).json()["items"]
                assert [x["title"] for x in lst] == ["上上"]
                # claim: one per guest, code, quota
                cid = cur["coupon_id"]
                assert (await post(T1, f"/api/coupons/{cid}/claim")).status_code == 400   # own
                a = (await post(G1, f"/api/coupons/{cid}/claim")).json()["coupon"]
                again = (await post(G1, f"/api/coupons/{cid}/claim")).json()["coupon"]
                assert len(a["my_code"]) == 6 and again["my_code"] == a["my_code"] and again["claimed"] == 1
                b = (await post(G2, f"/api/coupons/{cid}/claim")).json()["coupon"]
                assert b["remain"] == 0 and b["live"] is False
                r = await post(G3, f"/api/coupons/{cid}/claim")
                assert r.status_code == 400 and "领完" in r.json()["detail"]
                d = (await c.get(f"/api/lamps/{l1}/coupon", headers=tok(G1))).json()["coupon"]
                assert d["my_code"] == a["my_code"] and d["status"] == "sold_out"
                # sold out → no 惠 on the card
                h = await c.get("/api/home?city=上海&limit=20", headers=tok(G3))
                assert all("coupon" not in x for x in h.json()["items"])
                mine = (await c.get("/api/me/claims", headers=tok(G1))).json()["items"]
                assert mine[0]["title"] == "上上" and mine[0]["my_code"] == a["my_code"]
                # teacher sees who claimed, marks used
                t = (await c.get("/api/me/coupon", headers=tok(T1))).json()
                assert {x["who"] for x in t["claims"]} == {"小客", "客人"} and t["can_issue"]
                t = (await post(T1, "/api/me/coupon/use", {"code": a["my_code"].lower()})).json()
                assert [x["used"] for x in t["claims"] if x["code"] == a["my_code"]] == [True]
                assert (await post(T2, "/api/me/coupon/use", {"code": a["my_code"]})).status_code == 404
                # new coupon replaces the old one; end early
                await post(T1, "/api/me/coupon", {"text": "新人立减50", "days": 1})
                async with session_scope() as s:
                    sts = sorted(x.status for x in (await s.execute(select(Coupon).where(Coupon.user_id == T1))).scalars())
                assert sts == ["active", "ended"]
                t = (await post(T1, "/api/me/coupon/end")).json()
                assert t["current"] is None
                # admin: per city list + stop; scoped operator can't touch 上海
                await post(T2, "/api/me/coupon", {"text": "PP 立减", "days": 3})
                adm = (await c.get("/api/admin/coupons?city=上海", headers=tok(1))).json()["items"]
                assert [x["title"] for x in adm] == ["下下"]
                assert (await c.get("/api/admin/coupons?city=上海", headers=tok(OP))).status_code == 403
                assert (await c.get("/api/admin/coupons", headers=tok(OP))).json()["items"] == []
                assert (await post(OP, f"/api/admin/coupons/{adm[0]['coupon_id']}/stop")).status_code == 403
                assert (await post(1, f"/api/admin/coupons/{adm[0]['coupon_id']}/stop")).status_code == 200
                assert (await c.get("/api/lamps/" + l2 + "/coupon", headers=tok(G1))).json()["coupon"] is None
                allc = (await c.get("/api/admin/coupons?city=上海&status=all", headers=tok(1))).json()["items"]
                assert any(x["status"] == "stopped" and x["stopped_by"] == 1 for x in allc)
        finally:
            async with session_scope() as s:
                ids = [x.coupon_id for x in (await s.execute(select(Coupon).where(Coupon.user_id.in_([T1, T2, T3])))).scalars()]
                await s.execute(delete(CouponClaim).where(CouponClaim.coupon_id.in_(ids)))
                await s.execute(delete(Coupon).where(Coupon.coupon_id.in_(ids)))
                await s.execute(delete(Lamp).where(Lamp.lamp_id.in_([l1, l2, l3])))
            await admin_acl.save(prev_admins)
            await home_service.update_settings(enabled_cities=prev_cities, ops_config={"operator_cities": prev_ops})
            await db.close_db()

    asyncio.run(run())
