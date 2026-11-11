import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from sqlalchemy import select

from hetai.auth import validate_init_data
from hetai.config import reset_settings_cache


def _as(client, key):
    client.get(f"/demo/as/{key}")


def test_directory_filters_to_accessible_groups(demo_client):
    feed = demo_client.get("/app")
    assert feed.status_code == 200
    assert "仓库咖啡角" in feed.text
    _as(demo_client, "guest")
    guest_feed = demo_client.get("/app?view=personal")
    assert guest_feed.status_code == 200
    assert "仓库咖啡角" not in guest_feed.text
    _as(demo_client, "member")
    member_feed = demo_client.get("/app?view=personal")
    assert "仓库咖啡角" in member_feed.text


def test_denied_cross_group_reads_and_spoofed_context(demo_client):
    _as(demo_client, "member")
    assert demo_client.get("/app?group=2").status_code == 403
    assert demo_client.get("/app?chat_id=-100900002").status_code == 403
    assert demo_client.get("/app?startapp=-100900002").status_code == 403
    # chat_instance must not be treated as a group grant
    r = demo_client.get("/app?chat_instance=-100900002")
    assert r.status_code == 200
    assert "仓库咖啡角" in r.text
    # published listing in a group the member does not belong to is denied
    # (none published in group 2; approved item 2 belongs to group 1)
    _as(demo_client, "guest")
    denied = demo_client.get("/app/listings/2")
    assert denied.status_code == 403
    _as(demo_client, "member")
    ok = demo_client.get("/app/listings/2")
    assert ok.status_code == 200
    assert "仓库咖啡角" in ok.text


def test_member_and_operator_and_super_views(demo_client):
    _as(demo_client, "member")
    assert demo_client.get("/app/inbox").status_code == 403
    assert demo_client.get("/admin").status_code == 403
    submit = demo_client.get("/app/submit")
    assert submit.status_code == 200
    assert "示例群 · 华东运营" in submit.text
    assert "示例群 · 测试交流" not in submit.text

    _as(demo_client, "operator")
    inbox = demo_client.get("/app/inbox")
    assert inbox.status_code == 200
    assert "滨江晚茶摊" in inbox.text
    rejected = demo_client.get("/app/inbox?status=rejected")
    assert "重复广告" not in rejected.text
    assert demo_client.get("/admin/users").status_code == 403
    assert demo_client.get("/admin/tasks").status_code == 403
    # operator is only a group admin: global tools home redirects to inbox
    admin = demo_client.get("/admin", follow_redirects=False)
    assert admin.status_code == 303

    _as(demo_client, "admin")
    tools = demo_client.get("/admin?view=global")
    assert tools.status_code == 200
    assert "概览" in tools.text
    assert "待审投稿" in tools.text
    all_rejected = demo_client.get("/app/inbox?view=global&status=rejected")
    assert "重复广告" in all_rejected.text
    _as(demo_client, "operator")
    assert demo_client.get("/app?view=global").status_code == 403


def test_denied_cross_group_writes(demo_client):
    _as(demo_client, "member")
    bad = demo_client.post(
        "/app/submit",
        data={"group_id": 2, "title": "跨群投稿", "body": "不应写入"},
        follow_redirects=True,
    )
    assert bad.status_code == 200
    assert "只能向自己所在的群投稿" in bad.text
    _as(demo_client, "admin")
    listing = demo_client.get("/admin/submissions?status=pending")
    assert "跨群投稿" not in listing.text

    _as(demo_client, "operator")
    reject = demo_client.post(
        "/admin/submissions/3/review",
        data={"action": "approve", "review_note": "越权"},
        follow_redirects=True,
    )
    assert "没有该群的审核权限" in reject.text
    still = demo_client.get("/app/inbox?status=rejected")
    # operator cannot even list group 2
    assert "重复广告" not in still.text


