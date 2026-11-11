"""Cross-feature isolated PG: restore/history vs public map filters. No Telegram, no production DB."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, delete

PG = os.environ.get("YYCJ_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="YYCJ_TEST_PG not set")


@asynccontextmanager
async def world(monkeypatch, *, status="active", expired=False, lasting_rest=False):
    assert PG and ("127.0.0.1:55441/" in PG) and "yycj_edit_test" in PG
    os.environ["DATABASE_URL"] = PG
    import httpx
    from fastapi import FastAPI
    from bot import db
    from bot.api import routes_core_home as rh
    from bot.api.deps import get_current_user_id
    from bot.models import Lamp, User, Post, ListingEditOperation as Op, ListingEditVersion as Ver
    from bot.services import (
        listing_edits as svc,
        listing_history as hist,
        listing_notify,
        city_scope,
        edit_delivery,
        extras_store,
        home_service,
        places,
        pin_query,
        listing_stats,
        open_shift,
    )

    db._engine = None
    await db.connect_db()
    await db._ensure_columns(db._engine)
    lid = "MH" + uuid.uuid4().hex
    owner = 83000000 + int(uuid.uuid4().hex[:6], 16)
    until = datetime.utcnow() + timedelta(days=-1 if expired else 10)
    cache = __import__("pathlib").Path(__import__("tempfile").mkdtemp(prefix="yycj-mh-media-"))
    monkeypatch.setattr(hist, "_DISK_DIR", cache)
    monkeypatch.setattr(edit_delivery, "channel", AsyncMock(return_value=edit_delivery.outcome("synced", album="https://t.me/test/902")))
    monkeypatch.setattr(edit_delivery, "notify", AsyncMock(return_value=edit_delivery.outcome("sent")))
    monkeypatch.setattr(listing_notify, "notify_admins_new_post", AsyncMock())
    monkeypatch.setattr(city_scope, "scope_for", AsyncMock(return_value=None))
    monkeypatch.setattr(open_shift, "now_sh", lambda: datetime(2026, 11, 11, 12, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(places, "get_point", AsyncMock(return_value=None))
    monkeypatch.setattr(pin_query, "list_active_pins", AsyncMock(return_value=[]))
    monkeypatch.setattr(rh, "_home_contacts", AsyncMock(return_value={}))
    monkeypatch.setattr(home_service, "reputation_for_lamp", AsyncMock(return_value={}))
    monkeypatch.setattr(home_service, "list_reviews_for_lamp", AsyncMock(return_value=[]))
    monkeypatch.setattr(listing_stats, "record_view_soon", lambda *a, **k: None)
    await home_service.update_settings(enabled_cities=["深圳", "广州"], ops_config={"coupons_enabled": False})
    async with db.session_scope() as s:
        s.add(User(user_id=owner, role="teacher"))
        s.add(
            Lamp(
                lamp_id=lid,
                user_id=owner,
                city="深圳",
                title="原称呼",
                district="南山",
                status=status,
                expires_at=until,
                media=[{"type": "image", "url": "old-photo"}],
                photos=["old-photo"],
                description="原介绍",
                tags=["温柔"],
                price=800,
                price_text="800",
            )
        )
    extra = {"_hours": [{"start": "09:00", "end": "18:00"}]}
    if lasting_rest:
        extra["_lasting_rest"] = "1"
    await extras_store.save_extras(lid, extra)

    async def read():
        async with db.session_scope() as s:
            return (await s.execute(select(Lamp).where(Lamp.lamp_id == lid))).scalar_one()

    async def versions():
        async with db.session_scope() as s:
            return list((await s.execute(select(Ver).where(Ver.lamp_id == lid).order_by(Ver.sequence))).scalars())

    app = FastAPI()
    app.include_router(rh.router)
    app.dependency_overrides[get_current_user_id] = lambda: owner
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test")

    async def map_items(**kw):
        r = await client.get("/api/home", params={"city": kw.pop("city", "深圳"), "map_results": 1, "service": kw.pop("service", "all"), **kw})
        assert r.status_code == 200, r.text
        data = r.json()
        return data, {x["lamp_id"] for x in data["items"]}

    body = {
        "city": "深圳",
        "title": "新称呼",
        "tags": ["温柔"],
        "description": "新介绍",
        "price_text": "800",
        "price": 800,
        "district": "福田",
        "approx_label": None,
        "extras": {},
        "hours": [{"start": "09:00", "end": "18:00"}],
        "media": [],
    }
    try:
        yield {
            "svc": svc,
            "hist": hist,
            "lid": lid,
            "owner": owner,
            "until": until,
            "body": body,
            "read": read,
            "versions": versions,
            "map_items": map_items,
            "client": client,
        }
    finally:
        await client.aclose()
        import shutil
        shutil.rmtree(cache, ignore_errors=True)
        async with db.session_scope() as s:
            await s.execute(delete(Post).where(Post.lamp_data["edit_lamp_id"].astext == lid))
            await s.execute(delete(Op).where(Op.lamp_id == lid))
            await s.execute(delete(Ver).where(Ver.lamp_id == lid))
            await s.execute(delete(Lamp).where(Lamp.lamp_id == lid))
            await s.execute(delete(User).where(User.user_id == owner))
        await db.close_db()


def key():
    return "op-" + uuid.uuid4().hex


def test_restore_updates_map_district_filters_and_keeps_status_expiry(monkeypatch):
    async def run():
        async with world(monkeypatch) as w:
            data, ids = await w["map_items"](district="南山", service="open")
            assert w["lid"] in ids
            card = next(x for x in data["items"] if x["lamp_id"] == w["lid"])
            assert card["title"] == "原称呼" and "approx_lat" not in card
            await w["svc"].submit(w["lid"], w["owner"], w["body"], key())
            lamp = await w["read"]()
            assert lamp.title == "新称呼" and lamp.district == "福田" and lamp.status == "active" and lamp.expires_at == w["until"]
            _, nanshan = await w["map_items"](district="南山", service="open")
            _, futian = await w["map_items"](district="福田", service="open")
            assert w["lid"] not in nanshan and w["lid"] in futian
            baseline = [r for r in await w["versions"]() if r.source == "baseline"][0]
            result = await w["hist"].restore(
                w["lid"], baseline.version_id, w["owner"], key(),
                expected_updated_at=(await w["read"]()).updated_at.isoformat(), confirm=True, include_media=False,
            )
            assert result["text_saved"]
            lamp = await w["read"]()
            assert lamp.title == "原称呼" and lamp.district == "南山"
            assert lamp.status == "active" and lamp.expires_at == w["until"] and lamp.media[0]["url"] == "old-photo"
            data, nanshan = await w["map_items"](district="南山", service="open")
            _, futian = await w["map_items"](district="福田", service="open")
            assert w["lid"] in nanshan and w["lid"] not in futian
            card = next(x for x in data["items"] if x["lamp_id"] == w["lid"])
            assert card["title"] == "原称呼" and card["open_code"] == "open"
    asyncio.run(run())


def test_hidden_restore_does_not_relist_on_map(monkeypatch):
    async def run():
        async with world(monkeypatch) as w:
            await w["svc"].submit(w["lid"], w["owner"], w["body"], key())
            from bot.services import listing_ops
            await listing_ops.teacher_unlist(w["lid"], w["owner"])
            assert (await w["read"]()).status == "hidden" and (await w["read"]()).expires_at == w["until"]
            _, ids = await w["map_items"](service="all")
            assert w["lid"] not in ids
            baseline = [r for r in await w["versions"]() if r.source == "baseline"][0]
            result = await w["hist"].restore(
                w["lid"], baseline.version_id, w["owner"], key(),
                expected_updated_at=(await w["read"]()).updated_at.isoformat(), confirm=True, include_media=False,
            )
            # hidden listings require full review; text is not applied and status must stay hidden
            assert not result["text_saved"] and result["review_status"] == "pending"
            assert (await w["read"]()).status == "hidden" and (await w["read"]()).title == "新称呼"
            _, ids = await w["map_items"](service="all")
            assert w["lid"] not in ids
            done = await w["svc"].review(result["post_id"], True)
            assert done["ok"]
            lamp = await w["read"]()
            assert lamp.title == "原称呼" and lamp.status == "hidden" and lamp.expires_at == w["until"]
            _, ids = await w["map_items"](service="all")
            assert w["lid"] not in ids
    asyncio.run(run())


def test_restore_keeps_lasting_rest_on_map_and_rejects_missing_media(monkeypatch):
    async def run():
        async with world(monkeypatch, lasting_rest=True) as w:
            _, rest_ids = await w["map_items"](service="rest")
            _, open_ids = await w["map_items"](service="open")
            assert w["lid"] in rest_ids and w["lid"] not in open_ids
            first = await w["svc"].submit(w["lid"], w["owner"], {**w["body"], "media": [{"type": "image", "url": "new-photo"}]}, key())
            await w["svc"].review(first["post_id"], True)
            assert (await w["read"]()).media[0]["url"] == "new-photo"
            baseline = [r for r in await w["versions"]() if r.source == "baseline"][0]
            with pytest.raises(w["svc"].EditConflict, match="不可用"):
                await w["hist"].restore(
                    w["lid"], baseline.version_id, w["owner"], key(),
                    expected_updated_at=(await w["read"]()).updated_at.isoformat(), confirm=True, include_media=True,
                )
            assert (await w["read"]()).media[0]["url"] == "new-photo"
            await w["hist"].restore(
                w["lid"], baseline.version_id, w["owner"], key(),
                expected_updated_at=(await w["read"]()).updated_at.isoformat(), confirm=True, include_media=False,
            )
            lamp = await w["read"]()
            assert lamp.title == "原称呼" and lamp.district == "南山" and lamp.media[0]["url"] == "new-photo"
            _, rest_ids = await w["map_items"](service="rest", district="南山")
            _, open_ids = await w["map_items"](service="open", district="南山")
            assert w["lid"] in rest_ids and w["lid"] not in open_ids
    asyncio.run(run())


def test_stale_or_unauthorized_restore_leaves_map_unchanged(monkeypatch):
    async def run():
        async with world(monkeypatch) as w:
            await w["svc"].submit(w["lid"], w["owner"], w["body"], key())
            baseline = [r for r in await w["versions"]() if r.source == "baseline"][0]
            _, before = await w["map_items"](district="福田", service="open")
            assert w["lid"] in before
            with pytest.raises(w["svc"].EditConflict, match="确认"):
                await w["hist"].restore(w["lid"], baseline.version_id, w["owner"], key(), expected_updated_at=(await w["read"]()).updated_at.isoformat(), confirm=False)
            with pytest.raises(w["svc"].EditConflict, match="刷新"):
                await w["hist"].restore(w["lid"], baseline.version_id, w["owner"], key(), expected_updated_at="2000-01-01T00:00:00", confirm=True)
            other = w["owner"] + 9
            from bot import db
            from bot.models import User
            from bot.services import city_scope
            from fastapi import HTTPException
            async with db.session_scope() as s:
                s.add(User(user_id=other, role="teacher"))
            with pytest.raises(w["svc"].EditConflict):
                await w["hist"].restore(w["lid"], baseline.version_id, other, key(), expected_updated_at=(await w["read"]()).updated_at.isoformat(), confirm=True)
            monkeypatch.setattr(city_scope, "scope_for", AsyncMock(return_value={"广州"}))
            with pytest.raises(HTTPException):
                await w["hist"].history(w["lid"], 1, admin=True)
            lamp = await w["read"]()
            assert lamp.title == "新称呼" and lamp.district == "福田" and lamp.status == "active"
            _, after = await w["map_items"](district="福田", service="open")
            assert w["lid"] in after
            async with db.session_scope() as s:
                await s.execute(delete(User).where(User.user_id == other))
    asyncio.run(run())
