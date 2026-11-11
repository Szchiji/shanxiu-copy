"""首页多层筛选 / 距离 / 搜索 + 频道推送按钮与同步 + 结构化评价与私密凭证（YYCJ_TEST_PG）。"""
import asyncio
import os
import uuid

import pytest

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


class Msg:
    def __init__(self, mid):
        self.message_id = mid


class FakeBot:
    def __init__(self):
        self.calls = []
        self.n = 500

    async def _rec(self, kind, chat, **kw):
        self.n += 1
        self.calls.append((kind, chat, kw))
        return Msg(self.n)

    async def send_photo(self, chat, photo, caption=None, reply_markup=None, **kw):
        return await self._rec("photo", chat, caption=caption, kb=reply_markup)

    async def send_video(self, chat, video, caption=None, reply_markup=None, **kw):
        return await self._rec("video", chat, caption=caption, kb=reply_markup)

    async def send_media_group(self, chat, media):
        self.n += 1
        self.calls.append(("album", chat, {"n": len(media), "caps": [getattr(m, "caption", None) for m in media]}))
        return [Msg(self.n)]

    async def send_message(self, chat, text, reply_markup=None, **kw):
        return await self._rec("text", chat, text=text, kb=reply_markup)

    async def edit_message_caption(self, chat_id, message_id, caption, reply_markup=None):
        return await self._rec("edit_caption", chat_id, mid=message_id, text=caption, kb=reply_markup)

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None, **kw):
        return await self._rec("edit_text", chat_id, mid=message_id, text=text, kb=reply_markup)

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        return await self._rec("edit_kb", chat_id, mid=message_id, kb=reply_markup)

    async def get_chat(self, chat):
        return None


def _urls(kb):
    return [b.url for row in (kb.inline_keyboard if kb else []) for b in row]


