"""Fail-closed outbound and moderation checks for the dedicated test bot."""

from __future__ import annotations

import contextvars
import logging
from typing import Any

from hetai.config import Settings

logger = logging.getLogger("hetai.bot.guard")

_current_chat_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "hetai_update_chat_id", default=None
)

# Reply-style Bot API methods allowed for a user-initiated update chat.
REPLY_METHODS = {
    "sendMessage",
    "sendPhoto",
    "sendDocument",
    "sendAudio",
    "sendVideo",
    "sendAnimation",
    "sendVoice",
    "sendVenue",
    "sendLocation",
    "sendChatAction",
    "editMessageText",
    "editMessageCaption",
    "editMessageReplyMarkup",
    "answerCallbackQuery",
    "leaveChat",
}
# Explicitly blocked until a test chat is allowlisted and authorized.
MODERATION_METHODS = {
    "banChatMember",
    "unbanChatMember",
    "restrictChatMember",
    "promoteChatMember",
    "setChatAdministratorCustomTitle",
    "setChatPermissions",
    "setChatTitle",
    "setChatDescription",
    "setChatPhoto",
    "deleteChatPhoto",
    "pinChatMessage",
    "unpinChatMessage",
    "unpinAllChatMessages",
    "deleteMessage",
    "deleteMessages",
    "approveChatJoinRequest",
    "declineChatJoinRequest",
}
# Reads and webhook/menu setup are not user-message sends.
READ_OR_SETUP_METHODS = {
    "getMe",
    "getWebhookInfo",
    "getChat",
    "getChatMember",
    "getChatAdministrators",
    "getChatMemberCount",
    "getMyCommands",
    "getChatMenuButton",
    "getMyDefaultAdministratorRights",
}

# Never used from this runtime; ops scripts use raw HTTPS instead.
BLOCKED_RUNTIME_METHODS = {
    "setWebhook",
    "deleteWebhook",
    "getUpdates",
}


def set_update_chat_id(chat_id: int | None):
    return _current_chat_id.set(int(chat_id) if chat_id is not None else None)


def reset_update_chat_id(token) -> None:
    _current_chat_id.reset(token)


def current_update_chat_id() -> int | None:
    return _current_chat_id.get()


def _chat_id_from_method(method: Any) -> int | None:
    chat_id = getattr(method, "chat_id", None)
    if chat_id is None:
        return None
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return None


def outbound_allowed(settings: Settings, method_name: str, chat_id: int | None) -> tuple[bool, str]:
    name = (method_name or "").strip()
    if name in BLOCKED_RUNTIME_METHODS:
        return False, "runtime_forbidden"
    if name in READ_OR_SETUP_METHODS:
        return True, "read"
    if name in MODERATION_METHODS:
        allowed = settings.test_chat_id_list()
        if not allowed or chat_id is None or int(chat_id) not in allowed:
            return False, "moderation_not_authorized"
        return True, "moderation"
    if name in REPLY_METHODS or name.startswith("send") or name.startswith("editMessage"):
        incoming = current_update_chat_id()
        if chat_id is None:
            # answerCallbackQuery has no chat_id; allow only during an update.
            if name == "answerCallbackQuery" and incoming is not None:
                return True, "callback"
            return False, "missing_chat"
        if incoming is not None and int(chat_id) == int(incoming):
            return True, "update_reply"
        if settings.is_configured_owner(chat_id) and incoming is not None and int(incoming) == int(chat_id):
            return True, "owner_private"
        return False, "chat_not_in_current_update"
    # Unknown methods fail closed (no unsolicited or novel calls).
    return False, "method_not_allowlisted"


def deny_outbound(method_name: str, reason: str, chat_id: int | None) -> None:
    logger.warning(
        "blocked outbound Bot API method=%s reason=%s chat_bound=%s",
        method_name,
        reason,
        chat_id is not None,
    )
    raise PermissionError(f"出站调用已被拒绝: {reason}")


class GuardedSession:
    """Wrap a real or local aiogram session and fail closed before any request."""

    def __init__(self, inner, settings: Settings) -> None:
        self.inner = inner
        self.settings = settings
        self.calls = getattr(inner, "calls", [])

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    async def close(self) -> None:
        closer = getattr(self.inner, "close", None)
        if closer is not None:
            await closer()

    async def __call__(self, bot, method, timeout=None):
        return await self.make_request(bot, method, timeout=timeout)

    async def make_request(self, bot, method, timeout=None):
        name = getattr(method, "__api_method__", "") or method.__class__.__name__
        chat_id = _chat_id_from_method(method)
        ok, reason = outbound_allowed(self.settings, name, chat_id)
        if not ok:
            deny_outbound(name, reason, chat_id)
        inner_call = getattr(self.inner, "__call__", None)
        if inner_call is not None and inner_call is not self.__call__:
            return await inner_call(bot, method, timeout=timeout)
        return await self.inner.make_request(bot, method, timeout=timeout)
