"""Media proxy: real 206 ranges from a disk cache, one upstream fetch per file."""
import asyncio
import time

import httpx

from bot.api import routes_media as rm

DATA = bytes(range(256)) * 4096  # 1 MiB


def _app_client(monkeypatch, tmp_path, honor):
    calls = []

    async def handler(req):
        calls.append(req.headers.get("range"))
        rng = req.headers.get("range")
        if honor and rng:
            a, _, b = rng[6:].partition("-")
            a = int(a); b = int(b) if b else len(DATA) - 1
            body = DATA[a:b + 1]
            return httpx.Response(206, headers={"content-range": f"bytes {a}-{b}/{len(DATA)}"}, content=body)
        return httpx.Response(200, content=DATA)

    monkeypatch.setattr(rm, "_DISK_DIR", tmp_path)
    monkeypatch.setattr(rm, "_HTTP", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    rm._PATH_CACHE["BAACt"] = ("videos/t.mp4", time.time() + 999)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(rm.router)
    return app, calls


def _run(app, seq):
    async def go():
        out = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            for rng in seq:
                r = await c.get("/api/media/file/BAACt", headers={"Range": rng} if rng else {})
                out.append((r.status_code, r.headers.get("content-range"), r.content))
                await asyncio.sleep(0.05)
        return out
    return asyncio.run(go())


def test_ranges_when_upstream_ignores_range(monkeypatch, tmp_path):
    app, calls = _app_client(monkeypatch, tmp_path, honor=False)
    res = _run(app, ["bytes=0-1", "bytes=0-", "bytes=1000-1999", "bytes=-10", None])
    assert res[0][:2] == (206, f"bytes 0-1/{len(DATA)}") and res[0][2] == DATA[:2]
    assert res[1][0] == 206 and res[1][2] == DATA
    assert res[2][:2] == (206, f"bytes 1000-1999/{len(DATA)}") and res[2][2] == DATA[1000:2000]
    assert res[3][2] == DATA[-10:]
    assert res[4][0] == 200 and res[4][2] == DATA
    assert len([c for c in calls if c is None]) == 1  # one full fetch, then served from disk


def test_first_open_streams_and_caches(monkeypatch, tmp_path):
    app, calls = _app_client(monkeypatch, tmp_path, honor=True)
    res = _run(app, ["bytes=0-", "bytes=500-599"])
    assert res[0][0] == 206 and res[0][1] == f"bytes 0-{len(DATA)-1}/{len(DATA)}" and res[0][2] == DATA
    assert res[1][2] == DATA[500:600]
    assert len(calls) == 1  # second answered from the cache written during the first stream


def test_unsatisfiable_range(monkeypatch, tmp_path):
    app, _ = _app_client(monkeypatch, tmp_path, honor=False)
    res = _run(app, [None, f"bytes={len(DATA) + 5}-"])
    assert res[1][0] == 416
