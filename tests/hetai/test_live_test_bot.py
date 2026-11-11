"""Dedicated test-bot isolation: real Telegram identity never inherits fixture/super by default."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from hetai.auth import validate_init_data
from hetai.bot.guard import outbound_allowed, set_update_chat_id, reset_update_chat_id
from hetai.bot.runtime import reset_runtime
from hetai.config import Settings, reset_settings_cache

OWNER_ID = 777000111
OTHER_ID = 555000222
BOT_TOKEN = "123456789:FAKE-TEST-TOKEN-NOT-LIVE"
WEBHOOK_SECRET = "preview-webhook-secret-only-OK24"
PREVIEW_ACCESS = "preview-access-secret-9f3c-OK"
SESSION_SECRET = "preview-session-secret-7a21-OK"


def _sign_init_data(fields: dict, token: str) -> str:
    payload = {k: v for k, v in fields.items() if k != "hash"}
    data = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    payload["hash"] = digest
    return urlencode(payload)


@pytest.fixture
def live_test_client(tmp_path, monkeypatch):
    db = tmp_path / "hetai-live-test.sqlite"
    monkeypatch.setenv("HETAI_MODE", "preview")
    monkeypatch.setenv("HETAI_HOST", "0.0.0.0")
    monkeypatch.setenv("HETAI_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("HETAI_PREVIEW_ACCESS_SECRET", PREVIEW_ACCESS)
    monkeypatch.setenv("HETAI_SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv("HETAI_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("HETAI_SEED_DEMO", "true")
    monkeypatch.setenv("HETAI_RUN_SCHEDULER", "false")
    monkeypatch.setenv("HETAI_PYTEST", "1")
    monkeypatch.setenv("HETAI_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("HETAI_BOT_TRANSPORT", "dry-run")
    monkeypatch.setenv("HETAI_WEBAPP_URL", "https://example.invalid")
    monkeypatch.setenv("HETAI_OWNER_TELEGRAM_ID", str(OWNER_ID))
    monkeypatch.setenv("HETAI_BOT_USERNAME", "example_bot")
    monkeypatch.delenv("HETAI_ADMIN_IDS", raising=False)
    monkeypatch.delenv("HETAI_TEST_CHAT_IDS", raising=False)
    reset_settings_cache()
    reset_runtime()
    from hetai.app import create_app

    with TestClient(create_app()) as client:
        yield client
    reset_settings_cache()
    reset_runtime()


def _origin():
    return {"Origin": "http://testserver", "Referer": "http://testserver/tg"}


def test_owner_id_is_not_a_committed_default():
    from hetai.config import Settings as SettingsModel
    field = SettingsModel.model_fields["owner_telegram_id"]
    assert field.default == ""


def test_initdata_rejects_future_auth_date():
    user = json.dumps({"id": OWNER_ID, "first_name": "Owner"})
    future = {"auth_date": str(int(time.time()) + 3600), "user": user}
    signed = _sign_init_data(future, BOT_TOKEN)
    with pytest.raises(Exception):
        validate_init_data(signed, BOT_TOKEN)


def test_tg_gate_is_public_and_not_fixtures(live_test_client):
    r = live_test_client.get("/tg", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 401
    assert "请在 Telegram 中打开" in r.text or "请从 Telegram 打开" in r.text or "需要在 Telegram" in r.text
    assert "陈管理" not in r.text
    assert "示例身份（夹具）" not in r.text
    assert "/preview/login" not in (r.headers.get("location") or "")


def test_preview_password_cannot_open_tg_as_fixture(live_test_client):
    page = live_test_client.get("/preview/login")
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.fields = {}

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if tag == "input" and d.get("name"):
                self.fields[d["name"]] = d.get("value") or ""

    parser = P()
    parser.feed(page.text)
    live_test_client.post(
        "/preview/login",
        data={"password": PREVIEW_ACCESS, "csrf_token": parser.fields.get("csrf_token") or "", "next": "/app"},
        headers={"Origin": "http://testserver", "Referer": "http://testserver/preview/login"},
        follow_redirects=False,
    )
    home = live_test_client.get("/app")
    assert home.status_code == 200
    assert "陈管理" in home.text
    tg = live_test_client.get("/tg", headers={"Accept": "text/html"}, follow_redirects=False)
    assert tg.status_code == 401
    assert "陈管理" not in tg.text
    switch = live_test_client.get("/demo/as/admin?next=/tg", follow_redirects=False)
    assert switch.status_code == 303
    assert "/tg" not in (switch.headers.get("location") or "")


def test_real_initdata_owner_and_member_isolation(live_test_client):
    owner_data = _sign_init_data(
        {"auth_date": str(int(time.time())), "user": json.dumps({"id": OWNER_ID, "first_name": "真实主人"})},
        BOT_TOKEN,
    )
    member_data = _sign_init_data(
        {"auth_date": str(int(time.time())), "user": json.dumps({"id": OTHER_ID, "first_name": "普通用户"})},
        BOT_TOKEN,
    )
    fixture_data = _sign_init_data(
        {"auth_date": str(int(time.time())), "user": json.dumps({"id": 900001, "first_name": "夹具"})},
        BOT_TOKEN,
    )
    denied = live_test_client.post("/api/auth/telegram", json={"initData": fixture_data}, headers=_origin())
    assert denied.status_code == 401

    owner = live_test_client.post("/api/auth/telegram", json={"initData": owner_data}, headers=_origin())
    assert owner.status_code == 200
    body = owner.json()
    assert body["telegram_id"] == OWNER_ID
    assert body["is_owner"] is True
    assert "token" not in body
    me = live_test_client.get("/tg/me", headers={"Accept": "text/html"})
    assert me.status_code == 200
    assert "真实主人" in me.text
    assert "super" in me.text
    assert "示例身份（夹具）" not in me.text
    assert "陈管理" not in me.text
    assert "示例群 · 华东运营" not in me.text

    # group query hint must not grant a fixture or unknown group
    spoof = live_test_client.get("/tg?group=1", headers={"Accept": "text/html"})
    assert spoof.status_code == 403
    assert "示例群 · 华东运营" not in spoof.text

    other = TestClient(live_test_client.app)
    member = other.post("/api/auth/telegram", json={"initData": member_data}, headers=_origin())
    assert member.status_code == 200
    assert member.json()["is_owner"] is False
    member_me = other.get("/tg/me", headers={"Accept": "text/html"})
    assert member_me.status_code == 200
    assert "普通用户" in member_me.text
    assert '<span class="pill">super</span>' not in member_me.text
    assert "示例身份（夹具）" not in member_me.text


def test_startapp_does_not_grant_group(live_test_client):
    data = _sign_init_data(
        {
            "auth_date": str(int(time.time())),
            "user": json.dumps({"id": OTHER_ID, "first_name": "普通用户"}),
            "start_param": "-100900002",
        },
        BOT_TOKEN,
    )
    r = live_test_client.post("/api/auth/telegram", json={"initData": data}, headers=_origin())
    assert r.status_code == 200
    assert r.json()["context_hint"] is None
    feed = live_test_client.get("/tg?startapp=-100900002", headers={"Accept": "text/html"})
    assert feed.status_code in {200, 403}
    assert "示例群 · 测试交流" not in feed.text


def test_getchatmember_required_for_chat_hint(live_test_client, monkeypatch):
    from hetai.services import telegram_groups

    async def fake_member(chat_id, user_id):
        assert chat_id == -100555
        assert user_id == OTHER_ID
        return {"status": "member", "chat": {"id": chat_id, "title": "真实测试群"}}

    telegram_groups.set_get_chat_member_impl(fake_member)
    try:
        data = _sign_init_data(
            {
                "auth_date": str(int(time.time())),
                "user": json.dumps({"id": OTHER_ID, "first_name": "普通用户"}),
                "chat": json.dumps({"id": -100555, "title": "真实测试群", "type": "supergroup"}),
            },
            BOT_TOKEN,
        )
        r = live_test_client.post("/api/auth/telegram", json={"initData": data}, headers=_origin())
        assert r.status_code == 200
        assert r.json()["context_hint"]["title"] == "真实测试群"
        page = live_test_client.get("/tg?chat_id=-100555", headers={"Accept": "text/html"})
        assert page.status_code == 200
        assert "真实测试群" in page.text
        assert "示例群 · 华东运营" not in page.text
    finally:
        telegram_groups.set_get_chat_member_impl(None)


def test_outbound_guard_fail_closed():
    st = Settings(
        mode="preview",
        bot_transport="dry-run",
        preview_access_secret=PREVIEW_ACCESS,
        session_secret=SESSION_SECRET,
        webhook_secret=WEBHOOK_SECRET,
        bot_token=BOT_TOKEN,
        owner_telegram_id=str(OWNER_ID),
        test_chat_ids="",
    )
    token = set_update_chat_id(OWNER_ID)
    try:
        ok, reason = outbound_allowed(st, "sendMessage", OWNER_ID)
        assert ok and reason == "update_reply"
        blocked, why = outbound_allowed(st, "sendMessage", 999)
        assert not blocked and why == "chat_not_in_current_update"
        mod, mwhy = outbound_allowed(st, "banChatMember", -100555)
        assert not mod and mwhy == "moderation_not_authorized"
        assert outbound_allowed(st, "getUpdates", None)[0] is False
        assert outbound_allowed(st, "setWebhook", None)[0] is False
    finally:
        reset_update_chat_id(token)
    st2 = Settings(
        mode="preview",
        bot_transport="dry-run",
        preview_access_secret=PREVIEW_ACCESS,
        session_secret=SESSION_SECRET,
        webhook_secret=WEBHOOK_SECRET,
        bot_token=BOT_TOKEN,
        owner_telegram_id=str(OWNER_ID),
        test_chat_ids="-100555",
    )
    ok, _ = outbound_allowed(st2, "banChatMember", -100555)
    assert ok
    no, _ = outbound_allowed(st2, "banChatMember", -100999)
    assert not no


def test_preview_live_boot_requires_https_and_keeps_secret_policy(monkeypatch):
    monkeypatch.setenv("HETAI_MODE", "preview")
    monkeypatch.setenv("HETAI_PREVIEW_ACCESS_SECRET", PREVIEW_ACCESS)
    monkeypatch.setenv("HETAI_SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv("HETAI_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("HETAI_BOT_TRANSPORT", "live")
    monkeypatch.setenv("HETAI_WEBHOOK_SECRET", "short")
    monkeypatch.setenv("HETAI_WEBAPP_URL", "http://insecure.example")
    reset_settings_cache()
    st = Settings()
    with pytest.raises(RuntimeError):
        st.validate_boot()
    monkeypatch.setenv("HETAI_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("HETAI_WEBAPP_URL", "https://web-production-a4e487e5.up.railway.app")
    reset_settings_cache()
    Settings().validate_boot()
    reset_settings_cache()


def test_mini_app_url_for_live_preview(monkeypatch):
    monkeypatch.setenv("HETAI_MODE", "preview")
    monkeypatch.setenv("HETAI_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("HETAI_BOT_TRANSPORT", "live")
    monkeypatch.setenv("HETAI_WEBAPP_URL", "https://web-production-a4e487e5.up.railway.app")
    monkeypatch.setenv("HETAI_PREVIEW_ACCESS_SECRET", PREVIEW_ACCESS)
    monkeypatch.setenv("HETAI_SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv("HETAI_WEBHOOK_SECRET", WEBHOOK_SECRET)
    reset_settings_cache()
    assert Settings().mini_app_url() == "https://web-production-a4e487e5.up.railway.app/tg"
    reset_settings_cache()
