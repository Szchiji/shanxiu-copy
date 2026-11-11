"""评价待审 → 机器人通知管理员 + 按钮/后台同步 + 补发（YYCJ_TEST_PG）。"""
import asyncio
import os
import sys
import types

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


class Msg:
    def __init__(self, mid):
        self.message_id = mid


class FakeBot:
    def __init__(self):
        self.calls = []
        self.n = 100

    async def _rec(self, kind, chat, **kw):
        self.n += 1
        self.calls.append((kind, chat, kw))
        return Msg(self.n)

    async def send_photo(self, chat, photo, caption=None, reply_markup=None):
        return await self._rec("photo", chat, photo=photo, caption=caption, kb=reply_markup)

    async def send_media_group(self, chat, media):
        return await self._rec("album", chat, n=len(media))

    async def send_message(self, chat, text, reply_markup=None, **kw):
        return await self._rec("text", chat, text=text, kb=reply_markup)

    async def edit_message_caption(self, chat_id, message_id, caption, reply_markup=None):
        return await self._rec("edit_caption", chat_id, mid=message_id, text=caption, kb=reply_markup)

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None):
        return await self._rec("edit_text", chat_id, mid=message_id, text=text, kb=reply_markup)


def test_review_notify():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import Lamp, Review, User
        from bot.services import admin_acl, home_service, review_notify
        db._engine = None  # engines from earlier tests are bound to a closed loop
        await db.connect_db()
        await db._ensure_columns(db._engine)
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        SUPER, OP_SZ, OP_ALL, OP_GZ, T = 1, 7801, 7802, 7803, 7810
        async with session_scope() as s:
            s.add(User(user_id=T, role="teacher"))
            for g in (7821, 7822, 7823):
                s.add(User(user_id=g, role="guest", username=f"g{g}"))
            s.add(Lamp(lamp_id="RN-SZ", user_id=T, title="小红", city="深圳", district="南山", status="active"))
            s.add(Review(review_id="RN-OLD", lamp_id="RN-SZ", guest_id=7823, stars=4, text="昨晚的", status="pending"))
        await home_service.update_settings(enabled_cities=["深圳", "广州"], ops_config={"city_overrides": {}, "review_require_audit": False})
        await admin_acl.save([OP_SZ, OP_ALL, OP_GZ])
        await home_service.update_settings(ops_config={"operator_cities": {str(OP_SZ): ["深圳"], str(OP_GZ): ["广州"]}})
        fake = FakeBot()
        m.bot_real = m.bot
        m.bot = fake
        m._ready = True
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
                # no 约课凭证 → rejected with a clear hint, nobody notified
                r = await c.post("/api/reviews", json={"lamp_id": "RN-SZ", "stars": 5, "text": "plain"}, headers=tok(7821))
                assert r.status_code == 400 and "约课凭证" in r.json()["detail"]
                await asyncio.sleep(0.3)
                assert fake.calls == []
                # 1 proof → pending even with audit off → proof photo + text card with buttons to
                # super, 深圳 operator, all-city operator (not 广州)
                r = await c.post("/api/reviews", json={"lamp_id": "RN-SZ", "stars": 5, "text": "测试<b>", "proofs": ["AgACAgUAAxkBAAIBb2Vk1Zq9xQw"], "photos": ["AgACAgUAAxkBAAIBpub9"]}, headers=tok(7822))
                rid = r.json()["review"]["review_id"]
                assert r.status_code == 200 and r.json()["review"]["status"] == "pending"
                await asyncio.sleep(0.5)
                photos = [x for x in fake.calls if x[0] == "photo"]
                assert sorted(x[1] for x in photos) == sorted([SUPER, OP_SZ, OP_ALL]), fake.calls
                assert all("约课凭证" in (x[2]["caption"] or "") and x[2]["kb"] is None for x in photos)
                cards = [x for x in fake.calls if x[0] == "text" and x[2].get("kb") is not None]
                assert sorted(x[1] for x in cards) == sorted([SUPER, OP_SZ, OP_ALL]), fake.calls
                cap = cards[0][2]["text"]
                assert "小红" in cap and "★★★★★" in cap and "测试&lt;b&gt;" in cap and "带凭证" in cap and "@g7822" in cap
                assert "公开晒图：0 张" in cap and "约课凭证：1 张" in cap
                kb = cards[0][2]["kb"].inline_keyboard
                assert [b.callback_data for b in kb[0]] == [f"admin_rev_ok:{rid}", f"admin_rev_no:{rid}"]
                # notify is idempotent
                assert await review_notify.notify_new_review({"review_id": rid, "status": "pending", "lamp_id": "RN-SZ"}) == 0
                # backend bulk approve → every admin copy is edited, buttons removed
                fake.calls.clear()
                r = await c.post("/api/admin/reviews/bulk", json={"ids": [rid], "action": "approve"}, headers=tok(SUPER))
                assert r.json()["done"] == [rid]
                await asyncio.sleep(0.5)
                edits = [x for x in fake.calls if x[0] == "edit_text"]
                assert len(edits) == 3 and all("已通过" in x[2]["text"] and x[2]["kb"] is None for x in edits), fake.calls
                # 3 proofs → album + text card with buttons; then the bot button rejects it
                fake.calls.clear()
                r = await c.post("/api/reviews", json={"lamp_id": "RN-SZ", "stars": 3, "text": "三张", "proofs": ["AgACAgUAAxkBAAIBb2Vk1Zq9xQa", "AgACAgUAAxkBAAIBb2Vk1Zq9xQb", "AgACAgUAAxkBAAIBb2Vk1Zq9xQc"]}, headers=tok(7821))
                rid3 = r.json()["review"]["review_id"]
                await asyncio.sleep(0.5)
                assert len([x for x in fake.calls if x[0] == "album" and x[2]["n"] == 3]) == 3
                assert len([x for x in fake.calls if x[0] == "text" and x[2]["kb"] is not None]) == 3
        finally:
            pass

        from bot.handlers.admin import review_action

        class CB:
            def __init__(self, uid, data):
                self.from_user = types.SimpleNamespace(id=uid)
                self.data = data
                self.answers = []

            async def answer(self, text="", show_alert=False):
                self.answers.append(text)
        fake.calls.clear()
        cb = CB(OP_GZ, f"admin_rev_no:{rid3}")       # 广州 operator: out of scope
        await review_action(cb, fake)
        assert cb.answers and "城市" in cb.answers[0]
        cb = CB(9999, f"admin_rev_no:{rid3}")        # not an admin
        await review_action(cb, fake)
        assert cb.answers == ["无权限"]
        cb = CB(OP_SZ, f"admin_rev_no:{rid3}")
        await review_action(cb, fake)
        assert cb.answers == ["已拒绝"]
        edits = [x for x in fake.calls if x[0] == "edit_text"]
        assert len(edits) == 3 and all("已拒绝" in x[2]["text"] and f"管理员 {OP_SZ}" in x[2]["text"] for x in edits)
        async with session_scope() as s:
            from sqlalchemy import select
            assert (await s.execute(select(Review.status).where(Review.review_id == rid3))).scalar_one() == "rejected"
        cb = CB(SUPER, f"admin_rev_ok:{rid3}")       # second tap: already handled
        await review_action(cb, fake)
        assert cb.answers == ["这条已经处理过了"]
        # backfill: the old pending review (never notified) is sent once, then never again
        fake.calls.clear()
        assert await review_notify.backfill_pending(24) >= 1  # other tests may leave pending rows
        assert any("昨晚的" in (x[2].get("text") or "") for x in fake.calls if x[0] == "text")
        assert await review_notify.backfill_pending(24) == 0
        # a broken bot never fails the submit
        class Boom(FakeBot):
            async def send_message(self, *a, **k):
                raise RuntimeError("telegram down")
            async def send_photo(self, *a, **k):
                raise RuntimeError("telegram down")
        m.bot = Boom()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
            r = await c.post("/api/reviews", json={"lamp_id": "RN-SZ", "stars": 4, "text": "x", "proofs": ["AgACAgUAAxkBAAIBb2Vk1Zq9xQz"]}, headers=tok(7823))
            assert r.status_code == 200 and r.json()["review"]["status"] == "pending"
            await asyncio.sleep(0.3)
        m.bot = m.bot_real
        await db.close_db()

    asyncio.run(run())
