"""管理员硬删资料：软下架不变；硬删清掉资料、会话、该资料相关信用流水及其直接子表；删前记下城市并触发按区目录防抖刷新（YYCJ_TEST_PG）。"""
import asyncio
import os
import types
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
        self.calls = []

    async def edit_message_text(self, text, chat_id, message_id, **kw):
        self.calls.append(("edit", chat_id, message_id, text))
        return Msg(message_id)

    async def edit_message_caption(self, **kw):
        self.calls.append(("caption", kw.get("chat_id"), kw.get("message_id"), kw.get("caption")))
        return Msg(kw.get("message_id"))

    async def edit_message_reply_markup(self, **kw):
        self.calls.append(("markup", kw.get("chat_id"), kw.get("message_id")))
        return Msg(kw.get("message_id"))

    async def send_message(self, chat, text, **kw):
        self.calls.append(("send", chat, 0, text))
        return Msg(321)

    async def get_me(self):
        return types.SimpleNamespace(id=1, username="MoonTestBot", first_name="x")


def test_admin_hard_delete_keeps_unlist_and_refreshes_directory():
    os.environ["DATABASE_URL"] = PG

    async def run():
        from sqlalchemy import delete, select
        import bot.main as m
        from bot import db
        from bot.db import session_scope
        from bot.models import CreditHistory, HomepagePin, Lamp, Report, Review, Session, SessionMessage, User
        from bot.models_extra import ChannelDirectory, Coupon, CouponClaim, Favorite, LampView
        from bot.services import bot_info, channel_directory as cd, home_service, listing_ops
        db._engine = None
        await db.connect_db()
        await db._ensure_columns(db._engine)
        CITY = "江门"
        lids = [str(uuid.uuid4()) for _ in range(2)]
        rid = str(uuid.uuid4())
        cid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        now = datetime.utcnow()
        async with session_scope() as s:
            s.add(User(user_id=8991, role="teacher"))
            s.add(Lamp(lamp_id=lids[0], user_id=8991, title="硬删甲", city=CITY, district="蓬江",
                       status="active", description="x"))
            s.add(Lamp(lamp_id=lids[1], user_id=8991, title="软下架乙", city=CITY, district="新会", status="active"))
            s.add(HomepagePin(lamp_id=lids[0], sort_order=1))
            s.add(Favorite(user_id=1, lamp_id=lids[0]))
            s.add(LampView(lamp_id=lids[0], day=now.date(), views=3))
            s.add(Review(review_id=rid, lamp_id=lids[0], guest_id=2, stars=5, text="好", status="approved"))
            s.add(Report(report_id=str(uuid.uuid4()), lamp_id=lids[0], reporter_id=3, reason="spam"))
            s.add(Coupon(coupon_id=cid, batch_id=cid, user_id=8991, lamp_id=lids[0], city=CITY,
                         text="100P", ends_at=now + timedelta(days=1), quota=2, status="active"))
            s.add(CouponClaim(coupon_id=cid, batch_id=cid, user_id=7, code="123456"))
            s.add(Session(session_id=sid, lamp_id=lids[0], user_a_id=8991, user_b_id=7, status="ended"))
            s.add(SessionMessage(session_id=sid, from_role="a", content="hi"))
            s.add(CreditHistory(user_id=8991, action="session_end", delta=2, reason="会话结算", related_id=sid))
            s.add(CreditHistory(user_id=8991, action="daily_recovery", delta=1, reason="日恢复", related_id=None))
            s.add(CreditHistory(user_id=8991, action="other", delta=1, reason="别的资料", related_id=lids[1]))
            s.add(ChannelDirectory(city=CITY, chat="@jm_dir", message_id=700))
        prev = await home_service.get_or_create_settings()
        prev_cities = list(prev.get("enabled_cities") or [])
        await home_service.update_settings(enabled_cities=[CITY], ops_config={
            "broadcast_channel": "@jm_dir", "channel_directory": True, "directory_channel": "@jm_dir",
            "city_overrides": {}})
        bot_info._cache.update({"username": "MoonTestBot", "ts": 9e12})
        fake, real = FakeBot(), m.bot
        m.bot = fake
        gate = asyncio.Event()
        waits = []

        async def fake_sleep(sec):
            waits.append(sec)
            await gate.wait()
        cd._sleep, old_sleep = fake_sleep, cd._sleep
        try:
            # soft unlist leaves the row
            out = await listing_ops.unlist_lamp(lids[1], reason="admin")
            assert out["status"] == "hidden"
            async with session_scope() as s:
                assert (await s.execute(select(Lamp).where(Lamp.lamp_id == lids[1]))).scalar_one().status == "hidden"
            # finish the soft-unlist debounce so hard-delete gets its own refresh
            gate.set()
            await asyncio.sleep(0.05)
            t = cd._pending.get(CITY)
            if t:
                await t
            # hard delete removes lamp + children, keeps session
            gate.clear(); waits.clear(); fake.calls.clear()
            out = await listing_ops.delete_lamp(lids[0])
            await asyncio.sleep(0.01)
            assert out["deleted"] is True and out["city"] == CITY
            assert list(cd._pending) == [CITY] and waits == [cd.NEW_LISTING_DEBOUNCE]
            async with session_scope() as s:
                assert (await s.execute(select(Lamp).where(Lamp.lamp_id == lids[0]))).scalar_one_or_none() is None
                assert (await s.execute(select(HomepagePin).where(HomepagePin.lamp_id == lids[0]))).scalars().first() is None
                assert (await s.execute(select(Favorite).where(Favorite.lamp_id == lids[0]))).scalars().first() is None
                assert (await s.execute(select(LampView).where(LampView.lamp_id == lids[0]))).scalars().first() is None
                assert (await s.execute(select(Review).where(Review.lamp_id == lids[0]))).scalars().first() is None
                assert (await s.execute(select(Report).where(Report.lamp_id == lids[0]))).scalars().first() is None
                assert (await s.execute(select(Coupon).where(Coupon.coupon_id == cid))).scalar_one_or_none() is None
                assert (await s.execute(select(CouponClaim).where(CouponClaim.coupon_id == cid))).scalars().first() is None
                assert (await s.execute(select(Session).where(Session.session_id == sid))).scalar_one_or_none() is None
                assert (await s.execute(select(SessionMessage).where(SessionMessage.session_id == sid))).scalars().first() is None
                assert (await s.execute(select(CreditHistory).where(CreditHistory.related_id == sid))).scalars().first() is None
                # unrelated credit stays
                left = list((await s.execute(select(CreditHistory).where(CreditHistory.user_id == 8991))).scalars())
                assert {x.action for x in left} == {"daily_recovery", "other"}
                soft = (await s.execute(select(Lamp).where(Lamp.lamp_id == lids[1]))).scalar_one()
                assert soft.status == "hidden" and soft.title == "软下架乙"
            gate.set()
            await asyncio.sleep(0.05)
            t = cd._pending.get(CITY)
            if t:
                await t
            # missing id
            try:
                await listing_ops.delete_lamp(lids[0])
                assert False, "expected missing"
            except ValueError:
                pass
        finally:
            gate.set()
            cd._sleep = old_sleep
            m.bot = real
            async with session_scope() as s:
                await s.execute(delete(ChannelDirectory).where(ChannelDirectory.city == CITY))
                await s.execute(delete(SessionMessage).where(SessionMessage.session_id == sid))
                await s.execute(delete(Session).where(Session.session_id == sid))
                await s.execute(delete(CreditHistory).where(CreditHistory.user_id == 8991))
                await s.execute(delete(CouponClaim).where(CouponClaim.coupon_id == cid))
                await s.execute(delete(Coupon).where(Coupon.coupon_id == cid))
                await s.execute(delete(Review).where(Review.review_id == rid))
                await s.execute(delete(Report).where(Report.lamp_id.in_(lids)))
                await s.execute(delete(Favorite).where(Favorite.lamp_id.in_(lids)))
                await s.execute(delete(LampView).where(LampView.lamp_id.in_(lids)))
                await s.execute(delete(HomepagePin).where(HomepagePin.lamp_id.in_(lids)))
                await s.execute(delete(Lamp).where(Lamp.lamp_id.in_(lids)))
                await s.execute(delete(User).where(User.user_id == 8991))
            await home_service.update_settings(enabled_cities=prev_cities, ops_config={
                "broadcast_channel": "", "channel_directory": False, "directory_channel": "", "city_overrides": {}})
            await db.close_db()

    asyncio.run(run())
