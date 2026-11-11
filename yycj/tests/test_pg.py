"""Integration checks against a throwaway Postgres. Run with YYCJ_TEST_PG=postgresql+asyncpg://..."""
import asyncio
import os

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_pg_flow():
    os.environ["DATABASE_URL"] = PG
    from bot.config import get_settings
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

    async def run():
        import bot.main  # noqa: F401  (app import incl. middleware)
        from bot import db
        from bot.db import session_scope
        from sqlalchemy import text
        await db.connect_db()
        # ALTERs ran one per transaction: a deliberately bad one must not poison the rest.
        orig = list(db._ALTER_STATEMENTS)
        db._ALTER_STATEMENTS[:] = ["ALTER TABLE nope ADD COLUMN x int"] + orig
        await db._ensure_columns(db._engine)
        db._ALTER_STATEMENTS[:] = orig
        async with session_scope() as s:
            cols = {r[0] for r in (await s.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name='lamps'"))).all()}
        assert "extras" in cols

        from bot.services import home_service, admin_acl
        await home_service.get_or_create_settings()
        # Concurrent saves of different keys must all survive (row lock).
        await asyncio.gather(*[
            home_service.update_settings(ops_config={k: v})
            for k, v in [("chat_cta_label", "聊"), ("admin_contact", "https://t.me/x"),
                         ("bot_btn_label", "BOT"), ("listing_days", 15)]
        ])
        st = await home_service.get_or_create_settings()
        assert st["chat_cta_label"] == "聊" and st["admin_contact"] == "https://t.me/x"
        assert st["bot_btn_label"] == "BOT" and st["listing_days"] == 15

        await admin_acl.save([777, 888])
        await home_service.update_settings(ops_config={"chat_cta_label": "再聊"})
        st = await home_service.get_or_create_settings()
        assert st["extra_admin_ids"] == [777, 888]

        from bot.models import Lamp, User
        async with session_scope() as s:
            s.add(User(user_id=4242, is_banned=True))
            s.add(Lamp(lamp_id="L1", user_id=4242, title="t", city="c", status="active",
                       media=[{"type": "video", "url": "BAACfileid_1"}]))
        async with session_scope() as s:
            await s.execute(text("UPDATE lamps SET extras = CAST(:j AS jsonb) WHERE lamp_id='L1'"),
                            {"j": '{"_hours":"10-22","_remind_pending":"1"}'})

        from bot.services.extras_store import load_extras_bulk
        bulk = await load_extras_bulk(["L1", "nope"])
        assert bulk["L1"]["_hours"] == "10-22"

        from bot.services.ban_cache import is_banned, invalidate
        assert await is_banned(4242) is True
        assert await is_banned(5151) is False
        from bot.services import user_admin
        await user_admin.set_banned(4242, False)
        assert await is_banned(4242) is False

        from bot.api import routes_media
        assert await routes_media._known_media("BAACfileid_1") is True
        assert await routes_media._known_media("BAACother") is False

        from bot.services import open_shift
        await open_shift.expire_unanswered()
        await db.close_db()

    asyncio.run(run())
