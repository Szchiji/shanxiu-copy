"""启动时 Telegram 限流（Flood control）：按 retry_after 等待后重试，最多 3 次；仍失败也不算启动失败。
关闭时 aiohttp 会话一定会关（不再出现 Unclosed client session）。"""
import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123456789:TESTTOKENxxxxxxxxxxxxxxxxxxxxxxxxxxx")

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SetWebhook


def _flood(sec):
    return TelegramRetryAfter(method=SetWebhook(url="https://x/webhook"), message="Flood control exceeded", retry_after=sec)


def test_retry_after_then_success():
    import bot.main as m
    calls, slept = [], []

    async def call():
        calls.append(1)
        if len(calls) < 3:
            raise _flood(2)
        return "ok"

    async def fake_sleep(s):
        slept.append(s)

    assert asyncio.run(m._tg_call("set_webhook", call, sleep=fake_sleep)) == "ok"
    assert len(calls) == 3 and slept == [2 + m.TG_RETRY_BUFFER] * 2


def test_retry_gives_up_after_attempts_and_cap():
    import bot.main as m
    slept = []

    async def always():
        raise _flood(5)

    async def fake_sleep(s):
        slept.append(s)

    try:
        asyncio.run(m._tg_call("x", always, sleep=fake_sleep))
        raise AssertionError("should raise")
    except TelegramRetryAfter:
        pass
    assert len(slept) == m.TG_RETRY_ATTEMPTS - 1
    slept.clear()
    try:   # within the cap: waits twice, then gives up on the 3rd attempt
        asyncio.run(m._tg_call("x", always, sleep=fake_sleep, max_wait=30))
    except TelegramRetryAfter:
        pass
    assert slept == [5 + m.TG_RETRY_BUFFER] * 2   # 6.5 * 2 < 30
    slept.clear()

    async def big():   # a huge retry_after is not waited out (total cap)
        raise _flood(600)
    try:
        asyncio.run(m._tg_call("x", big, sleep=fake_sleep))
    except TelegramRetryAfter:
        pass
    assert slept == []


def test_webhook_failure_keeps_serving_and_hides_token(caplog, monkeypatch):
    import bot.main as m

    async def boom(**kw):
        raise _flood(600)

    monkeypatch.setattr(m.bot, "set_webhook", boom)
    with caplog.at_level("INFO"):
        ok = asyncio.run(m._setup_webhook())
    assert ok is False
    text = caplog.text
    assert "keeping the server up" in text and "TESTTOKEN" not in text


def test_shutdown_closes_bot_session_even_if_db_close_fails(monkeypatch):
    import bot.main as m
    closed = []

    async def bad():
        raise RuntimeError("db gone")

    async def ok():
        return None

    async def close_session():
        closed.append("bot")

    monkeypatch.setattr(m, "close_db", bad)
    monkeypatch.setattr(m.anti_brush, "close_redis", ok)
    monkeypatch.setattr(m.bot.session, "close", close_session)
    asyncio.run(m._shutdown())
    assert closed == ["bot"]
