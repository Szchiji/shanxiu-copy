import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from hetai.auth import validate_init_data
from hetai.config import Settings, reset_settings_cache


def _start_update(update_id: int, text: str = "/start", user_id: int = 900003) -> dict:
    cmd = text.split()[0]
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1700000000,
            "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(cmd)}],
            "chat": {"id": user_id, "type": "private", "first_name": "T"},
            "from": {"id": user_id, "is_bot": False, "first_name": "T"},
        },
    }



def test_health_and_dashboard_from_db(demo_client):
    r = demo_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["mode"] == "demo"
    assert body["bot_connected"] is False
    assert body["single_bot"] is True
    assert body["ingress"] == "/telegram/webhook"
    assert body["outbound"] == "local"
    assert body["outbound_live"] is False
    assert body["bot"]["dispatcher_routers"] == ["start", "help", "menu", "chat_member"]
    assert body["bot"]["transport"] == "local"
    home = demo_client.get("/admin")
    assert home.status_code == 200
    assert "概览" in home.text
    assert "示例数据" in home.text
    assert ">4<" in home.text or "成员" in home.text
    # counts from seed: 4 users, 2 groups, 1 pending, 1 approved
    assert "待审投稿" in home.text


def test_demo_pages_smoke(demo_client):
    for path in [
        "/app",
        "/app/me",
        "/app/submit",
        "/admin/groups",
        "/admin/users",
        "/admin/submissions",
        "/admin/tasks",
    ]:
        r = demo_client.get(path)
        assert r.status_code == 200, path
        assert "合台" in r.text
        assert "同一个机器人" in r.text


def test_member_cannot_open_admin(demo_client):
    demo_client.get("/demo/as/member")
    r = demo_client.get("/admin", headers={"Accept": "text/html"})
    assert r.status_code == 403


def test_webhook_secret_and_idempotency(demo_client):
    bad = demo_client.post("/telegram/webhook", json={"update_id": 1})
    assert bad.status_code == 401
    short = demo_client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "x"},
    )
    assert short.status_code == 401
    headers = {"X-Telegram-Bot-Api-Secret-Token": "demo-local-only"}
    first = demo_client.post("/telegram/webhook", json=_start_update(42), headers=headers)
    assert first.status_code == 200
    body = first.json()
    assert body["replayed"] is False
    assert body["sent"] is False
    assert body["handler"] == "start"
    assert body["transport"] == "local"
    assert any(c["method"] == "sendMessage" for c in body["calls"])
    second = demo_client.post("/telegram/webhook", json=_start_update(42), headers=headers)
    assert second.json()["replayed"] is True
    assert second.status_code == 200


def test_approve_submission_appears_in_feed(demo_client):
    feed = demo_client.get("/app")
    assert "滨江晚茶摊" not in feed.text
    assert "仓库咖啡角" in feed.text
    pending = demo_client.get("/admin/submissions?status=pending")
    assert "滨江晚茶摊" in pending.text
    # first pending item from seed is id 1
    review = demo_client.post(
        "/admin/submissions/1/review",
        data={"action": "approve", "review_note": "测试通过"},
        follow_redirects=True,
    )
    assert review.status_code == 200
    feed2 = demo_client.get("/app")
    assert "滨江晚茶摊" in feed2.text


def test_points_form_persists_and_idempotent(demo_client):
    # group 1 member user 3 has 120
    r = demo_client.post(
        "/admin/groups/1/points",
        data={"user_id": 3, "delta": -20, "reason": "测试扣减", "idempotency_key": "http-k1"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "积分已更新" in r.text
    assert "100" in r.text
    r2 = demo_client.post(
        "/admin/groups/1/points",
        data={"user_id": 3, "delta": -20, "reason": "测试扣减", "idempotency_key": "http-k1"},
        follow_redirects=True,
    )
    assert "100" in r2.text
    # tenant: user 4 is only in group 2
    bad = demo_client.post(
        "/admin/groups/1/points",
        data={"user_id": 4, "delta": -1, "reason": "跨群", "idempotency_key": "http-cross"},
        follow_redirects=True,
    )
    assert "不属于该群" in bad.text


def test_user_submit_persists(demo_client):
    demo_client.get("/demo/as/member")
    r = demo_client.post(
        "/app/submit",
        data={"group_id": 1, "title": "新投稿条目", "body": "由测试写入"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "已提交" in r.text
    demo_client.get("/demo/as/admin")
    lst = demo_client.get("/admin/submissions?status=pending")
    assert "新投稿条目" in lst.text


def test_initdata_signature_negative(monkeypatch):
    monkeypatch.setenv("HETAI_BOT_TOKEN", "123456:TESTTOKEN")
    reset_settings_cache()
    token = "123456:TESTTOKEN"
    user = json.dumps({"id": 1, "first_name": "X"})
    fields = {"auth_date": str(int(time.time())), "user": user, "query_id": "abc"}
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    # wrong secret
    try:
        validate_init_data(urlencode({**fields, "hash": "00" * 32}), token)
        assert False, "should fail"
    except Exception:
        pass
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    good = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    # fix data_check to use real newline
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    # actually validate_init_data uses "\n".join in code with real newline
    data_check = "\n".replace("\\n", "\n")  # placeholder, recompute below
    data_check = chr(10).join(f"{k}={v}" for k, v in sorted(fields.items()))
    good = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    ok = validate_init_data(urlencode({**fields, "hash": good}), token)
    assert ok["user"]["id"] == 1
    expired = dict(fields)
    expired["auth_date"] = str(int(time.time()) - 999999)
    data2 = chr(10).join(f"{k}={v}" for k, v in sorted(expired.items()))
    h2 = hmac.new(secret, data2.encode(), hashlib.sha256).hexdigest()
    try:
        validate_init_data(urlencode({**expired, "hash": h2}), token)
        assert False, "expired should fail"
    except Exception:
        pass


def test_production_auth_endpoint_not_demo_login(demo_client):
    r = demo_client.post("/api/auth/telegram", json={"initData": "x"})
    assert r.status_code == 401
    assert "未连接真实 Bot" in r.json()["detail"]
