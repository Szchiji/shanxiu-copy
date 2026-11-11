"""营业时间 / 开课提醒 端到端（YYCJ_TEST_PG）。"""
import asyncio
import os
from datetime import datetime, timedelta

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, uid, text, reply_markup=None):
        self.sent.append((uid, text, reply_markup))


def test_hours_flow():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import Lamp, Post, User
        from bot.services import open_shift as o
        from bot.services.extras_store import load_extras, save_extras
        from sqlalchemy import select
        await db.connect_db()
        await db._ensure_columns(db._engine)
        tok = {"Authorization": "Bearer " + issue_access_token(9101, get_settings().bot_token)}
        async with session_scope() as s:
            s.add(User(user_id=9101, role="teacher"))
            s.add(Lamp(lamp_id="H1", user_id=9101, title="小雅", city="深圳", status="active",
                       expires_at=datetime.utcnow() + timedelta(days=5)))
        m._ready = True
        t = httpx.ASGITransport(app=m.app)
        async with httpx.AsyncClient(transport=t, base_url="http://t") as c:
            today = o.today_str()
            # client-sent "_" keys are ignored; hours parsed leniently and stored canonical
            r = await c.post("/api/me/listings/H1/edit", headers=tok, json={
                "operation_id":"hours-save-first-0001", "city": "深圳", "title": "小雅", "hours": "10：00~22：00",
                "extras": {"_open_today": "1", "_open_date": today, "_auto_open": "1", "微信": "w"}})
            assert r.status_code == 200, r.text
            ex = await load_extras("H1")
            assert ex.get("_hours") == [{"start": "10:00", "end": "22:00"}], ex
            assert "_open_today" not in ex and "_auto_open" not in ex and ex.get("微信") == "w", ex

            await save_extras("H1", {"微信": "w", "_bc_mid": "77"})
            r = await c.get("/api/lamps/H1", headers=tok)
            lp = r.json()["lamp"]
            assert lp["hours"] == [{"start": "10:00", "end": "22:00"}] and lp["hours_text"] == "10:00–22:00"
            assert not any(k.startswith("_") for k in lp["extras"]), lp["extras"]

            r = await c.get("/api/me/listings", headers=tok)
            it = r.json()["items"][0]
            assert it["hours"] == [{"start": "10:00", "end": "22:00"}]
            assert not any(k.startswith("_") for k in it["extras"]), it["extras"]
            assert it["shop"]["remind_on"] is True and it["shop"]["auto"] is True   # defaults

            # publish: "_" keys dropped, hours canonical in the pending post
            r = await c.post("/api/posts", headers=tok, json={
                "city": "深圳", "title": "新老师", "description": "x", "hours": "20-4",
                "extras": {"_open_today": "1", "联系": "c"}, "media": [{"type": "image", "url": "https://example.com/a.jpg"}]})
            assert r.status_code == 200, r.text
            async with session_scope() as s:
                post = (await s.execute(select(Post).where(Post.post_id == r.json()["post_id"]))).scalar_one()
            assert post.lamp_data["hours"] == [{"start": "20:00", "end": "04:00"}]
            assert "_open_today" not in post.lamp_data["extras"]

            # shift endpoint: rest is today-only, reminders stay on; switches are explicit
            for action, key, want in [("rest", "_open_today", "0"), ("remind_off", "_remind_off", "1"),
                                      ("remind_on", "_remind_off", "0"), ("auto_on", "_auto_open", "1"),
                                      ("auto_off", "_auto_open", "0")]:
                r = await c.post("/api/me/listings/H1/shift", headers=tok, json={"action": action})
                assert r.status_code == 200, r.text
                ex = await load_extras("H1")
                assert ex.get(key) == want, (action, ex)
                if action == "rest":
                    assert ex.get("_remind_off") != "1"
            r = await c.post("/api/me/listings/H1/shift", headers=tok, json={"action": "nope"})
            assert r.status_code == 400

            # clearing hours removes _hours
            r = await c.post("/api/me/listings/H1/edit", headers=tok, json={"operation_id":"hours-clear-second-0002", "city": "深圳", "title": "小雅", "hours": []})
            assert r.status_code == 200 and "_hours" not in await load_extras("H1")

        # scheduler: night shift, prompt 1h before start, once
        await save_extras("H1", {"_hours": [{"start": "20:00", "end": "04:00"}], "_auto_open": "0"}, drop=("_open_date", "_open_today"))
        d = datetime.now(o.TZ).date()
        at = lambda days, hh, mm=0: datetime(d.year, d.month, d.day, hh, mm, tzinfo=o.TZ) + timedelta(days=days)
        fb = FakeBot()
        assert await o.send_due(now=at(0, 19, 20), bot=fb) == 0
        assert await o.send_due(now=at(0, 19, 35), bot=fb) == 1
        assert await o.send_due(now=at(0, 19, 40), bot=fb) == 0
        uid, text, kb = fb.sent[0]
        assert uid == 9101 and "今天 20:00 开课吗" in text and "20:00–次日04:00" in text
        assert kb.inline_keyboard[0][0].callback_data == f"shift:open:H1:{d.isoformat()}"
        # no reply → today only counts as rest, reminders stay on
        assert await o.expire_unanswered(now=at(0, 22, 30)) == 0      # < 3h after prompt
        assert await o.expire_unanswered(now=at(0, 22, 36)) == 1
        ex = await load_extras("H1")
        assert ex["_open_today"] == "0" and ex.get("_remind_off") != "1" and ex["_remind_missed"] == d.isoformat()
        assert o.shop_status(ex, "active", None, now=at(0, 23))["code"] == "rest"
        # late tap still works during the shift; shift survives midnight
        msg = await o.apply_choice("H1", 9101, "open", d.isoformat(), now=at(0, 23))
        assert "开课" in msg, msg
        ex = await load_extras("H1")
        assert "_remind_missed" not in ex
        assert o.shop_status(ex, "active", None, now=at(1, 1, 30))["code"] == "open"
        # tomorrow's prompt still comes
        assert await o.send_due(now=at(1, 19, 35), bot=fb) == 1
        # legacy: old logic wrote _remind_on="0" → stays off
        async with session_scope() as s:
            s.add(Lamp(lamp_id="H2", user_id=9101, title="旧", city="深圳", status="active",
                       expires_at=datetime.utcnow() + timedelta(days=5)))
        await save_extras("H2", {"_hours": "20-4", "_remind_on": "0", "_auto_open": "0"})
        fb2 = FakeBot()
        assert await o.send_due(now=at(2, 19, 35), bot=fb2) == 1 and fb2.sent[0][2].inline_keyboard[0][0].callback_data.split(":")[2] == "H1"
        # stale prompt
        assert "过期" in await o.apply_choice("H1", 9101, "open", (d - timedelta(days=2)).isoformat(), now=at(1, 19, 6))
        assert "不是你的" in await o.apply_choice("H1", 1, "open", now=at(1, 19, 6))
        await db.close_db()

    asyncio.run(run())
