"""Mini App static caching / gzip policy, card thumbnails, and asset cache-bust hygiene."""
import asyncio
import io
import re
import time
from pathlib import Path

import httpx
import pytest

from bot.api import routes_media as rm
from bot.api.static_cache import (
    IMMUTABLE, NO_STORE, REVALIDATE, MiniappCache, SelectiveGZip, cache_control_for, should_compress,
)

MINI = Path(__file__).resolve().parent.parent / "miniapp"


def test_cache_policy():
    assert cache_control_for("/app/go.html", "") == NO_STORE
    assert cache_control_for("/app/", "") == NO_STORE
    assert cache_control_for("/app", "") == NO_STORE
    assert cache_control_for("/app/home-ui.js", "v=20261031a") == IMMUTABLE
    assert cache_control_for("/app/styles.css", "x=1&v=abc") == IMMUTABLE
    assert cache_control_for("/app/home-ui.js", "") == REVALIDATE
    assert cache_control_for("/app/home-ui.js", "v=") == REVALIDATE
    assert cache_control_for("/api/home", "") is None
    assert cache_control_for("/app/pic.png", "") is None


def test_compress_selection():
    assert should_compress("/app/home-ui.js")
    assert should_compress("/app/go.html")
    assert should_compress("/api/home")
    assert not should_compress("/api/media/file/AgACx")
    assert not should_compress("/api/media/thumb/AgACx")
    assert not should_compress("/webhook")
    assert not should_compress("/app/x.png")


def _static_app():
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    app = FastAPI()
    app.add_middleware(MiniappCache)
    app.add_middleware(SelectiveGZip, minimum_size=1024)
    app.mount("/app", StaticFiles(directory=str(MINI), html=True), name="miniapp")
    return app


def test_static_headers_and_gzip():
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_static_app()), base_url="http://t") as c:
            r = await c.get("/app/home-ui.js?v=20261031a", headers={"Accept-Encoding": "gzip"})
            assert r.status_code == 200
            assert r.headers["cache-control"] == IMMUTABLE
            assert r.headers.get("content-encoding") == "gzip"
            assert "btnArea" in r.text  # httpx decodes gzip
            etag = r.headers.get("etag")
            r2 = await c.get("/app/home-ui.js", headers={"If-None-Match": etag})
            assert r2.status_code == 304 and r2.headers["cache-control"] == REVALIDATE
            r3 = await c.get("/app/go.html", headers={"Accept-Encoding": "gzip"})
            assert r3.headers["cache-control"] == NO_STORE and r3.headers.get("content-encoding") == "gzip"
    asyncio.run(go())


def test_every_local_asset_is_versioned_and_consistent():
    """Each asset is versioned consistently across pages; untouched CSS need not bump with JS."""
    by_asset = {}
    for page in ("go.html", "index.html"):
        html = (MINI / page).read_text("utf-8")
        refs = re.findall(r'(?:src|href)="\./([^"]+\.(?:js|css))(\?[^"]*)?"', html)
        assert refs, page
        versions = set()
        for name, q in refs:
            assert q and q.startswith("?v="), f"{page}: {name} has no ?v= cache-bust"
            versions.add(q)
            assert name not in by_asset or by_asset[name] == q, f"{name}: inconsistent cache version"
            by_asset[name] = q
            assert (MINI / name).is_file(), f"{page}: {name} missing"
        assert versions, page
        assert "app.bundle.js" in html
        bundle = (MINI / "app.bundle.js").read_text("utf-8")
        assert "swipe-back.js ----" in bundle and "net-cache.js ----" in bundle
        # all local scripts deferred, in the original order
        assert not re.search(r'<script src="\./[^"]+"></script>', html)


def _jpeg(w, h):
    from PIL import Image
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            px[x, y] = ((x * 7) % 255, (y * 3) % 255, (x * y) % 255)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=92)
    return out.getvalue()


def test_make_thumb():
    pytest.importorskip("PIL")
    from PIL import Image
    big = _jpeg(1280, 1706)
    t = rm.make_thumb(big, 480)
    assert t and len(t) < len(big)
    im = Image.open(io.BytesIO(t))
    assert im.size == (480, 640)
    assert rm.make_thumb(_jpeg(300, 400), 480) is None  # no upscaling
    assert rm.make_thumb(b"not an image", 480) is None
    assert rm.thumb_width(100) == 240 and rm.thumb_width(480) == 480 and rm.thumb_width(9999) == 720


def test_thumb_route(monkeypatch, tmp_path):
    pytest.importorskip("PIL")
    big = _jpeg(1280, 1706)
    calls = []

    async def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, content=big)

    monkeypatch.setattr(rm, "_DISK_DIR", tmp_path)
    monkeypatch.setattr(rm, "_HTTP", httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    class S:
        bot_token = "123:x"
        media_storage_chat_id = None
    monkeypatch.setattr(rm, "get_settings", lambda: S())
    rm._PATH_CACHE["AgACphoto1"] = ("photos/p.jpg", time.time() + 999)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(rm.router)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/media/thumb/AgACphoto1?w=480")
            assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
            assert "immutable" in r.headers["cache-control"]
            assert len(r.content) < len(big) / 2
            r2 = await c.get("/api/media/thumb/AgACphoto1?w=480")
            assert r2.content == r.content
            v = await c.get("/api/media/thumb/BAACvideo?w=480", follow_redirects=False)
            assert v.status_code == 307 and v.headers["location"] == "/api/media/file/BAACvideo"
            bad = await c.get("/api/media/thumb/..%2Fetc?w=480", follow_redirects=False)
            assert bad.status_code == 307
    asyncio.run(go())
    assert len(calls) == 1  # original fetched once; second request served from the thumb cache
