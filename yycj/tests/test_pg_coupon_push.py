"""优惠推送到频道（文字帖 + 文字链接，结束时改「已结束」）、券码有效期 = 优惠自身结束时间、约课带券码（YYCJ_TEST_PG）。"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


class Msg:
    def __init__(self, mid):
        self.message_id = mid


class FakeBot:
    def __init__(self):
        self.calls, self.n = [], 700

    async def send_message(self, chat, text, reply_markup=None, **kw):
        self.n += 1
        self.calls.append(("send", chat, text, self.n, reply_markup))
        return Msg(self.n)

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None, **kw):
        self.calls.append(("edit", chat_id, text, message_id, reply_markup))
        return True


def test_coupon_push_and_codes():
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
        from bot.services import bot_info, coupon_push, coupons, home_service
        from sqlalchemy import delete, select
        db._engine = None
        await db.connect_db()
        await db._ensure_columns(db._engine)
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        T1, T2, G1, G2 = 9851, 9852, 9861, 9862
        l1, l2 = str(uuid.uuid4()), str(uuid.uuid4())
        async with session_scope() as s:
            for uid, role in ((T1, "teacher"), (T2, "teacher"), (G1, "guest"), (G2, "guest")):
                s.add(User(user_id=uid, role=role))
            s.add(Lamp(lamp_id=l1, user_id=T1, title="<b>杭一</b>", city="杭州", district="西湖", price_text="900", status="active"))
            s.add(Lamp(lamp_id=l2, user_id=T2, title="杭二", city="杭州", district="上城", price_text="900", status="active"))
        prev = await home_service.get_or_create_settings()
        prev_cities = list(prev.get("enabled_cities") or [])
        await home_service.update_settings(enabled_cities=list(dict.fromkeys(prev_cities + ["杭州"])),
                                           ops_config={"broadcast_channel": "@hz_push", "city_overrides": {}})
        bot_info._cache.update({"username": "MoonTestBot", "ts": 9e12})
        fake, real = FakeBot(), m.bot
        m.bot = fake
        m._ready = True
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
                post = lambda uid, path, js=None: c.post(path, json=js or {}, headers=tok(uid))
                # default ON → text post on the city's push channel, escaped, text links, no buttons
                r = await post(T1, "/api/me/coupon", {"text": "P100 <PP200> & 茶", "days": 2, "quota": 2})
                assert r.status_code == 200, r.text
                sends = [x for x in fake.calls if x[0] == "send"]
                assert len(sends) == 1 and sends[0][1] == "@hz_push" and sends[0][4] is None
                txt = sends[0][2]
                assert "&lt;b&gt;杭一&lt;/b&gt;" in txt and "P100 &lt;PP200&gt; &amp; 茶" in txt and "限2张" in txt
                lid = l1.replace("-", "")
                assert f"?start=s_{lid}" in txt and f"?start=q_{lid}" in txt and "查看详情" in txt and "领取优惠" in txt
                cid = r.json()["current"]["coupon_id"]
                # claim to the quota → post edited to 已领完 · 已结束, links removed
                a = (await post(G1, f"/api/coupons/{cid}/claim")).json()["coupon"]
                fake.calls.clear()
                await post(G2, f"/api/coupons/{cid}/claim")
                edits = [x for x in fake.calls if x[0] == "edit"]
                assert len(edits) == 1 and "已结束" in edits[0][2] and "start=" not in edits[0][2] and edits[0][4] is None
                # 约课 (想聊聊) with the code: the teacher's notice carries it
                fake.calls.clear()
                r = await post(G1, "/api/sessions/request", {"lamp_id": l1})
                assert r.status_code == 200, r.text
                assert r.json()["coupon_code"] == a["my_code"]
                note = [x for x in fake.calls if x[0] == "send" and x[1] == T1][0][2]
                assert a["my_code"] in note and "P100 &lt;PP200&gt;" in note
                # teacher ends early: no more claims, but the claimed code stays valid until the coupon's end time
                await post(T1, "/api/me/coupon/end")
                d = (await c.get(f"/api/lamps/{l1}/coupon", headers=tok(G1))).json()["coupon"]
                assert d and d["my_code"] == a["my_code"] and d["my_valid"] is True
                hit = (await c.get("/api/me/coupon/code?code=" + a["my_code"].lower(), headers=tok(T1))).json()["claim"]
                assert hit["valid"] and not hit["used"]
                assert (await c.get("/api/me/coupon/code?code=" + a["my_code"], headers=tok(T2))).status_code == 404
                assert (await post(T1, "/api/me/coupon/use", {"code": a["my_code"]})).status_code == 200
                mine = (await c.get("/api/me/claims", headers=tok(G1))).json()["items"][0]
                assert mine["my_used"] and mine["my_valid"] is False
                # expiry: past end time → invalid; the 10-min job closes the post
                r = await post(T2, "/api/me/coupon", {"text": "立减50", "days": 1})
                c2 = r.json()["current"]["coupon_id"]
                b = (await post(G2, f"/api/coupons/{c2}/claim")).json()["coupon"]
                async with session_scope() as s:
                    x = (await s.execute(select(Coupon).where(Coupon.coupon_id == c2))).scalar_one()
                    x.ends_at = datetime.utcnow() - timedelta(minutes=1)
                fake.calls.clear()
                assert await coupon_push.job_close_due() == 1
                assert fake.calls and "已结束" in fake.calls[0][2]
                assert await coupon_push.job_close_due() == 0   # idempotent
                assert (await coupons.held_code(G2, T2)) is None
                mine = (await c.get("/api/me/claims", headers=tok(G2))).json()["items"]
                assert [x["my_valid"] for x in mine if x["coupon_id"] == c2] == [False]
                # city switch off → no post
                await home_service.update_city_overrides("杭州", {"broadcast_coupons": False})
                fake.calls.clear()
                await post(T2, "/api/me/coupon", {"text": "再来一张", "days": 1})
                assert not [x for x in fake.calls if x[0] == "send"]
                # own channel per city (rights checked on save)
                await home_service.update_city_overrides("杭州", {"broadcast_coupons": None})
        finally:
            m.bot = real
            async with session_scope() as s:
                ids = [x.coupon_id for x in (await s.execute(select(Coupon).where(Coupon.user_id.in_([T1, T2])))).scalars()]
                await s.execute(delete(CouponClaim).where(CouponClaim.coupon_id.in_(ids)))
                await s.execute(delete(Coupon).where(Coupon.coupon_id.in_(ids)))
                await s.execute(delete(Lamp).where(Lamp.lamp_id.in_([l1, l2])))
                from bot.models import Session as Sess
                await s.execute(delete(Sess).where(Sess.lamp_id.in_([l1, l2])))
            await home_service.update_settings(enabled_cities=prev_cities, ops_config={"broadcast_channel": "", "city_overrides": {}})
            await db.close_db()

    asyncio.run(run())
