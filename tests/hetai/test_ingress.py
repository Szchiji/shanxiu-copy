"""Webhook → Dispatcher 端到端：本地无发送会话，无外部 HTTP。"""

from __future__ import annotations


import pytest
from fastapi import HTTPException
from starlette.requests import Request

from hetai.auth import resolve_actor
from hetai.bot.runtime import get_outbound_session
from hetai.config import Settings, reset_settings_cache


def _headers():
    return {"X-Telegram-Bot-Api-Secret-Token": "demo-local-only"}


def _command_update(update_id: int, text: str, user_id: int = 900003) -> dict:
    cmd = text.split()[0]
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1700000000,
            "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(cmd)}],
            "chat": {"id": user_id, "type": "private", "first_name": "T"},
            "from": {"id": user_id, "is_bot": False, "first_name": "T"},
        },
    }


def _callback_update(update_id: int, user_id: int = 900001) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cb-1",
            "data": "hetai:open",
            "from": {"id": user_id, "is_bot": False, "first_name": "Admin", "username": "admin"},
            "chat_instance": "1",
            "message": {
                "message_id": 2,
                "date": 1700000000,
                "text": "menu",
                "chat": {"id": user_id, "type": "private", "first_name": "Admin"},
                "from": {"id": 1, "is_bot": True, "first_name": "HetaiLocal"},
            },
        },
    }


def _webapp_urls(calls: list[dict]) -> list[str]:
    urls = []
    for call in calls:
        markup = (call.get("payload") or {}).get("reply_markup") or {}
        for row in markup.get("inline_keyboard") or []:
            for btn in row:
                web = btn.get("web_app") or {}
                if web.get("url"):
                    urls.append(web["url"])
    return urls


def test_start_help_menu_dispatch_local_no_http(demo_client):
    session = get_outbound_session()
    assert session is not None
    assert session.calls == []

    start = demo_client.post("/telegram/webhook", json=_command_update(1001, "/start"), headers=_headers())
    assert start.status_code == 200, start.text
    body = start.json()
    assert body["handler"] == "start"
    assert body["sent"] is False
    assert body["transport"] == "local"
    assert body["replayed"] is False
    urls = _webapp_urls(body["calls"])
    assert any("/app" in u for u in urls)
    assert any(c["method"] == "sendMessage" for c in body["calls"])

    help_r = demo_client.post("/telegram/webhook", json=_command_update(1002, "/help"), headers=_headers())
    assert help_r.status_code == 200, help_r.text
    help_body = help_r.json()
    assert help_body["handler"] == "help"
    assert help_body["sent"] is False
    assert any("/app" in u for u in _webapp_urls(help_body["calls"]))

    menu = demo_client.post(
        "/telegram/webhook",
        json=_callback_update(1003, user_id=900001),
        headers=_headers(),
    )
    assert menu.status_code == 200, menu.text
    menu_body = menu.json()
    assert menu_body["handler"] == "menu"
    assert menu_body["sent"] is False
    methods = [c["method"] for c in menu_body["calls"]]
    assert "answerCallbackQuery" in methods
    assert "sendMessage" in methods
    assert any("/app" in u for u in _webapp_urls(menu_body["calls"]))

    # 本地会话从未配置 HTTP 客户端；调用列表仍来自 LocalNoSendSession（可经出站守卫包装）。
    from hetai.bot.guard import GuardedSession
    from hetai.bot.session import LocalNoSendSession

    session = get_outbound_session()
    inner = session.inner if isinstance(session, GuardedSession) else session
    assert isinstance(inner, LocalNoSendSession)


def test_start_idempotent_does_not_resend(demo_client):
    headers = _headers()
    first = demo_client.post("/telegram/webhook", json=_command_update(2001, "/start"), headers=headers)
    assert first.status_code == 200
    n_calls = len(first.json()["calls"])
    session = get_outbound_session()
    total_after_first = len(session.calls)
    second = demo_client.post("/telegram/webhook", json=_command_update(2001, "/start"), headers=headers)
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["sent"] is False
    assert len(session.calls) == total_after_first
    assert n_calls >= 1


def test_failed_processing_can_retry(demo_client, monkeypatch):
    from hetai.bot import ingress

    original = ingress.dispatch_update
    state = {"n": 0}

    async def flaky(payload, settings):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("simulated-dispatch-failure")
        return await original(payload, settings)

    monkeypatch.setattr(ingress, "dispatch_update", flaky)
    headers = _headers()
    first = demo_client.post("/telegram/webhook", json=_command_update(3001, "/start"), headers=headers)
    assert first.status_code == 500
    assert "可重试" in first.json()["detail"]

    # 若被误标 processed，第二次会 replayed=True 且不再走 handler。
    retry = demo_client.post("/telegram/webhook", json=_command_update(3001, "/start"), headers=headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["replayed"] is False
    assert retry.json()["handler"] == "start"
    assert retry.json()["sent"] is False
    assert state["n"] == 2


@pytest.mark.asyncio
async def test_production_ignores_demo_cookie_and_query(monkeypatch):
    monkeypatch.setenv("HETAI_MODE", "production")
    monkeypatch.setenv("HETAI_SESSION_SECRET", "a-sufficiently-long-session-secret")
    monkeypatch.setenv("HETAI_BOT_TOKEN", "123:TEST")
    monkeypatch.delenv("HETAI_PYTEST", raising=False)
    reset_settings_cache()
    settings = Settings()
    assert settings.is_demo is False

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/app",
            "raw_path": b"/app",
            "query_string": b"as=admin",
            "headers": [
                (b"host", b"example.com"),
                (b"cookie", b"hetai_demo_as=admin"),
            ],
            "client": ("8.8.8.8", 443),
            "server": ("example.com", 443),
        },
        receive,
    )

    class _ForbiddenSession:
        async def execute(self, *args, **kwargs):
            raise AssertionError("生产路径在校验令牌前不应查询演示身份")

    with pytest.raises(HTTPException) as exc:
        await resolve_actor(request, settings, _ForbiddenSession())
    assert exc.value.status_code == 401
    assert "登录" in str(exc.value.detail)
    reset_settings_cache()


def test_secrets_equal_missing_and_length():
    from hetai.bot.ingress import secrets_equal

    assert secrets_equal("", "demo-local-only") is False
    assert secrets_equal("x", "demo-local-only") is False
    assert secrets_equal("demo-local-only", "demo-local-only") is True
    assert secrets_equal("demo-local-onlx", "demo-local-only") is False
