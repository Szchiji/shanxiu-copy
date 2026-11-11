"""Release contracts without a production DB or Telegram connection."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.services.community import (
    safe_url,
    clean_welcome_buttons,
    public_links,
    district_groups,
)
from bot.services.home_feed import merge_ops, clean_override_value, ops_for_city


def test_safe_targets_and_welcome_limits():
    assert safe_url("t.me/real_group") == "https://t.me/real_group"
    for raw in (
        "javascript:alert(1)",
        "http://bad.test",
        "//bad.test",
        "https://u:p@bad.test",
        "https://x.test/\nheader",
        "https://[bad",
    ):
        assert not safe_url(raw)
    raw = [
        None,
        {},
        {"type": "webapp", "label": "wrong", "target": "https://evil.test"},
        {"type": "url", "label": "bad", "target": "javascript:1"},
    ]
    raw += [{"type": "webapp", "label": str(i), "target": "me"} for i in range(8)]
    clean = clean_welcome_buttons(raw)
    assert len(clean) == 8 and all(x["target"] == "me" for x in clean)
    assert clean_welcome_buttons("junk") == []


def test_settings_preserve_blank_vs_inherit():
    site = merge_ops(
        {
            "community_discussion_url": "t.me/site_group",
            "bot_welcome_buttons": [{}],
            "city_overrides": {
                "深圳": {
                    "community_discussion_url": "",
                    "link_label_detail": "",
                    "approve_attach_detail": False,
                }
            },
        }
    )
    city = ops_for_city(site, "深圳")
    assert city["community_discussion_url"] == "https://t.me/site_group"
    assert city["link_label_detail"] == "" and city["approve_attach_detail"] is False
    assert (
        site["approve_attach_detail"] is True
        and site["approve_attach_channel_btn"] is True
    )
    assert site["bot_welcome_buttons"] == []
    assert clean_override_value("link_label_location", "") == ""
    assert clean_override_value("community_location_url", "") is None
    assert not public_links(merge_ops({}))
    assert {x["key"] for x in public_links(city)} == {"discussion"}
    city["community_enabled"] = False
    assert not public_links(city)


def test_map_uses_only_public_district_centres():
    from bot.services.feed_filters import center_of

    data = [
        {
            "lamp_id": "a",
            "district": "南山",
            "approx_lat": 1.234567,
            "approx_lng": 2.345678,
        },
        {"lamp_id": "b", "district": "南山区"},
        {"lamp_id": "c", "district": "未知"},
    ]
    groups = district_groups(data, "深圳")
    known = next(g for g in groups if g["count"] == 2)
    assert (known["lat"], known["lng"]) == center_of("深圳", "南山")
    assert known["lamp_ids"] == ["a", "b"]
    unknown = next(g for g in groups if g["count"] == 1)
    assert "lat" not in unknown and "lng" not in unknown
    assert "1.234567" not in str(groups) and "approx_" not in str(groups)


def test_welcome_keyboard_known_pages_and_two_per_row(monkeypatch):
    from bot import keyboards

    monkeypatch.setattr(
        keyboards,
        "get_settings",
        lambda: SimpleNamespace(normalized_webapp_url="https://app.test/"),
    )
    kb = keyboards.welcome_kb(
        [
            {"type": "webapp", "label": "我的", "target": "me"},
            {"type": "url", "label": "联系", "target": "t.me/real_admin"},
            {"type": "webapp", "label": "首页", "target": "home"},
        ]
    )
    assert [len(row) for row in kb.inline_keyboard] == [2, 1]
    assert kb.inline_keyboard[0][0].web_app.url == "https://app.test/go.html?page=me"
    assert kb.inline_keyboard[1][0].web_app.url == "https://app.test/go.html"
    assert keyboards.welcome_kb([]).remove_keyboard is True


@pytest.mark.parametrize(
    "album,detail,channel,rows",
    [
        (None, True, True, 1),
        ("https://t.me/real_channel/12", True, True, 2),
        ("https://t.me/real_channel/12", False, False, 0),
    ],
)
def test_approval_dm_buttons_and_original_links(
    monkeypatch, album, detail, channel, rows
):
    import bot.main as main
    from bot.services import listing_notify, bot_info, home_service, share_gate

    fake = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(main, "bot", fake)
    monkeypatch.setattr(
        home_service,
        "get_or_create_settings",
        AsyncMock(
            return_value=merge_ops(
                {"approve_attach_detail": detail, "approve_attach_channel_btn": channel}
            )
        ),
    )
    monkeypatch.setattr(
        bot_info,
        "get_bot_identity",
        AsyncMock(return_value={"username": "real_test_bot"}),
    )
    monkeypatch.setattr(
        share_gate,
        "get_settings",
        lambda: SimpleNamespace(normalized_webapp_url="https://app.test/"),
    )
    lid = "12345678-1234-1234-1234-123456789abc"
    asyncio.run(
        listing_notify.notify_publisher_approved(
            1, "<老师>", album, "深圳", lamp_id=lid
        )
    )
    args, kw = fake.send_message.call_args
    assert "&lt;老师&gt;" in args[1] and "https://t.me/real_test_bot" in args[1]
    if album:
        assert album in args[1]
    assert ("?start=s_12345678123412341234123456789abc" in args[1]) is detail
    kb = kw["reply_markup"]
    assert (len(kb.inline_keyboard) if kb else 0) == rows
    if detail:
        assert kb.inline_keyboard[0][0].text == "查看我的资料"
        assert f"lamp={lid}" in kb.inline_keyboard[0][0].web_app.url


def test_ranking_query_dedup_audit_window_and_ties(monkeypatch):
    from sqlalchemy.dialects import postgresql
    from bot.api import routes_community as rc

    monkeypatch.setattr(rc, "city_site", AsyncMock(return_value=("深圳", {})))
    statements = []

    class Session:
        async def execute(self, stmt):
            statements.append(
                str(
                    stmt.compile(
                        dialect=postgresql.dialect(),
                        compile_kwargs={"literal_binds": True},
                    )
                )
            )
            return SimpleNamespace(
                all=lambda: [
                    ("a", "甲", "南山", 3),
                    ("b", "乙", "福田", 3),
                    ("c", "丙", "宝安", 1),
                ]
            )

    @asynccontextmanager
    async def scope():
        yield Session()

    monkeypatch.setattr(rc, "session_scope", scope)
    result = asyncio.run(rc.api_city_ranking("深圳", 1))
    sql = statements[0]
    assert "count(DISTINCT reviews.guest_id)" in sql and "lamps.city = '深圳'" in sql
    assert "reviews.status = 'approved'" in sql and "reviews.created_at >=" in sql
    assert "免审直接发布" in sql and "reviews.reviewed_at IS NOT NULL" in sql
    assert [x["rank"] for x in result["items"]] == [1, 1, 3]


def test_community_uses_last_refreshed_directory(monkeypatch):
    from bot.api import routes_community as rc
    from bot.services import channel_directory

    monkeypatch.setattr(
        rc,
        "city_site",
        AsyncMock(return_value=("深圳", merge_ops({"channel_directory": False}))),
    )
    monkeypatch.setattr(
        channel_directory, "_ref", AsyncMock(return_value=("@old_channel", 123))
    )
    out = asyncio.run(rc.api_community("深圳", 1))
    assert out["links"] == [
        {"key": "directory", "label": "按区目录", "url": "https://t.me/old_channel/123"}
    ]


def test_map_endpoint_keeps_filters_and_card_privacy(monkeypatch):
    from fastapi import FastAPI
    import httpx
    from bot.api import routes_core_home as rh
    from bot.api.deps import get_current_user_id
    from bot.services import home_service, search_service, pin_query, places

    settings = merge_ops({"coupons_enabled": False}) | {"enabled_cities": ["深圳"]}
    monkeypatch.setattr(
        home_service, "get_or_create_settings", AsyncMock(return_value=settings)
    )
    monkeypatch.setattr(pin_query, "list_active_pins", AsyncMock(return_value=[]))
    monkeypatch.setattr(places, "get_point", AsyncMock(return_value=None))
    monkeypatch.setattr(rh, "_prefill_extras", AsyncMock())
    monkeypatch.setattr(rh, "_home_contacts", AsyncMock(return_value={}))
    monkeypatch.setattr(search_service, "district_counts", AsyncMock(return_value=[]))
    query = AsyncMock(
        return_value=[
            {
                "lamp_id": "a",
                "title": "老师",
                "city": "深圳",
                "district": "南山",
                "status": "active",
                "approx_lat": 1.23456,
                "approx_lng": 2.34567,
                "extras": {"_secret": "x"},
            }
        ]
    )
    monkeypatch.setattr(search_service, "search_lamps", query)
    app = FastAPI()
    app.include_router(rh.router)
    app.dependency_overrides[get_current_user_id] = lambda: 1

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as c:
            for mode in ("0", "1"):
                r = await c.get(
                    "/api/home",
                    params={
                        "city": "深圳",
                        "q": "老师",
                        "district": "南山",
                        "sort": "reviews",
                        "price": "600-1000",
                        "tags": "温柔",
                        "map_results": mode,
                    },
                )
                assert r.status_code == 200, r.text
                out = r.json()
                assert (
                    out["page_size"] == 3
                    and "approx_lat" not in str(out["items"])
                    and "_secret" not in str(out["items"])
                )
                assert bool(out["map_groups"]) is (mode == "1")
                args = query.call_args.kwargs
                assert (
                    args["keyword"] == "老师"
                    and args["district"] == "南山"
                    and args["sort"] == "reviews"
                )
                assert args["limit"] == (600 if mode == "1" else 3)
                assert args["flt"].tags == ["温柔"] and args["flt"].band == (600, 1000)

            # A failed map query is actionable, not a fabricated empty viewport.
            # The historical non-map response remains unchanged.
            query.side_effect = RuntimeError("private database connection detail")
            for mode, code in [("1", 503), ("0", 200)]:
                r = await c.get("/api/home", params={"city": "深圳", "map_results": mode})
                assert r.status_code == code
                assert "private database" not in r.text
                if mode == "1":
                    assert r.json()["detail"] == "地图资料暂时无法加载，请重试"
                else:
                    assert r.json()["items"] == [] and r.json()["ok"] is True
            query.side_effect = None
            query.return_value = []
            r = await c.get("/api/home", params={"city": "深圳", "map_results": "1"})
            assert r.status_code == 200 and r.json()["items"] == []

    asyncio.run(run())


def test_channel_position_is_optional_text_only(monkeypatch):
    from bot.services import broadcast, home_service, bot_info

    site = merge_ops(
        {
            "community_location_url": "https://maps.example/city",
            "link_label_location": "位置",
        }
    )
    monkeypatch.setattr(
        home_service, "get_or_create_settings", AsyncMock(return_value=site)
    )
    monkeypatch.setattr(
        bot_info,
        "get_bot_identity",
        AsyncMock(return_value={"username": "real_test_bot"}),
    )
    out = asyncio.run(
        broadcast._caption(
            {"lamp_id": "12345678-1234-1234-1234-123456789abc", "city": "深圳"}, {}
        )
    )
    assert "https://maps.example/city" in out[1] and out[3] is None
    site["link_label_location"] = ""
    out = asyncio.run(
        broadcast._caption(
            {"lamp_id": "12345678-1234-1234-1234-123456789abc", "city": "深圳"}, {}
        )
    )
    assert "https://maps.example/city" not in out[1]


def test_no_home_card_trust_badges_and_correct_approval_wiring():
    root = Path(__file__).resolve().parents[1]
    js = (root / "miniapp/home-ui.js").read_text()
    card = js[js.index("function cardHtml") : js.index("function pinHtml")]
    assert "已审核" not in card and "review_count" not in card
    wire = (root / "bot/services/admin_ops_wire.py").read_text()
    assert "listing_edits.review(post_id,True" in wire and 'lamp_id=result.get("lamp_id")' in wire
    delivery = Path("bot/services/edit_delivery.py").read_text()
    assert 'lamp_id=lamp["lamp_id"]' in delivery


def test_resting_listing_rejects_contact_before_session_creation(monkeypatch):
    from fastapi import FastAPI
    import httpx
    from bot.api import routes_core_home as rh
    from bot.api.deps import get_current_user_id
    from bot.services import search_service, session_service

    monkeypatch.setattr(
        search_service,
        "get_lamp",
        AsyncMock(return_value={"lamp_id": "a", "status": "active", "user_id": 2}),
    )
    monkeypatch.setattr(rh, "_with_shop", AsyncMock(return_value={"open_code": "rest"}))
    create = AsyncMock()
    monkeypatch.setattr(session_service, "create_request", create)
    app = FastAPI()
    app.include_router(rh.router)
    app.dependency_overrides[get_current_user_id] = lambda: 1

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as c:
            r = await c.post("/api/sessions/request", json={"lamp_id": "a"})
            assert r.status_code == 409 and r.json()["detail"] == "休息中，暂不联系"

    asyncio.run(run())
    create.assert_not_awaited()