def test_filters_channel_reviews():
    os.environ["DATABASE_URL"] = PG

    async def run():
        import httpx
        import bot.main as m
        from bot import db
        from bot.api.telegram_webapp import issue_access_token
        from bot.config import get_settings
        from bot.db import session_scope
        from bot.models import Lamp, User
        from bot.services import bot_info, broadcast, extras_store, home_service, listing_ops
        db._engine = None
        await db.connect_db()
        await db._ensure_columns(db._engine)
        tok = lambda uid: {"Authorization": "Bearer " + issue_access_token(uid, get_settings().bot_token)}
        T, G = 8801, 8811
        ids = {k: str(uuid.uuid4()) for k in ("a", "b", "c", "d", "e")}
        async with session_scope() as s:
            s.add(User(user_id=T, role="teacher"))
            s.add(User(user_id=G, role="guest"))
            s.add(Lamp(lamp_id=ids["a"], user_id=T, title="小南", city="惠州", district="惠城区", price=8001500, price_text="800P 1500PP", tags=["#新人", "温柔"], status="active"))
            s.add(Lamp(lamp_id=ids["b"], user_id=T, title="阿福", city="惠州", district="惠阳", price=500, price_text="500/次", tags=["温柔"], status="active"))
            s.add(Lamp(lamp_id=ids["c"], user_id=T, title="龙姐", city="惠州", district="惠东", price_text="2000", tags=["御姐"], status="active"))
            s.add(Lamp(lamp_id=ids["d"], user_id=T, title="宝宝", city="惠州", district="博罗", price_text="面议", tags=[], status="active",
                       approx_lat=23.1012345, approx_lng=114.4198765))
            s.add(Lamp(lamp_id=ids["e"], user_id=T, title="广州的", city="广州", district="天河", price_text="900", tags=["新人"], status="active"))
        from bot.services.search_service import backfill_prices
        assert await backfill_prices() >= 3   # "800P 1500PP" → 800, "2000" → 2000, "900" → 900
        await home_service.update_settings(enabled_cities=["深圳", "广州", "惠州"], ops_config={"broadcast_channel": "@yy_test", "city_overrides": {}})
        bot_info._cache.update({"username": "MoonTestBot", "ts": 9e12})
        fake = FakeBot()
        real = m.bot
        m.bot = fake
        m._ready = True
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://t") as c:
                h = tok(G)
                get = lambda qs: c.get("/api/home?city=惠州&limit=20&" + qs, headers=h)
                f = (await c.get("/api/home/facets?city=惠州", headers=h)).json()
                assert f["total"] == 4 and f["unpriced"] == 1
                assert {t["tag"] for t in f["tags"]} >= {"新人", "温柔", "御姐"}
                assert {g["key"] for g in f["geo_districts"]} == {"惠城", "惠阳", "惠东", "博罗"}
                titles = lambda r: sorted(x["title"] for x in r.json()["items"])
                # 价格档 (strict: 面议 excluded) + tags (any of) + combined with district
                assert titles(await get("price=600-1000")) == ["小南"]
                assert titles(await get("price=-600")) == ["阿福"]
                assert titles(await get("tags=温柔")) == ["小南", "阿福"]
                assert titles(await get("tags=温柔,御姐&district=惠阳")) == ["阿福"]
                r = await get("tags=新人&price=600-1000")
                assert titles(r) == ["小南"] and r.json()["filters"]["active"] == 2
                # 距离: centre = 南山 by district pick; radius 10 keeps 南山/福田(~13km? no) — compute
                r = await get("sort=near&at=惠城")
                order = [x["title"] for x in r.json()["items"]]
                assert order[0] == "小南" and order[-1] == "龙姐"
                assert all(x.get("distance") for x in r.json()["items"])
                assert "approx_lat" not in r.text and "23.101" not in r.text   # coords never leave
                assert titles(await get("sort=near&at=惠城&radius=3")) == ["小南"]
                assert titles(await get("sort=near&at=惠城&radius=5")) == ["宝宝", "小南"]
                # user location (rounded server-side) works the same
                assert titles(await get("lat=22.79&lng=114.46&radius=3")) == ["阿福"]
                # 搜索: name / district / tag
                assert titles(await get("q=惠东")) == ["龙姐"]
                assert titles(await get("q=御姐")) == ["龙姐"]
                assert titles(await get("q=小")) == ["小南"]
                # old clients (no new params) unchanged
                assert len((await get("")).json()["items"]) == 4

                # ---- 频道推送：带按钮 + 去重 + 同步编辑 + 下架标记 ----
                lamp = await extras_store.get_lamp(ids["a"]) if hasattr(extras_store, "get_lamp") else None
                from bot.services import search_service
                lamp = await search_service.get_lamp(ids["a"])
                lamp["photos"] = ["AgACAgUAAxkBAAIBpic1"]
                lamp["media"] = [{"type": "image", "url": "AgACAgUAAxkBAAIBpic1"}]
                link = await broadcast.broadcast_listing(lamp)
                assert link and link.startswith("https://t.me/yy_test/")
                kind, chat, kw = fake.calls[-1]
                assert kind == "photo" and chat == "@yy_test" and kw["kb"] is None      # no buttons at all
                compact = ids["a"].replace("-", "")
                bn = (await bot_info.get_bot_identity()).get("username")
                cap = kw["caption"]
                for k, label in (("s", "查看详情"), ("r", "查看评价"), ("w", "写评价")):
                    assert f'<a href="https://t.me/{bn}?start={k}_{compact}">{label}</a>' in cap
                assert "startapp" not in cap
                n = len(fake.calls)
                await broadcast.broadcast_listing(lamp)                   # second call = edit, never a duplicate post
                assert [k for k, *_ in fake.calls[n:]] == ["edit_caption"] and fake.calls[-1][2]["kb"] is None
                lamp["price_text"] = "999P"
                await broadcast.update_broadcast(lamp)
                assert "999P" in fake.calls[-1][2]["text"] and "写评价</a>" in fake.calls[-1][2]["text"]
                await listing_ops.unlist_lamp(ids["a"], reason="admin")
                last = fake.calls[-1]
                assert last[0] == "edit_caption" and "已下架" in last[2]["text"] and last[2]["kb"] is None
                assert "<a href" not in last[2]["text"]                     # links removed on takedown
                # album: caption on the first item carries the links; no extra card message
                lb = await search_service.get_lamp(ids["b"])
                lb["media"] = [{"type": "image", "url": "AgACAgUAAxkBAAIBpic2"}, {"type": "image", "url": "AgACAgUAAxkBAAIBpic3"}]
                n = len(fake.calls)
                await broadcast.broadcast_listing(lb)
                assert [k for k, *_ in fake.calls[n:]] == ["album"]
                assert f"start=s_{ids['b'].replace('-', '')}" in fake.calls[-1][2]["caps"][0]
                n = len(fake.calls)
                await broadcast.update_broadcast(lb)
                assert [k for k, *_ in fake.calls[n:]] == ["edit_caption"]
                # user content is HTML-escaped (names with < & used to break the send)
                lc = await search_service.get_lamp(ids["c"])
                lc["title"] = "A<b>&C"
                lc["media"] = []
                await broadcast.broadcast_listing(lc)
                assert fake.calls[-1][0] == "text" and "A&lt;b&gt;&amp;C" in fake.calls[-1][2]["text"]
                # 按区目录: off by default; on → one post, then edits the same message
                from bot.services import channel_directory
                assert await channel_directory.refresh_city("惠州") is False
                await home_service.update_settings(ops_config={"channel_directory": True})
                n = len(fake.calls)
                assert await channel_directory.refresh_city("惠州") is True
                assert await channel_directory.refresh_city("惠州") is True
                ks = [(k, kw.get("mid")) for k, _, kw in fake.calls[n:]]
                assert [k for k, _ in ks] == ["text", "edit_text"] and ks[1][1] == n + 501
                assert "惠阳" in fake.calls[-1][2]["text"] and "start=s_" in fake.calls[-1][2]["text"]
                await home_service.update_settings(ops_config={"channel_directory": False})

                # ---- 结构化评价 + 私密凭证 ----
                body = {"lamp_id": ids["b"], "stars": 4, "sections": {"diff": "九成像", "pros": "态度很好", "cons": "无"}}
                r = await c.post("/api/reviews", json=body, headers=h)
                assert r.status_code == 400 and "至少" in r.json()["detail"]
                body["sections"] = {"diff": "人照九成像", "pros": "态度很好人很温柔", "cons": "环境一般般吧", "extra": ""}
                body["proofs"] = ["AgACAgUAAxkBAAIBprf1", "AgACAgUAAxkBAAIBprf2"]
                fake.calls.clear()
                r = await c.post("/api/reviews", json=body, headers=h)
                assert r.status_code == 200, r.text
                rv = r.json()["review"]
                assert rv["status"] == "pending" and "proofs" not in rv and rv["proof_count"] == 2
                await asyncio.sleep(0.4)
                albums = [x for x in fake.calls if x[0] == "album"]
                assert albums and albums[0][2]["n"] == 2 and "约课凭证" in (albums[0][2]["caps"][0] or "")
                txt = [x for x in fake.calls if x[0] == "text" and "新评价待审核" in (x[2].get("text") or "")]
                assert txt and "人照课表差异" in txt[0][2]["text"] and "约课凭证：2 张" in txt[0][2]["text"]
                # admin list has proofs; public detail never does
                adm = (await c.get("/api/admin/reviews/pending", headers=tok(1))).json()
                mine = [x for x in adm["items"] if x["review_id"] == rv["review_id"]][0]
                assert mine["proofs"] == body["proofs"] and mine["sections"]["pros"].startswith("态度")
                await home_service.set_review_status(rv["review_id"], "approved")
                d = (await c.get(f"/api/lamps/{ids['b']}", headers=h)).text
                assert "AgACAgUAAxkBAAIBprf1" not in d and "人照九成像" in d
                # 约课凭证 always required
                body.pop("proofs")
                r = await c.post("/api/reviews", json=body, headers=h)
                assert r.status_code == 400 and "约课凭证" in r.json()["detail"]
                # public photos are no longer accepted (old reviews keep theirs)
                body.update(photos=["AgACAgUAAxkBAAIBpub1"], proofs=["AgACAgUAAxkBAAIBprf3"])
                r = await c.post("/api/reviews", json=body, headers=h)
                assert r.status_code == 200 and r.json()["review"]["photos"] == []
                hd = (await c.get("/api/home?city=惠州&limit=1", headers=h)).json()["review_form"]
                assert hd == {"public_photos": False, "proof_required": True}
        finally:
            m.bot = real
            await home_service.update_settings(ops_config={"broadcast_channel": ""})
            await db.close_db()

    asyncio.run(run())
