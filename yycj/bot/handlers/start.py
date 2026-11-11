""" /start 与 /help。分享深链 + 每次进入检查必订。"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.keyboards import admin_webapp_kb, remove_kb, welcome_kb
from bot.services import home_service, users
from bot.services.share_gate import join_kb, missing_required, open_card_kb, parse_start_payload

router = Router(name="start")

_OPEN_TEXT = {"s": "点下方打开资料。", "r": "点下方查看这条资料的评价。", "w": "点下方给这条资料写评价。", "q": "点下方打开资料，领取优惠。"}

HELP_TEXT = """<b>月影车姬</b>
点左下角「首页」打开应用。
管理员可发 /admin。"""


async def _force_join(message: Message, lamp_id: str | None = None) -> bool:
    user = message.from_user
    if not user:
        return False
    missing = await missing_required(user.id)
    if not missing:
        return False
    await message.answer(
        "请先加入以下频道/群，再点「我已关注」。",
        reply_markup=join_kb(missing, lamp_id or "home"),
    )
    return True


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if not user:
        return
    await users.ensure_user(
        user.id,
        username=user.username,
        full_name=user.full_name,
    )
    arg = (command.args if command else "") or ""
    if not arg and message.text:
        parts = message.text.split(maxsplit=1)
        arg = parts[1] if len(parts) > 1 else ""
    kind, lamp_id = parse_start_payload(arg)
    # the "我已关注" callback carries the kind too, so 查看评价 / 写评价 survive the follow check
    if await _force_join(message, (f"{kind}_{lamp_id}" if lamp_id and kind != "s" else lamp_id)):
        return
    if lamp_id:
        kb = open_card_kb(lamp_id, kind)
        await message.answer(_OPEN_TEXT.get(kind, _OPEN_TEXT["s"]), reply_markup=kb or remove_kb())
        return
    settings = get_settings()
    tip = "点左下角「首页」开始。" if settings.normalized_webapp_url else "请先配置 WEBAPP_URL。"
    buttons = []
    welcome_enabled = True
    config_revision = None
    welcome_config_loaded = False
    welcome = f"欢迎使用 <b>月影车姬</b>\n{tip}"
    try:
        site = await home_service.get_or_create_settings()
        welcome_config_loaded = True
        from bot.services.feature_registry import enabled
        welcome_enabled=enabled(site,"welcome")
        config_revision=site.get("config_revision")
        buttons = site.get("bot_welcome_buttons") or []
        custom = (site.get("bot_welcome_text") or "").strip()
        if custom:
            welcome = custom
    except Exception:
        pass
    from bot.services import execution_log
    record=await execution_log.begin('welcome','message',operation_id=f'welcome-{message.message_id}',actor_id=user.id,revision=config_revision,target='private' if message.chat.type=='private' else 'chat')
    if not welcome_config_loaded:await execution_log.finish(record,'failed','config_unavailable')
    elif not welcome_enabled:await execution_log.finish(record,'skipped','disabled')
    else:
        try:
            await message.answer(welcome, reply_markup=welcome_kb(buttons) if message.chat.type == "private" else remove_kb())
            await execution_log.finish(record,'sent')
        except Exception as exc:
            await execution_log.finish(record,'failed' if execution_log.reason(exc)!='delivery_unknown' else 'unknown',execution_log.reason(exc))
            raise
    if settings.is_admin(user.id):
        kb = admin_webapp_kb()
        await message.answer("管理员可点下方打开后台，或发 /admin。", reply_markup=kb)


@router.callback_query(F.data.startswith("share_go:"))
async def cb_share_go(query: CallbackQuery) -> None:
    user = query.from_user
    payload = (query.data or "").split(":", 1)[-1]
    kind, parsed = parse_start_payload(payload)
    lamp_id = parsed or payload
    if not user:
        await query.answer()
        return
    missing = await missing_required(user.id)
    if missing:
        await query.answer("还有频道未加入", show_alert=True)
        if query.message:
            await query.message.edit_reply_markup(reply_markup=join_kb(missing, payload or "home"))
        return
    await query.answer()
    if parsed:
        kb = open_card_kb(parsed, kind)
        if query.message:
            await query.message.answer("订阅已完成，" + _OPEN_TEXT.get(kind, _OPEN_TEXT["s"]), reply_markup=kb or remove_kb())
        return
    if query.message:
        await query.message.answer("订阅已完成，点左下角「首页」进入。", reply_markup=remove_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=remove_kb())
