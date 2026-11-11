"""按区目录 / 精选评价：全站默认 + 每城开关和频道、保存时检查机器人权限、换频道/关闭时清理旧目录（YYCJ_TEST_PG）。"""
import asyncio
import os
import types
import uuid

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


class Msg:
    def __init__(self, mid):
        self.message_id = mid


class FakeBot:
    ADMIN = {"@ok_push", "@ok_zh", "@ok_zh2", "@ok_rev"}

    def __init__(self):
        self.calls = []
        self.n = 900

    async def get_me(self):
        return types.SimpleNamespace(id=42, username="MoonTestBot")

    async def get_chat_member(self, chat, uid):
        if chat == "@missing":
            raise RuntimeError("Bad Request: chat not found")
        if chat in self.ADMIN:
            return types.SimpleNamespace(status="administrator", can_post_messages=True)
        if chat == "@nopost":
            return types.SimpleNamespace(status="administrator", can_post_messages=False)
        return types.SimpleNamespace(status="member")

    async def send_message(self, chat, text, reply_markup=None, **kw):
        self.n += 1
        self.calls.append(("send", chat, text, self.n))
        return Msg(self.n)

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None, **kw):
        self.calls.append(("edit", chat_id, text, message_id))
        return Msg(message_id)

    async def delete_message(self, chat_id, message_id):
        self.calls.append(("delete", chat_id, "", message_id))
        return True