def test_approval_reward_idempotent_and_credit_untouched(demo_client):
    _as(demo_client, "member")
    before = demo_client.get("/app/me")
    assert "430" in before.text  # credit
    assert "120" in before.text  # group points

    _as(demo_client, "admin")
    first = demo_client.post(
        "/admin/submissions/1/review",
        data={"action": "approve", "review_note": "测试通过"},
        follow_redirects=True,
    )
    assert first.status_code == 200
    assert "审核已保存" in first.text
    assert "积分变动 10" in first.text
    feed = demo_client.get("/app")
    assert "滨江晚茶摊" in feed.text

    _as(demo_client, "member")
    after = demo_client.get("/app/me")
    assert "430" in after.text
    assert "130" in after.text
    assert "示例规则：目录投稿通过后奖励 10 积分" in after.text

    _as(demo_client, "admin")
    second = demo_client.post(
        "/admin/submissions/1/review",
        data={"action": "approve", "review_note": "重复通过"},
        follow_redirects=True,
    )
    assert second.status_code == 200
    assert "重复提交未重复奖励" in second.text
    _as(demo_client, "member")
    again = demo_client.get("/app/me")
    assert "430" in again.text
    assert "130" in again.text
    ledger = demo_client.get("/app/groups/1/ledger")
    assert ledger.text.count("目录投稿通过后奖励 10 积分") == 1


def test_zero_reward_default_on_unconfigured_group(demo_client):
    _as(demo_client, "guest")
    posted = demo_client.post(
        "/app/submit",
        data={"group_id": 2, "title": "交流群投稿", "body": "用于验证默认零奖励"},
        follow_redirects=True,
    )
    assert "已提交" in posted.text
    before = demo_client.get("/app/me")
    assert "200" in before.text
    assert "15" in before.text

    inbox = demo_client.get("/demo/as/admin")
    pending = demo_client.get("/app/inbox?status=pending")
    assert "交流群投稿" in pending.text
    # seed pending is id 1; the new item is the latest in group 2. Follow the inbox link.
    import re
    match = re.search(r'href="[^"]*/app/inbox/(\d+)[^"]*">交流群投稿', pending.text)
    assert match, pending.text
    item_id = match.group(1)
    reviewed = demo_client.post(
        f"/admin/submissions/{item_id}/review",
        data={"action": "approve", "review_note": "通过但不奖励"},
        follow_redirects=True,
    )
    assert reviewed.status_code == 200
    assert "积分变动" not in reviewed.text
    _as(demo_client, "guest")
    after = demo_client.get("/app/me")
    assert "200" in after.text
    assert "15" in after.text
    assert "交流群投稿" in after.text
    assert after.text.count("通过目录投稿奖励") == 0


