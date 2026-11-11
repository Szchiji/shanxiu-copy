"""Typed, safe delivery outcomes for edit operations. Never delete historical messages."""
import logging
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InputMediaPhoto, InputMediaVideo
from bot.services import broadcast, extras_store, listing_notify

log = logging.getLogger(__name__)


def outcome(status, retryable=False, album=None):
    return {"status":status, "retryable":retryable, "album":album}


def known_rejection(exc):
    return isinstance(exc, (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter))


async def channel(lamp, action):
    from bot.main import bot
    acknowledged = False
    album = None
    try:
        chat, caption, _, _ = await broadcast._caption(lamp, lamp.get("extras") or {})
        if not chat:
            return outcome("not_configured", True)
        if action == "text":
            chat, mid = await extras_store.load_broadcast(lamp["lamp_id"])
            if not chat or not str(mid).isdigit():
                return outcome("no_post")
            try:
                await bot.edit_message_caption(chat_id=chat,message_id=int(mid),caption=caption,reply_markup=None)
            except TelegramBadRequest as exc:
                if "not modified" not in str(exc).lower():
                    try:
                        await bot.edit_message_text(chat_id=chat,message_id=int(mid),text=caption,reply_markup=None,disable_web_page_preview=True)
                    except TelegramBadRequest as text_exc:
                        if "not modified" not in str(text_exc).lower():
                            raise
            return outcome("synced", album=broadcast.album_link(chat,int(mid)))
        items = broadcast._media_items(lamp)
        if len(items) > 1:
            group = [(InputMediaVideo if kind == "video" else InputMediaPhoto)(media=raw,caption=caption if i==0 else None) for i,(kind,raw) in enumerate(items)]
            messages = await bot.send_media_group(chat,media=group)
            msg = messages[0] if messages else None
        elif items:
            kind,raw = items[0]
            msg = await (bot.send_video(chat,raw,caption=caption) if kind == "video" else bot.send_photo(chat,raw,caption=caption))
        else:
            msg = await bot.send_message(chat,caption,disable_web_page_preview=True)
        acknowledged = True
        mid = getattr(msg,"message_id",None)
        if not mid:
            return outcome("unknown")
        album = broadcast.album_link(chat,int(mid))
        await extras_store.remember_broadcast(lamp["lamp_id"],chat,int(mid),card_mid=None)
        stored_chat,stored_mid = await extras_store.load_broadcast(lamp["lamp_id"])
        if str(stored_chat) != str(chat) or str(stored_mid) != str(mid):
            return outcome("unknown",album=album)
        return outcome("synced",album=album)
    except Exception as exc:
        log.exception("edit channel delivery failed for %s",lamp.get("lamp_id"))
        if action == "text":
            return outcome("failed",True)
        # Network timeout / acknowledgement-with-ref-failure must never be resent blindly.
        return outcome("failed",True) if not acknowledged and known_rejection(exc) else outcome("unknown",album=album)


async def notify(lamp, album, *, rejected=False):
    try:
        if rejected:
            from bot.main import bot
            await bot.send_message(int(lamp["user_id"]),"你的灯笼投稿未通过审核。")
        else:
            sent = await listing_notify.notify_publisher_approved(int(lamp["user_id"]),lamp.get("title") or "资料",album,
                city=lamp.get("city"),lamp_id=lamp["lamp_id"],raise_errors=True,listing_active=lamp.get("_edit_listing_active",lamp.get("status")=="active"),operation_id=lamp.get("_edit_operation_id"),actor_id=lamp.get("_edit_actor_id"))
            if sent is None:return outcome("skipped")
            if sent is False:return outcome("unknown")
        return outcome("sent")
    except Exception as exc:
        log.exception("edit owner notification failed for %s",lamp.get("lamp_id"))
        return outcome("failed",True) if known_rejection(exc) else outcome("unknown")