def test_channel_features():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import Lamp, User
        from bot.services import bot_info, broadcast, channel_directory, home_service
        db._engine = None
        await db.connect_db()
        await db._ensure_columns(db._engine)
        SUPER = 1
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        async with session_scope() as s:
            s.add(User(user_id=8901, role="teacher"))
            for t, c, d in (("珠一", "珠海", "香洲"), ("珠二", "珠海", "金湾"), ("佛一", "佛山", "禅城")):
                s.add(Lamp(lamp_id=str(uuid.uuid4()), user_id=8901, title=t, city=c, district=d, status="active"))
        prev_cities = list((await home_service.get_or_create_settings()).get("enabled_cities") or [])
        await home_service.update_settings(enabled_cities=["珠海", "佛山"],
                                           ops_config={"broadcast_channel": "", "city_overrides": {}, "channel_directory": False,
                                                       "broadcast_reviews": False, "directory_channel": "", "review_channel": ""})
        bot_info._cache.update({"username": "MoonTestBot", "ts": 9e12})
        fake = FakeBot()
        real = m.bot
        m.bot = fake
        m._ready = True
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
                h = tok(SUPER)
                cf = lambda body: c.post("/api/admin/channel-features", json=body, headers=h)
                # both default off; overview lists each city
                o = (await c.get("/api/admin/channel-features", headers=h)).json()
                assert o["global"]["channel_directory"] is False and o["global"]["broadcast_reviews"] is False
                assert [x["city"] for x in o["cities"]] == ["珠海", "佛山"]
                # switch on with no channel anywhere → clear error
                r = await cf({"channel_directory": True})
                assert r.status_code == 400 and "没有频道" in r.json()["detail"]
                # bad format / missing / not admin / no post right
                for ch, word in (("not a chan", "格式不对"), ("@missing", "找不到"), ("@someone", "不是"), ("@nopost", "发消息")):
                    r = await cf({"directory_channel": ch})
                    assert r.status_code == 400 and word in r.json()["detail"], (ch, r.text)
                # push channel set → site-wide on falls back to it
                await home_service.update_settings(ops_config={"broadcast_channel": "@ok_push"})
                r = await cf({"channel_directory": True, "broadcast_reviews": True, "review_channel": "@ok_rev"})
                assert r.status_code == 200, r.text
                zh = [x for x in r.json()["cities"] if x["city"] == "珠海"][0]
                assert zh["channel_directory"]["chat"] == "@ok_push" and zh["channel_directory"]["via"] == "site_push"
                assert zh["broadcast_reviews"]["chat"] == "@ok_rev" and zh["broadcast_reviews"]["via"] == "site"
                assert r.json()["directory_applies_at"] in ("今天 12:00", "明天 12:00")
                # site-wide save: inherited channels checked per city, only warnings
                assert r.json()["warnings"] == []
                await asyncio.sleep(0.2)
                assert not fake.calls   # nothing posted on save — applied at 12:00
                await channel_directory.job_refresh_all()
                posted = {x[1]: x[2] for x in fake.calls if x[0] == "send"}
                assert "@ok_push" in posted
                texts = [x[2] for x in fake.calls if x[0] == "send"]
                zh_txt = [t for t in texts if "珠海" in t][0]
                fs_txt = [t for t in texts if "佛山" in t][0]
                assert "香洲" in zh_txt and "金湾" in zh_txt and "禅城" not in zh_txt and "佛一" not in zh_txt
                assert "禅城" in fs_txt and "香洲" not in fs_txt
                # city override: 珠海 gets its own directory channel (checked), old message removed, new posted
                r = await c.post("/api/admin/cities/settings", json={"city": "珠海", "overrides": {"directory_channel": "@missing"}}, headers=h)
                assert r.status_code == 400 and "找不到" in r.json()["detail"]
                fake.calls.clear()
                r = await c.post("/api/admin/cities/settings", json={"city": "珠海", "overrides": {"directory_channel": "@ok_zh"}}, headers=h)
                assert r.status_code == 200, r.text
                await asyncio.sleep(0.2)
                assert not fake.calls
                await channel_directory.job_refresh_all()
                kinds = [(k, ch) for k, ch, _t, _m in fake.calls]
                assert ("delete", "@ok_push") in kinds and ("send", "@ok_zh") in kinds
                # the daily job edits the same message, per city
                fake.calls.clear()
                await channel_directory.job_refresh_all()
                assert sorted((k, ch) for k, ch, _t, _m in fake.calls) == [("edit", "@ok_push"), ("edit", "@ok_zh")]
                # a city that inherits a broken channel → warning on 全站 save, save still OK
                await home_service.update_city_overrides("佛山", {"broadcast_channel": "@someone"})
                r = await cf({"broadcast_reviews": True})
                assert r.status_code == 200
                w = r.json()["warnings"]
                assert w and w[0]["city"] == "佛山" and "不是" in w[0]["error"], w
                await home_service.update_city_overrides("佛山", {"broadcast_channel": None})
                # 珠海 switched off → its directory is deleted; 佛山 keeps going
                fake.calls.clear()
                r = await c.post("/api/admin/cities/settings", json={"city": "珠海", "overrides": {"channel_directory": False}}, headers=h)
                assert r.status_code == 200 and r.json()["directory_applies_at"]
                fake.calls.clear()
                await channel_directory.job_refresh_all()
                assert ("delete", "@ok_zh") in [(k, ch) for k, ch, _t, _m in fake.calls]
                o = (await c.get("/api/admin/channel-features", headers=h)).json()
                zh = [x for x in o["cities"] if x["city"] == "珠海"][0]
                assert zh["channel_directory"]["on"] is False and zh["channel_directory"]["own_switch"] is True
                # operators can't set channels (super only), like 推送频道
                # 精选评价 goes to its own channel
                fake.calls.clear()
                lamp = {"lamp_id": str(uuid.uuid4()), "title": "珠一", "city": "珠海", "district": "香洲"}
                await broadcast.broadcast_review({"stars": 5, "text": "好"}, lamp)
                assert fake.calls and fake.calls[-1][1] == "@ok_rev"
                # site-wide off again → every directory retired
                fake.calls.clear()
                r = await cf({"channel_directory": False, "broadcast_reviews": False})
                assert r.status_code == 200
                await channel_directory.job_refresh_all()
                assert ("delete", "@ok_push") in [(k, ch) for k, ch, _t, _m in fake.calls]
        finally:
            m.bot = real
            await home_service.update_settings(enabled_cities=prev_cities,
                                               ops_config={"broadcast_channel": "", "city_overrides": {}, "channel_directory": False,
                                                           "broadcast_reviews": False, "directory_channel": "", "review_channel": ""})
            await db.close_db()

    asyncio.run(run())
