"""Per-city settings overrides: fallback, consumers, permissions (throwaway Postgres, YYCJ_TEST_PG)."""
import asyncio
import os
from datetime import datetime, timedelta

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


def test_city_settings():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import Lamp, User
        from bot.services import admin_acl, bot_info, broadcast, home_service, listing_ops
        from bot.services.home_feed import ops_for_city
        await db.connect_db()
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        SUPER, OP, OP_ALL = 1, 600, 601
        async with session_scope() as s:
            s.add(User(user_id=61, role="teacher"))
            s.add(Lamp(lamp_id="CS-SZ", user_id=61, title="sz", city="深圳", status="active"))
            s.add(Lamp(lamp_id="CS-GZ", user_id=61, title="gz", city="广州", status="active"))
        await home_service.update_settings(
            enabled_cities=["深圳", "广州", "东莞"],
            ops_config={"broadcast_channel": "@all_ch", "home_feed_page_size": 3, "listing_days": 30,
                        "admin_contact": "https://t.me/alladmin", "show_admin_link": True,
                        "operator_cities": {}, "extra_admin_ids": []},
        )
        await admin_acl.save([OP, OP_ALL])
        await home_service.update_settings(ops_config={"operator_cities": {str(OP): ["广州"]}})

        async def fake_ident():
            return {"username": "yycj_test_bot"}
        bot_info.get_bot_identity = fake_ident
        m._ready = True
        t = httpx.ASGITransport(app=m.app)
        async with httpx.AsyncClient(transport=t, base_url="http://t") as c:
            g = lambda path, uid: c.get(path, headers=tok(uid))
            p = lambda body, uid: c.post("/api/admin/cities/settings", json=body, headers=tok(uid))
            # nothing set: every key inherits, global shown
            r = (await g("/api/admin/cities/settings?city=深圳", SUPER)).json()
            by = {k["key"]: k for k in r["keys"]}
            assert r["overrides"] == {} and by["broadcast_channel"]["global"] == "@all_ch" and by["broadcast_channel"]["override"] is None
            assert r["can_edit"] and r["can_edit_super"]
            # super sets 深圳 overrides
            r = await p({"city": "深圳", "overrides": {"broadcast_channel": "@sz_ch", "home_feed_page_size": "5",
                                                     "show_admin_link": False, "admin_contact": "@szadmin", "listing_days": 7,
                                                     "chat_cta_label": "  "}}, SUPER)
            assert r.status_code == 200, r.text
            ov = r.json()["overrides"]
            assert ov["broadcast_channel"] == "@sz_ch" and ov["home_feed_page_size"] == 5 and ov["show_admin_link"] is False
            assert ov["listing_days"] == 7 and "chat_cta_label" not in ov and ov["admin_contact"].endswith("szadmin")
            site = await home_service.get_or_create_settings()
            assert ops_for_city(site, "深圳")["broadcast_channel"] == "@sz_ch"
            assert ops_for_city(site, "广州")["broadcast_channel"] == "@all_ch"
            assert ops_for_city(site, None)["broadcast_channel"] == "@all_ch"
            assert ops_for_city(site, "")["broadcast_channel"] == "@all_ch"
            # push channel follows the card's city; card without a city -> site-wide channel
            assert (await broadcast._caption({"city": "深圳", "title": "a"}, None))[0] == "@sz_ch"
            assert (await broadcast._caption({"city": "广州", "title": "a"}, None))[0] == "@all_ch"
            assert (await broadcast._caption({"title": "a"}, None))[0] == "@all_ch"
            # listing days per city
            d1 = await listing_ops.set_listing_expiry("CS-SZ")
            d2 = await listing_ops.set_listing_expiry("CS-GZ")
            now = datetime.utcnow()
            assert timedelta(days=6) < d1["expires_at"] - now <= timedelta(days=7)
            assert timedelta(days=29) < d2["expires_at"] - now <= timedelta(days=30)
            # /api/home picks up the city's values, other cities keep site-wide
            h = (await g("/api/home?city=深圳", 61)).json()
            assert h["page_size"] == 5 and h["limit"] == 5 and h["contacts"]["show_admin"] is False
            h = (await g("/api/home?city=广州", 61)).json()
            assert h["page_size"] == 3 and h["contacts"]["show_admin"] is True and h["contacts"]["admin_url"].endswith("alladmin")
            # clear one key -> back to site-wide
            r = await p({"city": "深圳", "overrides": {"broadcast_channel": None, "home_feed_page_size": ""}}, SUPER)
            assert "broadcast_channel" not in r.json()["overrides"] and "home_feed_page_size" not in r.json()["overrides"]
            assert ops_for_city(await home_service.get_or_create_settings(), "深圳")["broadcast_channel"] == "@all_ch"
            # validation
            assert (await p({"city": "深圳", "overrides": {"bot_token": "x"}}, SUPER)).status_code == 400
            assert (await p({"city": "火星", "overrides": {"chat_cta_label": "x"}}, SUPER)).status_code == 400
            assert (await p({"city": "深圳", "overrides": {"admin_contact": "not a link !!"}}, SUPER)).status_code == 400
            # scoped operator: own city, non-super keys only
            r = await p({"city": "广州", "overrides": {"chat_cta_label": "聊聊吧"}}, OP)
            assert r.status_code == 200 and r.json()["overrides"]["chat_cta_label"] == "聊聊吧" and not r.json()["can_edit_super"]
            assert (await p({"city": "广州", "overrides": {"broadcast_channel": "@x"}}, OP)).status_code == 403
            assert (await p({"city": "深圳", "overrides": {"chat_cta_label": "x"}}, OP)).status_code == 403
            assert (await g("/api/admin/cities/settings?city=深圳", OP)).status_code == 403
            # unassigned operator: can read every city, can't edit
            r = (await g("/api/admin/cities/settings?city=深圳", OP_ALL)).json()
            assert r["can_edit"] is False
            assert (await p({"city": "深圳", "overrides": {"chat_cta_label": "x"}}, OP_ALL)).status_code == 403
            sm = (await g("/api/admin/cities/summary", SUPER)).json()
            assert sm["city_override_counts"]["广州"] == 1
            # global save keeps overrides
            await home_service.update_settings(ops_config={"chat_cta_label": "想聊聊"})
            assert (await home_service.get_or_create_settings())["city_overrides"]["广州"]["chat_cta_label"] == "聊聊吧"
        await db.close_db() if hasattr(db, "close_db") else None

    asyncio.run(run())
