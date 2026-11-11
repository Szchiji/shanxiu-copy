"""No real DB/Telegram: fresh renewal send, ordinary dedupe, checkpoints and failure reporting."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from bot.services import renewal
from bot.models import ListingRenewal


def setup_flow(monkeypatch, *, album="https://t.me/test/501", notified=True):
    saved = {}
    async def claim(key, lid, admin, days):
        if key in saved:
            return False
        saved[key] = {"ok": False, "message": "处理中"}
        return True
    async def load(key, *args):
        return dict(saved[key])
    async def save(key, result):
        saved[key] = dict(result)
    monkeypatch.setattr(renewal, "claim", claim)
    monkeypatch.setattr(renewal, "load", load)
    monkeypatch.setattr(renewal, "save", save)
    expiry=AsyncMock(return_value={"lamp_id":"lamp1","expires_at":datetime(2026,12,10),"status":"active"})
    post=AsyncMock(return_value=album)
    notify=AsyncMock(return_value=notified)
    monkeypatch.setattr(renewal.listing_ops,"set_listing_expiry",expiry)
    monkeypatch.setattr(renewal.search_service,"get_lamp",AsyncMock(return_value={"lamp_id":"lamp1","user_id":77,"title":"老师","city":"深圳","extras":{"价位":"示例"}}))
    monkeypatch.setattr(renewal.broadcast,"broadcast_listing",post)
    monkeypatch.setattr(renewal.listing_notify,"notify_publisher_approved",notify)
    return saved,expiry,post,notify


def test_future_renewal_explicit_repost_and_correct_teacher_notify_once(monkeypatch):
    saved,expiry,post,notify=setup_flow(monkeypatch)
    async def run():
        a=await renewal.renew_listing("lamp1",123,30,"operation-12345678")
        b=await renewal.renew_listing("lamp1",123,30,"operation-12345678")
        assert a == b and a["ok"] and a["channel_status"] == a["notify_status"] == "sent"
    asyncio.run(run())
    assert expiry.await_count == post.await_count == notify.await_count == 1
    assert post.call_args.kwargs == {"force_new":True}
    notify.assert_awaited_once_with(77,"老师","https://t.me/test/501",city="深圳",lamp_id="lamp1",raise_errors=True,operation_id="operation-12345678",actor_id=123)


@pytest.mark.parametrize("album,notified,channel,notification",[(None,True,"failed_or_unknown","not_attempted"),("https://t.me/test/501",False,"sent","failed_or_unknown")])
def test_partial_failure_not_claimed_as_full_success_or_automatically_retried(monkeypatch,album,notified,channel,notification):
    _,expiry,post,notify=setup_flow(monkeypatch,album=album,notified=notified)
    async def run():
        a=await renewal.renew_listing("lamp1",123,30,"operation-12345678")
        assert a["renewed"] and not a["ok"]
        assert a["channel_status"] == channel and a["notify_status"] == notification
        assert await renewal.renew_listing("lamp1",123,30,"operation-12345678") == a
    asyncio.run(run())
    assert expiry.await_count == post.await_count == 1
    assert notify.await_count == (1 if album else 0)


def test_before_send_checkpoint_failure_never_sends(monkeypatch):
    _,_,post,notify=setup_flow(monkeypatch)
    monkeypatch.setattr(renewal,"save",AsyncMock(side_effect=RuntimeError("database unavailable")))
    with pytest.raises(RuntimeError):
        asyncio.run(renewal.renew_listing("lamp1",123,30,"operation-12345678"))
    post.assert_not_awaited();notify.assert_not_awaited()


def test_missing_listing_does_not_push_or_notify(monkeypatch):
    _,expiry,post,notify=setup_flow(monkeypatch)
    expiry.side_effect=ValueError("资料不存在")
    with pytest.raises(ValueError):
        asyncio.run(renewal.renew_listing("lamp1",123,30,"operation-12345678"))
    post.assert_not_awaited();notify.assert_not_awaited()


def test_force_repost_sends_album_and_updates_new_ref_without_deleting_history(monkeypatch):
    import bot.main as main
    from bot.services import broadcast,extras_store
    fake=SimpleNamespace(send_media_group=AsyncMock(return_value=[SimpleNamespace(message_id=501)]),delete_message=AsyncMock())
    monkeypatch.setattr(main,"bot",fake)
    monkeypatch.setattr(extras_store,"load_broadcast",AsyncMock(return_value=("@test","123")))
    remember=AsyncMock();monkeypatch.setattr(extras_store,"remember_broadcast",remember)
    monkeypatch.setattr(broadcast,"_caption",AsyncMock(return_value=("@test",'正文 <a href="https://t.me/testbot?start=s_lamp1">查看详情</a>',"testbot",None)))
    edit=AsyncMock(return_value="old-link");monkeypatch.setattr(broadcast,"update_broadcast",edit)
    lamp={"lamp_id":"lamp1","media":[{"type":"image","url":"photo1"},{"type":"image","url":"photo2"}]}
    assert asyncio.run(broadcast.broadcast_listing(lamp)) == "old-link"
    fake.send_media_group.assert_not_awaited()
    assert asyncio.run(broadcast.broadcast_listing(lamp,force_new=True)) == "https://t.me/test/501"
    assert edit.await_count == 1
    remember.assert_awaited_once_with("lamp1","@test",501,card_mid=None)
    assert fake.send_media_group.call_args.kwargs["media"][0].caption.startswith("正文")
    fake.delete_message.assert_not_awaited()
    # If post delivery succeeded but persistence failed, never report full success.
    remember.side_effect=RuntimeError("ref persistence failed")
    assert asyncio.run(broadcast.broadcast_listing(lamp,force_new=True)) is None


def test_renewal_table_is_additive_and_request_key_is_unique():
    ddl=str(CreateTable(ListingRenewal.__table__).compile(dialect=postgresql.dialect()))
    assert "CREATE TABLE listing_renewals" in ddl and "PRIMARY KEY (operation_id)" in ddl
    assert "DROP" not in ddl


def test_renewal_admin_ui_has_stable_key_and_no_get_refresh_side_effect():
    from pathlib import Path
    js=Path("miniapp/admin-list.js").read_text()
    assert "sessionStorage.setItem(key, JSON.stringify(pending))" in js
    assert "if(result.ok) sessionStorage.removeItem(key)" in js
    assert "ev.stopImmediatePropagation()" in js


def test_real_notification_uses_new_album_current_city_and_miniapp_buttons(monkeypatch):
    import bot.main as main
    from bot.services import listing_notify,home_service,bot_info,share_gate
    from bot.services.home_feed import merge_ops
    real_notify=listing_notify.notify_publisher_approved
    setup_flow(monkeypatch)
    monkeypatch.setattr(listing_notify,"notify_publisher_approved",real_notify)
    bot=SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(main,"bot",bot)
    monkeypatch.setattr(home_service,"get_or_create_settings",AsyncMock(return_value=merge_ops({"approve_attach_detail":False,"city_overrides":{"深圳":{"approve_attach_detail":True,"approve_attach_channel_btn":True,"approve_promo_text":"本城通知文案"}}})))
    monkeypatch.setattr(bot_info,"get_bot_identity",AsyncMock(return_value={"username":"testbot"}))
    monkeypatch.setattr(share_gate,"get_settings",lambda:SimpleNamespace(normalized_webapp_url="https://app.test/"))
    result=asyncio.run(renewal.renew_listing("lamp1",123,30,"operation-12345678"))
    assert result["ok"]
    args,kw=bot.send_message.call_args
    assert args[0] == 77 and "本城通知文案" in args[1] and "https://t.me/test/501" in args[1]
    rows=kw["reply_markup"].inline_keyboard
    assert rows[0][0].web_app and "lamp=lamp1" in rows[0][0].web_app.url
    assert rows[-1][0].text == "查看频道帖" and rows[-1][0].url == "https://t.me/test/501"


def test_production_preview_is_unreachable_and_versions_menu_are_consistent():
    import httpx
    import bot.main as main
    from pathlib import Path
    assert main._miniapp_asset_v() == "20261112b"
    assert main._menu_webapp_url("https://app.test/app") == "https://app.test/app/go.html?v=20261112b"
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url="http://test") as client:
            for path in ("/preview-fixtures.js","/scripts/preview_review.py","/scripts/preview/fixtures.js","/app/preview-fixtures.js"):
                assert (await client.get(path)).status_code == 404
    asyncio.run(run())
    for page in ("go.html","desk.html"):
        text=Path("miniapp",page).read_text()
        assert "preview-fixtures" not in text and "LOCAL_PREVIEW" not in text
    desk=Path("miniapp/desk.html").read_text()
    for asset,version in (("admin.js","20261112a"),("workspace.js","20261112a"),("workspace.css","20261111g"),("admin-ops.js","20261112a"),("admin-proxy.js","20261111a"),("admin-buttons.js","20261111b"),("admin-coupons.js","20261111a"),("admin-list.js","20261111d")):
        assert f"{asset}?v={version}" in desk
