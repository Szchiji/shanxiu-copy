"""公开预览模式：无口令拒启、未登录受保护、会话与角色限制。"""

from __future__ import annotations

import os
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from hetai.bot.runtime import reset_runtime
from hetai.config import Settings, reset_settings_cache


STRONG_ACCESS = "preview-access-secret-9f3c-OK"
STRONG_SESSION = "preview-session-secret-7a21-OK"


class _InputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        d = dict(attrs)
        name = d.get("name")
        if name:
            self.fields[name] = d.get("value") or ""


def _parse_inputs(html: str) -> dict[str, str]:
    p = _InputParser()
    p.feed(html)
    return p.fields


@pytest.fixture
def preview_client(tmp_path, monkeypatch):
    db = tmp_path / "hetai-preview-test.sqlite"
    monkeypatch.setenv("HETAI_MODE", "preview")
    monkeypatch.setenv("HETAI_HOST", "0.0.0.0")
    monkeypatch.setenv("HETAI_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("HETAI_PREVIEW_ACCESS_SECRET", STRONG_ACCESS)
    monkeypatch.setenv("HETAI_SESSION_SECRET", STRONG_SESSION)
    monkeypatch.setenv("HETAI_SEED_DEMO", "true")
    monkeypatch.setenv("HETAI_RUN_SCHEDULER", "false")
    monkeypatch.setenv("HETAI_WEBHOOK_SECRET", "preview-webhook-secret-only")
    monkeypatch.setenv("HETAI_PYTEST", "1")
    monkeypatch.delenv("HETAI_BOT_TOKEN", raising=False)
    reset_settings_cache()
    reset_runtime()
    from hetai.preview_auth import limiter
    limiter.clear("testserver")
    limiter._hits.clear()
    from hetai.app import create_app

    with TestClient(create_app()) as client:
        yield client
    reset_settings_cache()
    reset_runtime()


def _origin():
    return {"Origin": "http://testserver", "Referer": "http://testserver/preview/login"}


def login(client: TestClient, password: str = STRONG_ACCESS):
    page = client.get("/preview/login")
    assert page.status_code == 200
    fields = _parse_inputs(page.text)
    data = {
        "username": fields.get("username") or "preview",
        "password": password,
        "csrf_token": fields.get("csrf_token") or "",
        "next": fields.get("next") or "/app",
    }
    return client.post("/preview/login", data=data, headers=_origin(), follow_redirects=False)


def test_preview_boot_rejects_missing_or_weak_secret(monkeypatch):
    monkeypatch.setenv("HETAI_MODE", "preview")
    monkeypatch.setenv("HETAI_HOST", "0.0.0.0")
    monkeypatch.setenv("HETAI_SESSION_SECRET", STRONG_SESSION)
    monkeypatch.delenv("HETAI_PREVIEW_ACCESS_SECRET", raising=False)
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="PREVIEW_ACCESS_SECRET"):
        Settings().validate_boot()
    monkeypatch.setenv("HETAI_PREVIEW_ACCESS_SECRET", "short")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="PREVIEW_ACCESS_SECRET"):
        Settings().validate_boot()
    monkeypatch.setenv("HETAI_PREVIEW_ACCESS_SECRET", STRONG_ACCESS)
    monkeypatch.setenv("HETAI_SESSION_SECRET", "demo")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        Settings().validate_boot()
    reset_settings_cache()


def test_preview_uses_railway_port_fallback(monkeypatch):
    monkeypatch.setenv("HETAI_MODE", "preview")
    monkeypatch.setenv("HETAI_PREVIEW_ACCESS_SECRET", STRONG_ACCESS)
    monkeypatch.setenv("HETAI_SESSION_SECRET", STRONG_SESSION)
    monkeypatch.delenv("HETAI_PORT", raising=False)
    monkeypatch.setenv("PORT", "8080")
    reset_settings_cache()
    st = Settings()
    st.validate_boot()
    assert st.bind_host() == "0.0.0.0"
    assert st.bind_port() == 8080
    assert st.resolved_transport == "local"
    reset_settings_cache()


def test_health_is_minimal_and_public(preview_client):
    r = preview_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "app": "合台", "mode": "preview"}
    assert "bot" not in body
    assert "outbound" not in body
    assert "ingress" not in body


def test_unauthenticated_pages_and_apis_protected(preview_client):
    app = preview_client.get("/app", follow_redirects=False)
    assert app.status_code == 303
    assert "/preview/login" in app.headers["location"]
    admin = preview_client.get("/admin", follow_redirects=False)
    assert admin.status_code == 303
    api = preview_client.post("/api/auth/telegram", json={"initData": "x"}, headers=_origin())
    assert api.status_code in {401, 303, 403}
    hook = preview_client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={**_origin(), "X-Telegram-Bot-Api-Secret-Token": "preview-webhook-secret-only"},
    )
    assert hook.status_code in {401, 303, 403}
    switch = preview_client.get("/demo/as/admin", follow_redirects=False)
    assert switch.status_code == 303


def test_login_session_and_identity_roles(preview_client):
    denied = login(preview_client, "wrong-password-not-the-secret")
    assert denied.status_code == 303
    assert "error=auth" in denied.headers["location"]
    assert preview_client.get("/app", follow_redirects=False).status_code == 303

    ok = login(preview_client)
    assert ok.status_code == 303
    assert ok.headers["location"] in {"/app", "http://testserver/app"}
    home = preview_client.get("/app")
    assert home.status_code == 200
    assert "公开预览" in home.text
    assert "示例数据" in home.text
    assert "未连接真实 Telegram Bot" in home.text

    admin = preview_client.get("/admin")
    assert admin.status_code == 200
    preview_client.get("/demo/as/member")
    blocked = preview_client.get("/admin", headers={"Accept": "text/html"})
    assert blocked.status_code == 403

    # 示例写操作在登录且管理员身份下可用
    preview_client.get("/demo/as/admin")
    feed = preview_client.get("/admin/submissions?status=pending")
    assert feed.status_code == 200


def test_login_rate_limited(preview_client):
    for _ in range(5):
        r = login(preview_client, "wrong-password-not-the-secret")
        assert "error=auth" in r.headers.get("location", "")
    limited = login(preview_client, "wrong-password-not-the-secret")
    assert "error=rate" in limited.headers.get("location", "")


def test_mutation_without_origin_rejected(preview_client):
    assert login(preview_client).status_code == 303
    r = preview_client.post(
        "/app/submit",
        data={"group_id": "1", "title": "x", "body": "y"},
        follow_redirects=False,
    )
    assert r.status_code == 403
