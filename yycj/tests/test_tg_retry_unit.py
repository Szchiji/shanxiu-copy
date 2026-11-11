"""Telegram 临时错误重试（Bad Gateway / 5xx / 网络 / 限流）：短退避约 3 次；真错误立即抛出。"""
import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123456789:TESTTOKENxxxxxxxxxxxxxxxxxxxxxxxxxxx")

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.methods import GetMe

from bot.services import tg_retry


def _bad_gateway():
    return TelegramServerError(method=GetMe(), message="Bad Gateway")


def _run(make, **kw):
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    return asyncio.run(tg_retry.tg_call("t", make, sleep=fake_sleep, **kw)), slept


def test_bad_gateway_retried_then_ok():
    n = []

    async def call():
        n.append(1)
        if len(n) < 3:
            raise _bad_gateway()
        return "ok"
    out, slept = _run(call)
    assert out == "ok" and len(n) == 3 and slept == [1.0, 3.0]


def test_network_error_gives_up_after_three():
    n = []

    async def call():
        n.append(1)
        raise TelegramNetworkError(method=GetMe(), message="Request timeout error")
    with pytest.raises(TelegramNetworkError):
        _run(call)
    assert len(n) == 3


def test_bad_request_not_retried():
    n = []

    async def call():
        n.append(1)
        raise TelegramBadRequest(method=GetMe(), message="Bad Request: message to edit not found")
    with pytest.raises(TelegramBadRequest):
        _run(call)
    assert len(n) == 1


def test_is_transient():
    assert tg_retry.is_transient(_bad_gateway()) and tg_retry.is_transient(asyncio.TimeoutError())
    assert not tg_retry.is_transient(TelegramBadRequest(method=GetMe(), message="x")) and not tg_retry.is_transient(ValueError())


def test_main_reuses_helper():
    import bot.main as m
    assert m._tg_call is tg_retry.tg_call


def test_bot_identity_survives_bad_gateway(monkeypatch, caplog):
    from bot.services import bot_info
    monkeypatch.setattr(tg_retry, "TG_BACKOFF", (0.0, 0.0, 0.0))
    n = []

    class B:
        async def get_me(self):
            n.append(1)
            raise _bad_gateway()
    before = dict(bot_info._cache)
    with caplog.at_level("INFO"):
        out = asyncio.run(bot_info.refresh_bot_identity(B()))
    assert len(n) == 3 and out == before
    assert "using cached identity" in caplog.text and "Traceback" not in caplog.text and "TESTTOKEN" not in caplog.text
