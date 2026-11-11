"""有上新自动刷新按区目录：只刷新这一城、这一城开了才刷、每城防抖（多条上新只刷一次）、审核通过 / 续期重新上架都会触发；
下架 / 封号 / 到期 / 删除 / 改城市也会刷新；改设置不触发（YYCJ_TEST_PG）。"""
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

    async def send_message(self, chat, text, **kw):
        self.calls.append(("send", chat, 0, text))
        return Msg(321)

    async def get_me(self):
        return types.SimpleNamespace(id=1, username="MoonTestBot", first_name="x")


def test_directory_auto_refresh_on_new_listing():
    os.environ["DATABASE_URL"] = PG

    async def run():
        from sqlalchemy import delete
        import bot.main as m
        from bot import db
        from bot.db import session_scope
        from bot.models import Lamp, User
        from bot.models_extra import ChannelDirectory
        from bot.services import bot_info, channel_directory as cd, home_service, listing_ops, search_service
        import bot.services.search_hooks  # noqa: F401  (wires approve_lamp)
        db._engine = None
        await db.connect_db()
        await db._ensure_columns(db._engine)
        ON, OFF = "江门", "肇庆"
        ids = [str(uuid.uuid4()) for _ in range(5)]
        now = datetime.utcnow()
        async with session_scope() as s:
            s.add(User(user_id=8981, role="teacher"))
            s.add(Lamp(lamp_id=ids[0], user_id=8981, title="江一", city=ON, district="蓬江", status="pending"))
            s.add(Lamp(lamp_id=ids[1], user_id=8981, title="江二", city=ON, district="新会", status="pending"))
            s.add(Lamp(lamp_id=ids[2], user_id=8981, title="江三", city=ON, district="蓬江", status="hidden",
                       unlist_reason="expired", expires_at=now - timedelta(days=1)))
            s.add(Lamp(lamp_id=ids[3], user_id=8981, title="肇一", city=OFF, district="端州", status="pending"))
            s.add(Lamp(lamp_id=ids[4], user_id=8981, title="江四", city=ON, district="江海", status="hidden",
                       unlist_reason="admin", expires_at=now + timedelta(days=5)))
            s.add(ChannelDirectory(city=ON, chat="@jm_dir", message_id=600))
        prev = await home_service.get_or_create_settings()
        prev_cities = list(prev.get("enabled_cities") or [])
        await home_service.update_settings(enabled_cities=[ON, OFF], ops_config={
            "broadcast_channel": "@jm_dir", "channel_directory": False, "directory_channel": "",
            "city_overrides": {ON: {"channel_directory": True}}})
        bot_info._cache.update({"username": "MoonTestBot", "ts": 9e12})
        fake, real = FakeBot(), m.bot
        m.bot = fake
        waits = []
        gate = asyncio.Event()

        async def fake_sleep(sec):     # debounce window: held open until the test releases it
            waits.append(sec)
            await gate.wait()
        cd._sleep, old_sleep = fake_sleep, cd._sleep
        try:
            # two approvals close together in the enabled city → one refresh
            await search_service.approve_lamp(ids[0])
            await search_service.approve_lamp(ids[1])
            assert list(cd._pending) == [ON] and waits == [cd.NEW_LISTING_DEBOUNCE]
            # disabled city: nothing scheduled
            await search_service.approve_lamp(ids[3])
            assert OFF not in cd._pending
            assert fake.calls == []
            gate.set()
            await asyncio.sleep(0.05)
            t = cd._pending.get(ON)
            if t:
                await t
            edits = [c for c in fake.calls if c[0] == "edit"]
            assert len(edits) == 1 and edits[0][1:3] == ("@jm_dir", 600) and "江一" in edits[0][3] and "江二" in edits[0][3]
            assert "肇一" not in edits[0][3] and not [c for c in fake.calls if c[0] == "send"]
            # renew an expired listing (hidden → active) and relist an unlisted one → both trigger, again coalesced
            gate.clear()
            fake.calls.clear()
            waits.clear()
            await listing_ops.set_listing_expiry(ids[2], days=7)
            await listing_ops.relist_lamp(ids[4],actor_id=1,operation_id=str(uuid.uuid4()),expected_updated_at=(await search_service.get_lamp(ids[4]))['updated_at'].isoformat())
            assert list(cd._pending) == [ON] and len(waits) == 1
            gate.set()
            await asyncio.sleep(0.05)
            edits = [c for c in fake.calls if c[0] == "edit"]
            assert len(edits) == 1 and "江三" in edits[0][3] and "江四" in edits[0][3]
            # renewing a listing that is already live is not a 上新
            waits.clear()
            await listing_ops.set_listing_expiry(ids[2], days=7)
            assert waits == [] and not cd._pending
            # leaving the directory: admin unlist + ban-unlist coalesce into one refresh; the listing is gone from it
            async def cycle(*acts, expect_calls=1):
                gate.clear(); fake.calls.clear(); waits.clear()
                for act in acts:
                    await act()
                await asyncio.sleep(0.01)
                assert len(waits) == (1 if expect_calls else 0), waits
                gate.set()
                await asyncio.sleep(0.05)
                e = [c for c in fake.calls if c[0] == "edit"]
                assert len(e) == expect_calls
                return e[0][3] if e else ""
            txt = await cycle(lambda: listing_ops.unlist_lamp(ids[0], reason="admin"),
                              lambda: listing_ops.unlist_lamp(ids[1], reason="banned"))
            assert "江一" not in txt and "江二" not in txt and "江三" in txt
            # unlisting something already hidden → nothing
            await cycle(lambda: listing_ops.unlist_lamp(ids[0]), expect_calls=0)
            # scheduler auto-expiry → refresh
            async with session_scope() as s:
                from sqlalchemy import select
                (await s.execute(select(Lamp).where(Lamp.lamp_id == ids[2]))).scalar_one().expires_at = \
                    datetime.utcnow() - timedelta(minutes=1)
            txt = await cycle(listing_ops.expire_due_lamps)
            assert "江三" not in txt and "江四" in txt
            # moving a live listing to another (disabled) city → old city refreshed
            from bot.services import listing_flow
            txt = await cycle(lambda: listing_flow.apply_admin_edit(ids[4], {"city": OFF, "description": "x"}))
            assert "江四" not in txt
            # deletion: city captured before the row is gone
            ids.append(str(uuid.uuid4()))
            async with session_scope() as s:
                s.add(Lamp(lamp_id=ids[-1], user_id=8981, title="江五", city=ON, district="蓬江", status="active",
                           description="演示"))
            from bot.services import demo_purge
            txt = await cycle(demo_purge.purge_demo_listings)
            assert "江五" not in txt
            # settings change does not trigger anything
            await home_service.update_city_overrides(ON, {"channel_directory": True})
            assert not cd._pending
        finally:
            gate.set()
            cd._sleep = old_sleep
            m.bot = real
            async with session_scope() as s:
                await s.execute(delete(ChannelDirectory).where(ChannelDirectory.city.in_([ON, OFF])))
                await s.execute(delete(Lamp).where(Lamp.lamp_id.in_(ids)))
                await s.execute(delete(User).where(User.user_id == 8981))
            await home_service.update_settings(enabled_cities=prev_cities, ops_config={
                "broadcast_channel": "", "channel_directory": False, "directory_channel": "", "city_overrides": {}})
            await db.close_db()

    asyncio.run(run())
