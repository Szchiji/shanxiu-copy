from bot.services.extras_clean import clean_user_extras
from bot.services import home_feed
from bot.api.upload_guard import UploadSizeGuard
from bot.api import routes_media
import asyncio


def test_extras_clean():
    out = clean_user_extras({
        "微信": "abc", "_bc_mid": "1", "<img src=x onerror=alert(1)>": "x",
        "电话/微信": "<b>1</b>", "k" * 17: "v", "空": "  ", "n": {"a": 1}, "联系": 123,
    })
    assert out == {"微信": "abc", "电话/微信": "<b>1</b>", "联系": "123"}
    assert len(clean_user_extras({str(i): "v" for i in range(50)})) == 24


def test_merge_ops_keeps_extra_admins():
    ops = home_feed.merge_ops({"extra_admin_ids": ["123", 456, "x", 123]})
    assert ops["extra_admin_ids"] == [123, 456]
    assert home_feed.merge_ops({})["extra_admin_ids"] == []


def test_upload_whitelist():
    ok = routes_media._allowed_upload
    assert ok("a.jpg", "image/jpeg") and ok("a.mp4", "video/mp4") and ok("a.mov", "application/octet-stream")
    assert not ok("a.svg", "image/svg+xml") and not ok("a.html", "text/html") and not ok("a.exe", "")


def test_rate_limit():
    routes_media._RATE.clear()
    assert routes_media._rate_ok(7, 60)
    assert not routes_media._rate_ok(7, 1)


def test_upload_guard_rejects_big_body():
    sent = []
    called = []

    async def app(scope, receive, send):
        called.append(1)
        while True:
            m = await receive()
            if not m.get("more_body"):
                break

    async def send(m):
        sent.append(m)

    chunks = [{"type": "http.request", "body": b"x" * 600, "more_body": True}] * 3

    async def receive():
        return chunks.pop() if chunks else {"type": "http.request", "body": b""}

    g = UploadSizeGuard(app, max_body=1000)
    scope = {"type": "http", "path": "/api/media/upload", "method": "POST", "headers": []}
    asyncio.run(g(scope, receive, send))
    assert sent[0]["status"] == 413
    sent.clear()
    scope2 = dict(scope, headers=[(b"content-length", b"5000")])
    asyncio.run(g(scope2, receive, send))
    assert sent[0]["status"] == 413


def test_startup_maintenance_uses_live_scheduler(monkeypatch):
    from bot.services import bot_info

    class FakeSched:
        running = True
        def __init__(self):
            self.ids = []
        def add_job(self, fn, *a, id=None, **k):
            self.ids.append(id)

    async def noop():
        return None
    monkeypatch.setattr(bot_info, "job_expiry_nudge", noop)
    sch = FakeSched()
    asyncio.run(bot_info.startup_maintenance(sch))
    assert {"open_shift", "shift_timeout", "expiry_nudge"} <= set(sch.ids)
