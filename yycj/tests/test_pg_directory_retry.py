"""按区目录刷新遇到 Telegram 临时错误：编辑重试、不发重复新帖；真「消息不存在」才发新帖；整城失败几分钟后再试一次（YYCJ_TEST_PG）。"""
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


def test_directory_transient_retry():
    os.environ["DATABASE_URL"] = PG

    async def run():
        from aiogram.exceptions import TelegramBadRequest, TelegramServerError
        from aiogram.methods import GetMe
        from sqlalchemy import delete, select
        import bot.main as m
        from bot import db
        from bot.db import session_scope
        from bot.models import Lamp, User
        from bot.models_extra import ChannelDirectory
        from bot.services import bot_info, channel_directory as cd, home_service, tg_retry
        db._engine = None
        await db.connect_db()
        await db._ensure_columns(db._engine)
        tg_retry.TG_BACKOFF, old_backoff = (0.0, 0.0, 0.0), tg_retry.TG_BACKOFF
        CITY = "惠州"
        lid = str(uuid.uuid4())
        async with session_scope() as s:
            s.add(User(user_id=8971, role="teacher"))
            s.add(Lamp(lamp_id=lid, user_id=8971, title="惠一", city=CITY, district="惠城", status="active"))
            s.add(ChannelDirectory(city=CITY, chat="@hz_dir", message_id=500))
        prev = await home_service.get_or_create_settings()
        prev_cities = list(prev.get("enabled_cities") or [])
        await home_service.update_settings(enabled_cities=[CITY], ops_config={"broadcast_channel": "@hz_dir", "city_overrides": {},
                                                                              "channel_directory": True, "directory_channel": ""})
        bot_info._cache.update({"username": "MoonTestBot", "ts": 9e12})

        class Bot:
            def __init__(self, edit_errors=(), send_errors=()):
                self.edit_errors, self.send_errors, self.calls = list(edit_errors), list(send_errors), []

            async def edit_message_text(self, text, chat_id, message_id, **kw):
                self.calls.append(("edit", message_id))
                if self.edit_errors:
                    raise self.edit_errors.pop(0)
                return Msg(message_id)

            async def send_message(self, chat, text, **kw):
                self.calls.append(("send", chat))
                if self.send_errors:
                    raise self.send_errors.pop(0)
                return Msg(777)

            async def get_me(self):
                return types.SimpleNamespace(id=1, username="MoonTestBot", first_name="x")

        bg = lambda: TelegramServerError(method=GetMe(), message="Bad Gateway")
        real = m.bot
        try:
            # one Bad Gateway on the edit → the edit is retried, succeeds, no new post
            m.bot = Bot(edit_errors=[bg()])
            assert await cd._refresh_city(CITY) == "ok"
            assert m.bot.calls == [("edit", 500), ("edit", 500)]
            # Bad Gateway on every try → fail, but NO duplicate post
            m.bot = Bot(edit_errors=[bg(), bg(), bg()])
            assert await cd._refresh_city(CITY) == "fail"
            assert [k for k, _ in m.bot.calls] == ["edit", "edit", "edit"]
            # genuine "message to edit not found" → falls back to a new post (also retried on Bad Gateway)
            m.bot = Bot(edit_errors=[TelegramBadRequest(method=GetMe(), message="Bad Request: message to edit not found")],
                        send_errors=[bg()])
            assert await cd._refresh_city(CITY) == "ok"
            assert [k for k, _ in m.bot.calls] == ["edit", "send", "send"]
            async with session_scope() as s:
                assert (await s.execute(select(ChannelDirectory.message_id).where(ChannelDirectory.city == CITY))).scalar_one() == 777
            # noon job: a city that still fails is retried once later
            scheduled = []
            orig = cd._schedule_retry
            cd._schedule_retry = lambda cities: scheduled.append(list(cities))
            m.bot = Bot(edit_errors=[bg(), bg(), bg()])
            assert await cd.job_refresh_all() == 0 and scheduled == [[CITY]]
            cd._schedule_retry = orig
            # the later retry succeeds
            m.bot = Bot()
            slept = []

            async def fake_sleep(s):
                slept.append(s)
            assert await cd._retry_later([CITY], sleep=fake_sleep) == 1 and slept == [cd.RETRY_CITY_AFTER]
            assert m.bot.calls == [("edit", 777)]
            # public bool API unchanged
            assert await cd.refresh_city(CITY) is True
        finally:
            m.bot = real
            tg_retry.TG_BACKOFF = old_backoff
            async with session_scope() as s:
                await s.execute(delete(ChannelDirectory).where(ChannelDirectory.city == CITY))
                await s.execute(delete(Lamp).where(Lamp.lamp_id == lid))
                await s.execute(delete(User).where(User.user_id == 8971))
            await home_service.update_settings(enabled_cities=prev_cities, ops_config={"broadcast_channel": "", "city_overrides": {},
                                                                                       "channel_directory": False, "directory_channel": ""})
            await db.close_db()

    asyncio.run(run())
