"""Telegram Update 分流规则。

一个 Bot Token 只有一个 webhook，所有 update 都进同一个入口，这里决定每条 update
交给哪套系统处理：

- 月影车姬（aiogram3）：私聊主入口、Mini App、匿名会话、投稿/审核
- 闪秀（python-telegram-bot）：群组/频道管理、积分、抽奖、投票、报告

规则只依赖 update 的原始 JSON（不 import 任何一方的框架对象），可以单独测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class Target(str, Enum):
    YY = "yy"            # 只给月影车姬
    SX = "sx"            # 只给闪秀
    YY_FIRST = "yy_first"  # 先给月影；月影没有处理器接住时再给闪秀


# 私聊里明确属于月影的命令（其余命令归闪秀，闪秀的命令多得多）
YY_PRIVATE_COMMANDS = {
    "start",
    "admin",
    "search",
    "session_messages",
}

# 月影的回复键盘文本：点按钮发出来的是普通文本，必须留在月影
YY_REPLY_BUTTONS = {
    "🔍 搜索灯笼",
    "🔍 搜索",
    "🌕 我的月影",
    "🌕 我的",
    "✨ 点亮灯笼",
    "✨ 发布",
    "📝 月影报告",
    "📝 报告",
    "❓ 帮助",
    "📱 打开首页",
    "打开首页",
    "取消",
}

# 月影的 callback_data 前缀（全部来自 bot/handlers/*.py 的 F.data 过滤）
YY_CALLBACK_PREFIXES: tuple[str, ...] = (
    "admin_post_ok:",
    "admin_post_no:",
    "admin_report_ok:",
    "admin_report_no:",
    "admin_rev_",
    "report_lamp:",
    "search_city:",
    "search_price:",
    "session_request:",
    "session_accept:",
    "session_reject:",
    "session_end:",
    "session_praise:",
    "shift:",
    "share_go:",
)

GROUP_CHAT_TYPES = {"group", "supergroup", "channel"}

# 与聊天无关、只可能属于群管理侧（频道帖、成员变动、加入申请、助力）的 update
SX_ONLY_UPDATE_KEYS = (
    "channel_post",
    "edited_channel_post",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "chat_boost",
    "removed_chat_boost",
    "message_reaction",
    "message_reaction_count",
)

MESSAGE_KEYS = ("message", "edited_message", "business_message", "edited_business_message")


@dataclass(frozen=True)
class Decision:
    """分流结果。

    target: 交给谁
    reason: 判定依据，写进日志方便排查（不含任何用户内容）
    """

    target: Target
    reason: str


def _chat_type(obj: Mapping[str, Any]) -> str:
    chat = obj.get("chat") or {}
    return str(chat.get("type") or "")


def _command_of(text: str) -> str:
    """从 "/start@somebot arg" 里取出 "start"。"""
    head = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if not head.startswith("/"):
        return ""
    head = head[1:].split("@", 1)[0]
    return head.lower()


def decide(update: Mapping[str, Any]) -> Decision:
    """给一条 update 决定去向。认不出来的默认给闪秀（群管理侧更有兜底能力）。"""
    if not isinstance(update, Mapping):
        return Decision(Target.SX, "not-a-mapping")

    for key in SX_ONLY_UPDATE_KEYS:
        if key in update:
            return Decision(Target.SX, f"update:{key}")

    for key in MESSAGE_KEYS:
        msg = update.get(key)
        if not isinstance(msg, Mapping):
            continue
        ctype = _chat_type(msg)
        if ctype in GROUP_CHAT_TYPES:
            # 群和频道的一切都归闪秀（群管、积分、抽奖、频道帖同步）
            return Decision(Target.SX, f"{key}:{ctype}")

        text = str(msg.get("text") or msg.get("caption") or "")
        cmd = _command_of(text)
        if cmd:
            if cmd in YY_PRIVATE_COMMANDS:
                return Decision(Target.YY, f"private-command:{cmd}")
            return Decision(Target.SX, f"private-command:{cmd}")

        if text in YY_REPLY_BUTTONS:
            return Decision(Target.YY, "private:yy-reply-button")

        # 私聊普通消息：月影要先看（匿名会话中转、投稿对话、报告对话都在私聊），
        # 月影没人接再给闪秀（关键词自动回复、积分）。
        return Decision(Target.YY_FIRST, "private:try-yy-then-sx")

    cb = update.get("callback_query")
    if isinstance(cb, Mapping):
        data = str(cb.get("data") or "")
        ctype = _chat_type(cb.get("message") or {})
        if ctype in GROUP_CHAT_TYPES:
            return Decision(Target.SX, "callback:group")
        if data.startswith(YY_CALLBACK_PREFIXES):
            return Decision(Target.YY, "callback:yy-prefix")
        # 私聊里的其余按钮全部是闪秀的（积分商店、抽奖、投票、报告、分页…）
        return Decision(Target.SX, "callback:private")

    if "inline_query" in update or "chosen_inline_result" in update:
        # 两套系统都没做 inline 模式，交给闪秀兜住（不会回复）
        return Decision(Target.SX, "inline")

    return Decision(Target.SX, "default")


def yy_update_types() -> list[str]:
    """月影侧 aiogram 能用到的 update 类型（只用于日志/对照，webhook 仍注册全集）。"""
    return ["message", "edited_message", "callback_query", "chat_member", "my_chat_member", "channel_post"]
