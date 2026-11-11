"""/api/home: extras loaded in one query (no per-card N+1), feed index present (YYCJ_TEST_PG)."""
import asyncio
import os

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_home_no_n_plus_one():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        from sqlalchemy import event, text
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import HomepagePin, Lamp, User
        await db.connect_db()
        await db._ensure_columns(db._engine)
        async with session_scope() as s:
            s.add(User(user_id=9301, role="guest"))
            for i in range(10):
                s.add(Lamp(lamp_id=f"P{i}", user_id=9301, title=f"t{i}", city="深圳", district="性能测试区", status="active"))
        async with session_scope() as s:
            await s.execute(text("""UPDATE lamps SET extras = '{"_hours":[{"start":"10:00","end":"22:00"}]}'::jsonb WHERE lamp_id IN ('P0','P1')"""))
            s.add(HomepagePin(lamp_id="P0", sort_order=1))
            s.add(HomepagePin(lamp_id="P3", sort_order=2))
        async with session_scope() as s:
            idx = (await s.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_lamps_feed'"))).first()
            assert idx is not None
        m._ready = True
        tok = {"Authorization": "Bearer " + issue_access_token(9301, get_settings().bot_token)}
        # Seed site defaults outside steady-state query budget on a fresh isolated DB.
        from bot.services import home_service
        await home_service.get_or_create_settings()
        stmts = []

        def count(conn, cursor, statement, *a):
            stmts.append(statement)
        event.listen(db._engine.sync_engine, "before_cursor_execute", count)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
                # own district so lamps from other PG tests in the shared DB do not mix in
                r = await c.get("/api/home?city=深圳&limit=10&district=性能测试", headers=tok)
        finally:
            event.remove(db._engine.sync_engine, "before_cursor_execute", count)
        data = r.json()
        assert r.status_code == 200 and len(data["items"]) == 10
        by_id = {x["lamp_id"]: x for x in data["items"]}
        assert by_id["P0"]["hours_text"] and by_id["P1"]["hours_text"] and not by_id["P5"]["hours_text"]
        assert [p["lamp"]["lamp_id"] for p in data["pins"]] == ["P0", "P3"]
        assert data["pins"][0]["lamp"]["hours_text"]
        per_card = [s for s in stmts if "SELECT extras FROM lamps WHERE lamp_id" in s]
        assert per_card == [], f"{len(per_card)} per-card extras queries"
        assert len(stmts) <= 10, stmts
        await db.close_db()

    asyncio.run(run())
