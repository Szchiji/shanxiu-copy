"""一次发多项优惠：整批共用有效期 / 每项数量 / 每批每人限领 1 张（并发也只成功 1 张）/ 频道一帖多项（某项领完改帖）/ 约课带全部券码 /
后台一批一行、整批停用 / 往期按项 / 旧数据迁移成一项一批（YYCJ_TEST_PG）。"""
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
        self.calls, self.n = [], 900

    async def send_message(self, chat, text, reply_markup=None, **kw):
        self.n += 1
        self.calls.append(("send", chat, text, self.n))
        return Msg(self.n)

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None, **kw):
        self.calls.append(("edit", chat_id, text, message_id))
        return True


def test_coupon_batches():
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
        from sqlalchemy import delete, select, text as sql
        db._engine = None
        await db.connect_db()
        await db._ensure_columns(db._engine)
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        T, T2, G1, G2, G3 = 9901, 9902, 9911, 9912, 9913
        l1, l2 = str(uuid.uuid4()), str(uuid.uuid4())
        async with session_scope() as s:
            for uid, role in ((T, "teacher"), (T2, "teacher"), (G1, "guest"), (G2, "guest"), (G3, "guest")):
                s.add(User(user_id=uid, role=role, guest_alias="小一" if uid == G1 else None))
            s.add(Lamp(lamp_id=l1, user_id=T, title="多多", city="苏州", district="姑苏", price_text="900", status="active"))
            s.add(Lamp(lamp_id=l2, user_id=T2, title="旧旧", city="苏州", district="吴中", price_text="900", status="active"))
            # a coupon written before batches existed (batch_id NULL) → migration makes it a one-item batch
            s.add(Coupon(coupon_id="LEGACY-9902", user_id=T2, lamp_id=l2, city="苏州", text="老优惠", quota=0, claimed=0,
                         status="active", ends_at=datetime.utcnow() + timedelta(days=1)))
            s.add(CouponClaim(coupon_id="LEGACY-9902", user_id=G3, code="LEG001"))
        async with db._engine.begin() as conn:
            await conn.execute(sql("UPDATE coupons SET batch_id = NULL WHERE coupon_id = 'LEGACY-9902'"))
            await conn.execute(sql("UPDATE coupon_claims SET batch_id = NULL WHERE coupon_id = 'LEGACY-9902'"))
        await db._ensure_columns(db._engine)
        async with session_scope() as s:
            assert (await s.execute(select(Coupon.batch_id).where(Coupon.coupon_id == "LEGACY-9902"))).scalar_one() == "LEGACY-9902"
            leg = (await s.execute(select(CouponClaim).where(CouponClaim.coupon_id == "LEGACY-9902"))).scalar_one()
            assert leg.batch_id == "LEGACY-9902" and leg.code == "LEG001" and leg.user_id == G3   # existing claim intact
        prev = await home_service.get_or_create_settings()
        prev_cities = list(prev.get("enabled_cities") or [])
        await home_service.update_settings(enabled_cities=list(dict.fromkeys(prev_cities + ["苏州"])),
                                           ops_config={"broadcast_channel": "@sz_push", "city_overrides": {}})
        bot_info._cache.update({"username": "MoonTestBot", "ts": 9e12})
        fake, real = FakeBot(), m.bot
        m.bot = fake
        m._ready = True
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
                post = lambda uid, path, js=None: c.post(path, json=js or {}, headers=tok(uid))
                new = lambda items, days=2: post(T, "/api/me/coupon", {"days": days, "items": items})
                # validation: 1–5 items, 数量 required 1–200, no links, no duplicates
                assert (await new([])).status_code == 400
                assert (await new([{"text": f"x{i}", "quota": 1} for i in range(6)])).status_code == 400
                r = await new([{"text": "100P", "quota": 2}, {"text": "200PP", "quota": 0}])
                assert r.status_code == 400 and "第 2 项" in r.json()["detail"] and "数量" in r.json()["detail"]
                assert (await new([{"text": "100P", "quota": 201}])).status_code == 400
                assert (await new([{"text": "100P", "quota": 2}, {"text": "加 @me", "quota": 1}])).status_code == 400
                assert (await new([{"text": "100P", "quota": 2}, {"text": "100p", "quota": 1}])).status_code == 400
                assert (await new([{"text": "100P", "quota": 2}], days=5)).status_code == 400
                # publish 100P×2 / 200PP×3 / 500夜课×4 → one batch, one channel post listing all three
                r = await new([{"text": "100P", "quota": 2}, {"text": "200PP", "quota": 3}, {"text": "500夜课", "quota": 4}], days=3)
                assert r.status_code == 200, r.text
                cur = r.json()["current"]
                assert [x["text"] for x in cur["items"]] == ["100P", "200PP", "500夜课"] and cur["n_items"] == 3
                assert cur["quota"] == 9 and cur["remain"] == 9 and cur["text"] == "100P / 200PP / 500夜课"
                assert len({x["batch_id"] for x in cur["items"]}) == 1 and len({x["ends_at"] for x in cur["items"]}) == 1
                sends = [x for x in fake.calls if x[0] == "send"]
                assert len(sends) == 1 and sends[0][1] == "@sz_push"
                assert all(t in sends[0][2] for t in ("100P · 限2张", "200PP · 限3张", "500夜课 · 限4张")) and "start=q_" in sends[0][2]
                i1, i2, i3 = (x["coupon_id"] for x in cur["items"])
                # home card 惠 + 本城优惠: one entry per batch
                h = await c.get("/api/home?city=苏州&limit=20", headers=tok(G1))
                assert {x["title"]: x.get("coupon", {}).get("text") for x in h.json()["items"]}["多多"] == "100P / 200PP / 500夜课"
                lst = (await c.get("/api/coupons?city=苏州", headers=tok(G1))).json()["items"]
                assert [x["title"] for x in lst].count("多多") == 1
                # guest: ONE claim per batch (pick one item)
                a1 = (await post(G1, f"/api/coupons/{i1}/claim")).json()["coupon"]
                r = await post(G1, f"/api/coupons/{i2}/claim")
                assert r.status_code == 400 and "限领 1 张" in r.json()["detail"]
                again = (await post(G1, f"/api/coupons/{i1}/claim")).json()["coupon"]
                codes = {x["coupon_id"]: x["code"] for x in a1["my_codes"]}
                assert set(codes) == {i1} and len(codes[i1]) == 6
                assert again["claimed_code"] == codes[i1] and again["claimed"] == 1
                d = (await c.get(f"/api/lamps/{l1}/coupon", headers=tok(G1))).json()["coupon"]
                st = {x["text"]: (x.get("my_code"), x["remain"], x["live"]) for x in d["items"]}
                assert st["100P"] == (codes[i1], 1, True) and st["200PP"] == (None, 3, True) and st["500夜课"] == (None, 4, True)
                # 100P runs out → post edited: that item 已领完, the rest still open (links kept)
                fake.calls.clear()
                await post(G2, f"/api/coupons/{i1}/claim")
                edits = [x for x in fake.calls if x[0] == "edit"]
                assert len(edits) == 1 and "<s>100P</s> · 已领完" in edits[0][2] and "200PP · 限3张" in edits[0][2] and "start=q_" in edits[0][2]
                r = await post(G3, f"/api/coupons/{i1}/claim")
                assert r.status_code == 400 and "领完" in r.json()["detail"]
                assert (await post(G3, f"/api/coupons/{i3}/claim")).status_code == 200    # other items still open
                # 想聊聊: the teacher's notice carries the guest's single code for this batch
                fake.calls.clear()
                r = await post(G1, "/api/sessions/request", {"lamp_id": l1})
                assert r.status_code == 200, r.text
                assert r.json()["coupon_codes"] == [codes[i1]] and r.json()["coupon_code"] == codes[i1]
                note = [x for x in fake.calls if x[0] == "send" and x[1] == T][0][2]
                assert codes[i1] in note and "100P" in note
                # teacher: per-item claims, summaries across items, 核销 one code
                t = (await c.get("/api/me/coupon", headers=tok(T))).json()
                assert t["current"]["claimed"] == 3 and t["current"]["pending"] == 3 and t["max_items"] == 5
                per = {x["text"]: (x["claimed"], x["pending"]) for x in t["current"]["items"]}
                assert per == {"100P": (2, 2), "200PP": (0, 0), "500夜课": (1, 1)}
                assert {x["item"] for x in t["claims"] if x["code"] == codes[i1]} == {"100P"}
                hit = (await c.get("/api/me/coupon/code?code=" + codes[i1], headers=tok(T))).json()["claim"]
                assert hit["text"] == "100P" and hit["valid"]
                t = (await post(T, "/api/me/coupon/use", {"code": codes[i1]})).json()
                assert {x["text"]: x["used"] for x in t["current"]["items"]}["100P"] == 1 and t["current"]["used"] == 1
                mine = (await c.get("/api/me/claims", headers=tok(G1))).json()
                assert [x["text"] for x in mine["items"]] == ["100P"] and mine["usable"] == 0
                # admin: one row per batch with its items; 停用 stops the whole batch
                adm = (await c.get("/api/admin/coupons?city=苏州", headers=tok(1))).json()["items"]
                row = [x for x in adm if x["title"] == "多多"][0]
                assert [i["text"] for i in row["items"]] == ["100P", "200PP", "500夜课"] and row["claimed"] == 3
                assert [x["text"] for x in adm if x["title"] == "旧旧"] == ["老优惠"]    # migrated legacy = one-item batch
                # end early → whole batch 已结束 on the channel, history per item
                fake.calls.clear()
                t = (await post(T, "/api/me/coupon/end")).json()
                assert t["current"] is None
                edits = [x for x in fake.calls if x[0] == "edit"]
                assert len(edits) == 1 and "已结束" in edits[0][2] and "start=" not in edits[0][2]
                hb = t["history"][0]
                assert [i["text"] for i in hb["items"]] == ["100P", "200PP", "500夜课"] and hb["claimed"] == 3 and hb["used"] == 1
                assert {i["text"]: i["pending"] for i in hb["items"]} == {"100P": 1, "200PP": 0, "500夜课": 1}
                # claimed codes stay valid until the batch end time (ended early)
                d = (await c.get(f"/api/lamps/{l1}/coupon", headers=tok(G2))).json()["coupon"]
                assert d and {x["text"]: x.get("my_valid") for x in d["items"]}["100P"] is True and not d["live"]
                # a new batch replaces; admin stop hits every item
                r = await new([{"text": "A套餐", "quota": 1}, {"text": "B套餐", "quota": 1}])
                nb = r.json()["current"]
                # race: the same guest grabs both items at once → exactly one succeeds
                rs = await asyncio.gather(*(post(G3, f"/api/coupons/{x['coupon_id']}/claim") for x in nb["items"]))
                assert sorted(x.status_code for x in rs) == [200, 400]
                async with session_scope() as s:
                    n = len((await s.execute(select(CouponClaim).where(CouponClaim.batch_id == nb["batch_id"], CouponClaim.user_id == G3))).scalars().all())
                assert n == 1
                assert (await post(1, f"/api/admin/coupons/{nb['items'][1]['coupon_id']}/stop")).status_code == 200
                async with session_scope() as s:
                    sts = {x.status for x in (await s.execute(select(Coupon).where(Coupon.batch_id == nb["batch_id"]))).scalars()}
                assert sts == {"stopped"}
                # all items 领完 → 已领完 · 已结束 (job also closes on its own when every item is out)
                r = await new([{"text": "单张", "quota": 1}, {"text": "双张", "quota": 1}])
                x1, x2 = (x["coupon_id"] for x in r.json()["current"]["items"])
                await post(G1, f"/api/coupons/{x1}/claim")
                fake.calls.clear()
                await post(G2, f"/api/coupons/{x2}/claim")
                edits = [x for x in fake.calls if x[0] == "edit"]
                assert len(edits) == 1 and "已领完 · 已结束" in edits[0][2]
                assert await coupon_push.job_close_due() == 0
        finally:
            m.bot = real
            async with session_scope() as s:
                ids = [x.coupon_id for x in (await s.execute(select(Coupon).where(Coupon.user_id.in_([T, T2])))).scalars()]
                await s.execute(delete(CouponClaim).where(CouponClaim.coupon_id.in_(ids)))
                await s.execute(delete(Coupon).where(Coupon.coupon_id.in_(ids)))
                await s.execute(delete(Lamp).where(Lamp.lamp_id.in_([l1, l2])))
                from bot.models import Session as Sess
                await s.execute(delete(Sess).where(Sess.lamp_id.in_([l1, l2])))
                await s.execute(delete(User).where(User.user_id.in_([T, T2, G1, G2, G3])))
            await home_service.update_settings(enabled_cities=prev_cities, ops_config={"broadcast_channel": "", "city_overrides": {}})
            await db.close_db()

    asyncio.run(run())