def test_reward_failure_rolls_back_publish(demo_client, monkeypatch):
    from hetai.services import review as review_mod

    async def boom(*args, **kwargs):
        raise ValueError("simulated reward failure")

    monkeypatch.setattr(review_mod, "_award_if_configured", boom)
    resp = demo_client.post(
        "/admin/submissions/1/review",
        data={"action": "approve", "review_note": "应回滚"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "无法完成奖励入账" in resp.text or "审核未生效" in resp.text
    pending = demo_client.get("/app/inbox?status=pending")
    assert "滨江晚茶摊" in pending.text
    feed = demo_client.get("/app")
    assert "滨江晚茶摊" not in feed.text
    _as(demo_client, "member")
    me = demo_client.get("/app/me")
    assert "120" in me.text
    ledger = demo_client.get("/app/groups/1/ledger")
    assert "目录投稿通过后奖励 10 积分" not in ledger.text


def test_initdata_missing_invalid_expired():
    token = "123456:TESTTOKEN"
    user = json.dumps({"id": 7, "first_name": "X"})
    try:
        validate_init_data("", token)
        assert False, "missing initData should fail"
    except Exception:
        pass
    fields = {"auth_date": str(int(time.time())), "user": user, "query_id": "abc"}
    try:
        validate_init_data(urlencode({**fields, "hash": "00" * 32}), token)
        assert False, "bad signature should fail"
    except Exception:
        pass
    # missing auth_date
    no_date = {"user": user, "query_id": "abc"}
    data = chr(10).join(f"{k}={v}" for k, v in sorted(no_date.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    try:
        validate_init_data(urlencode({**no_date, "hash": digest}), token)
        assert False, "missing auth_date should fail"
    except Exception:
        pass
    expired = {"auth_date": str(int(time.time()) - 999999), "user": user, "query_id": "abc"}
    data2 = chr(10).join(f"{k}={v}" for k, v in sorted(expired.items()))
    digest2 = hmac.new(secret, data2.encode(), hashlib.sha256).hexdigest()
    try:
        validate_init_data(urlencode({**expired, "hash": digest2}), token)
        assert False, "expired should fail"
    except Exception:
        pass


def test_authenticated_403_keeps_actor_and_does_not_leak(demo_client):
    html = {"Accept": "text/html"}
    _as(demo_client, "operator")
    denied = demo_client.get("/app?group=2", headers=html)
    assert denied.status_code == 403
    assert "无权访问该群" in denied.text
    assert '<span class="who-name">李运营</span>' in denied.text
    assert '<span class="pill">operator</span>' in denied.text
    assert '<span class="who-name">访客</span>' not in denied.text
    assert '<span class="pill">anonymous</span>' not in denied.text
    assert "示例身份（夹具）" in denied.text
    assert "示例运营（仅华东运营群）" in denied.text
    assert "个人视图" in denied.text
    assert "待办" in denied.text
    assert "示例群 · 华东运营" in denied.text
    assert "示例群 · 测试交流" not in denied.text
    assert "-100900002" not in denied.text
    assert denied.headers.get("location") is None

    _as(demo_client, "member")
    member_denied = demo_client.get("/app?group=2", headers=html)
    assert member_denied.status_code == 403
    assert '<span class="who-name">王成员</span>' in member_denied.text
    assert '<span class="who-name">访客</span>' not in member_denied.text
    assert '<span class="pill">anonymous</span>' not in member_denied.text
    assert "示例群 · 测试交流" not in member_denied.text
    assert ">待办<" not in member_denied.text
    assert "目录" in member_denied.text

    admin_denied = demo_client.get("/admin", headers=html)
    assert admin_denied.status_code == 403
    assert '<span class="who-name">王成员</span>' in admin_denied.text
    assert "当前示例身份没有管理权限" in admin_denied.text
    assert '<span class="who-name">访客</span>' not in admin_denied.text
    assert '<span class="pill">anonymous</span>' not in admin_denied.text
    assert "示例身份（夹具）" in admin_denied.text
    assert admin_denied.headers.get("location") is None


def test_stylesheet_and_templates_desktop_shell():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    css = (root / "hetai/web/static/hetai.css").read_text()
    assert re.search(r"\.tabbar\s*\{[^}]*position:\s*fixed", css) is None
    widths = re.findall(r"\.shell\s*\{[^}]*max-width:\s*([^;]+)", css)
    assert widths, "shell max-width missing"
    last = widths[-1].strip()
    if last.startswith("var("):
        # resolve --shell-max
        m = re.search(r"--shell-max:\s*([^;]+)", css)
        assert m, "shell max-width variable missing"
        last = m.group(1).strip()
    if last.endswith("rem"):
        assert float(last[:-3]) >= 60
    elif last.endswith("px"):
        assert float(last[:-2]) >= 960
    else:
        raise AssertionError(f"unexpected shell max-width: {last}")
    assert 'input[type="checkbox"]' in css
    assert "label.check" in css
    assert "context-meta" in css
    html = (root / "hetai/web/templates/base.html").read_text()
    assert "shell-body" in html
    assert "context-meta" in html
    settings = (root / "hetai/web/templates/app_group_settings.html").read_text()
    assert 'class="check"' in settings
