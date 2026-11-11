"""
Report module – Telegram bot handlers.

Conversation flow (triggered by /start report_<user_id>):
  Questions are loaded dynamically from SystemConfig key ``report_questions``
  (JSON array of ``{"text": str, "required": bool}``).
  After all questions the bot optionally asks for a photo depending on
  the ``report_push_media`` config flag.

  States:
    STEP_QUESTION – collecting text answers one by one
    STEP_PHOTO    – waiting for the user to send a photo (or skip)
    STEP_CONFIRM  – showing a summary and waiting for confirm/cancel

View flow (triggered by /start view_<user_id>):
  Lists all approved reports for the target user and links to channel messages.

Audit callbacks (inline keyboard in admin group):
  audit_approve_<id>  → publish to channel, notify submitter
  audit_reject_<id>   → update status, notify submitter
"""

import json
import logging
import asyncio
import re as _re
import warnings

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

logger = logging.getLogger(__name__)

# Conversation states
STEP_QUESTION, STEP_PHOTO, STEP_CONFIRM, STEP_TARGET = range(4)

# Key used to store the target user_id inside conversation user_data
_TARGET_KEY = '_report_target_user_id'

# Matches https://t.me/username, http://t.me/username, t.me/username
# Telegram usernames are 5–32 characters: letters, digits, underscores.
_TG_URL_RE = _re.compile(r'^(?:https?://)?t\.me/([A-Za-z0-9_]{5,32})\s*$', _re.IGNORECASE)

_DEFAULT_QUESTIONS = [
    {"text": "请问故障发生的时间是？", "required": True,
     "hint": "例如：2024-01-15 14:30"},
    {"text": "请描述具体的故障现象？", "required": True,
     "hint": "例如：设备无法启动，屏幕显示错误代码 E01"},
    {"text": "最终的处理结果是什么？", "required": True,
     "hint": "例如：已更换电源模块，设备恢复正常"},
]

_DEFAULT_PHOTO_PROMPT = '请发送预约聊天截图或付款截图 📷（必填）'

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_flask_app(context: ContextTypes.DEFAULT_TYPE):
    """Return the Flask app stored in bot_data by run_bot()."""
    return context.application.bot_data.get('flask_app')


def _get_clone_id(context: ContextTypes.DEFAULT_TYPE):
    """Return the clone_id for the running bot (None for the main bot)."""
    return context.application.bot_data.get('clone_id')


def _scoped_key(key: str, clone_id) -> str:
    """Return a clone-scoped config key, or the bare key for the main bot."""
    if clone_id:
        return f'clone_{clone_id}_{key}'
    return key


def _db_get_config(flask_app, key: str, default: str = '', clone_id=None) -> str:
    with flask_app.app_context():
        from app.models import SystemConfig
        return SystemConfig.get_value(_scoped_key(key, clone_id), default)


def _group_user_to_namespace(gu):
    """Convert a GroupUser ORM row to a SimpleNamespace compatible with GroupMember.

    The result exposes ``user_id``, ``username``, ``first_name``, and
    ``last_name`` attributes, sourced from ``gu.tg_id`` and the JSON stored in
    ``gu.profile_data``.

    ``username`` is resolved by priority:
      1. ``tg_username`` key (set by activity-tracking code)
      2. ``username`` key
      3. Any string field whose value starts with ``@`` (admin-entered @handle)
    """
    from types import SimpleNamespace
    try:
        pd = json.loads(gu.profile_data or '{}')
    except (ValueError, TypeError):
        pd = {}
    # Resolve username: check dedicated keys first, then any @-prefixed value.
    username = pd.get('tg_username') or pd.get('username') or ''
    if not username:
        for v in pd.values():
            if isinstance(v, str) and v.startswith('@') and len(v) > 1:
                username = v[1:]  # strip leading '@' for consistency
                break
    return SimpleNamespace(
        user_id=gu.tg_id,
        username=username,
        first_name=pd.get('first_name') or pd.get('name') or '',
        last_name=pd.get('last_name') or '',
    )


def _load_questions(flask_app, clone_id=None) -> list:
    """Return the configured question list, falling back to defaults."""
    raw = _db_get_config(flask_app, 'report_questions', '', clone_id=clone_id)
    try:
        qs = json.loads(raw) if raw else _DEFAULT_QUESTIONS
        return qs if qs else _DEFAULT_QUESTIONS
    except (ValueError, TypeError):
        return _DEFAULT_QUESTIONS


def _push_media_enabled(flask_app, clone_id=None) -> bool:
    """Return True if channel push should include the photo."""
    val = _db_get_config(flask_app, 'report_push_media', 'true', clone_id=clone_id)
    return val.lower() != 'false'


def _require_photo_enabled(flask_app, clone_id=None) -> bool:
    """Return True if the report flow should ask the user to submit a photo."""
    val = _db_get_config(flask_app, 'report_require_photo', 'true', clone_id=clone_id)
    return val.lower() != 'false'


def _build_answers_block(answers: list) -> str:
    """Format a list of {question, answer} dicts as a readable text block."""
    lines = []
    for item in answers:
        q = item.get('question', '')
        a = item.get('answer', '')
        lines.append(f'❓ {q}\n💬 {a}')
    return '\n\n'.join(lines)


def _build_push_caption(flask_app, report_id: int, answers: list, clone_id=None) -> str:
    """Build the channel-push caption from the configured template.

    Supported placeholders:
      {report_id} – numeric report ID
      {answers}   – all Q&A pairs formatted as a block
      {q1}, {q2}, … {qN} – individual answer for question N (1-indexed)
    """
    template = _db_get_config(
        flask_app,
        'report_push_template',
        '📋 <b>认证用户报告 #{report_id}</b>\n\n{answers}',
        clone_id=clone_id,
    )
    answers_block = _build_answers_block(answers)
    result = template.replace('{report_id}', str(report_id)).replace('{answers}', answers_block)
    # Replace individual answer placeholders {q1}, {q2}, …
    # Answers are always appended in question order; skipped optional questions
    # append '' so the list index always maps 1-to-1 with the question number.
    for i, item in enumerate(answers, 1):
        result = result.replace(f'{{q{i}}}', item.get('answer', ''))
    return result


def _prompt_for_question(questions: list, idx: int, total: int) -> str:
    q = questions[idx]
    optional_hint = '' if q.get('required', True) else '（选填，输入 - 可跳过）'
    text = f'第{idx + 1}/{total}步：{q["text"]}{optional_hint}'
    hint = q.get('hint', '').strip()
    if hint:
        text += f'\n\n💡 填写提示：{hint}'
    return text


def _get_photo_prompt(flask_app, clone_id=None) -> str:
    """Return the configured photo-step prompt text."""
    return _db_get_config(flask_app, 'report_photo_prompt', _DEFAULT_PHOTO_PROMPT, clone_id=clone_id)


def _format_user_identity(user_id, member=None, from_user=None) -> str:
    """Return a rich identity string: 昵称 @username ｜ ID: xxx

    Args:
        user_id: Telegram numeric user ID (always included).
        member: GroupMember ORM row (used if from_user is None).
        from_user: Live Telegram User object (takes priority over member).
    """
    parts = []
    first_name = last_name = username = None
    if from_user is not None:
        first_name = from_user.first_name or ''
        last_name = from_user.last_name or ''
        username = from_user.username
    elif member is not None:
        first_name = member.first_name or ''
        last_name = member.last_name or ''
        username = member.username
    if first_name:
        name = ' '.join(filter(None, [first_name, last_name]))
        if name:
            parts.append(name)
    if username:
        parts.append(f'@{username}')
    parts.append(f'ID: <code>{user_id}</code>')
    return ' ｜ '.join(parts)


async def _try_delete_message(bot, chat_id: int, message_id: int) -> None:
    """Silently delete a message, ignoring errors (too old, already deleted, etc.)."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _edit_or_reply(context, message, chat_id: int, text: str, **kwargs):
    """Edit the stored q_msg_id if available, otherwise send a new message.

    Updates ``context.user_data['q_msg_id']`` so subsequent steps always point
    at the single persistent question/status card.
    """
    q_msg_id = context.user_data.get('q_msg_id')
    if q_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=q_msg_id, text=text, **kwargs
            )
            return
        except Exception:
            pass
    sent = await message.reply_text(text, **kwargs)
    context.user_data['q_msg_id'] = sent.message_id


def _build_channel_link(channel: str, msg_id: int) -> str:
    """Build a t.me link for a channel message.

    For public channels (e.g., @channelname or channelname), returns
    ``https://t.me/channelname/msg_id``.
    For private channels whose ID starts with ``-100``, returns
    ``https://t.me/c/{numeric_id}/msg_id`` which works for private channels.
    """
    chan = channel.strip()
    stripped = chan.lstrip('-')
    if stripped.isdigit():
        # Numeric ID – private or supergroup channel
        numeric_id = stripped
        if numeric_id.startswith('100'):
            numeric_id = numeric_id[3:]
        return f'https://t.me/c/{numeric_id}/{msg_id}'
    # Public username
    chan = chan.removeprefix('@')
    return f'https://t.me/{chan}/{msg_id}'


# ──────────────────────────────────────────────────────────────────────────────
# ConversationHandler – write a report
# ──────────────────────────────────────────────────────────────────────────────

async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: /start report_<user_id>"""
    import re as _re
    text = update.message.text or ''
    m = _re.search(r'/start report_(\d+)', text)
    if not m:
        await update.message.reply_text('❌ 链接无效，请重新点击报告链接。')
        return ConversationHandler.END

    target_user_id = int(m.group(1))
    context.user_data[_TARGET_KEY] = target_user_id

    flask_app = _get_flask_app(context)
    if flask_app is None:
        await update.message.reply_text('❌ 服务暂时不可用，请稍后再试。')
        return ConversationHandler.END

    clone_id = _get_clone_id(context)
    questions = _load_questions(flask_app, clone_id=clone_id)
    context.user_data['questions'] = questions
    context.user_data['answers'] = []
    context.user_data['current_q_idx'] = 0

    total = len(questions)
    # Delete the /start command message to keep the chat clean.
    await _try_delete_message(context.bot, update.effective_chat.id, update.message.message_id)
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f'📝 开始填写报告（共{total}步）\n\n' + _prompt_for_question(questions, 0, total),
    )
    context.user_data['q_msg_id'] = msg.message_id
    return STEP_QUESTION


async def report_step_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flask_app = _get_flask_app(context)
    questions = context.user_data.get('questions', _DEFAULT_QUESTIONS)
    idx = context.user_data.get('current_q_idx', 0)
    answer = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Always delete the user's answer to keep the chat clean.
    await _try_delete_message(context.bot, chat_id, update.message.message_id)

    # Validate required questions: reject empty/whitespace-only answers.
    if questions[idx].get('required', True) and answer == '':
        await _edit_or_reply(
            context, update.message, chat_id,
            '⚠️ 此项为必填，请输入有效内容。\n\n' + _prompt_for_question(questions, idx, len(questions)),
        )
        return STEP_QUESTION

    # Handle optional-skip marker
    if answer == '-' and not questions[idx].get('required', True):
        answer = ''

    context.user_data['answers'].append(answer)
    idx += 1
    context.user_data['current_q_idx'] = idx

    total = len(questions)
    if idx < total:
        await _edit_or_reply(
            context, update.message, chat_id,
            _prompt_for_question(questions, idx, total),
        )
        return STEP_QUESTION

    # All questions answered – check if photo is needed
    clone_id = _get_clone_id(context)
    if flask_app and _require_photo_enabled(flask_app, clone_id=clone_id):
        photo_prompt = _get_photo_prompt(flask_app, clone_id=clone_id)
        await _edit_or_reply(
            context, update.message, chat_id,
            f'第{total + 1}步：{photo_prompt}',
        )
        return STEP_PHOTO

    # No photo required – go to confirmation summary
    return await _show_confirm_edit(update, context)


async def _show_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show a summary of all answers and ask the user to confirm or cancel."""
    flask_app = _get_flask_app(context)
    clone_id = _get_clone_id(context)
    questions = context.user_data.get('questions', _DEFAULT_QUESTIONS)
    raw_answers = context.user_data.get('answers', [])
    photo_file_id = context.user_data.get('photo_file_id')

    answers = [
        {"question": questions[i]["text"], "answer": raw_answers[i] if i < len(raw_answers) else ''}
        for i in range(len(questions))
    ]

    if flask_app:
        preview_text = _build_push_caption(flask_app, '预览', answers, clone_id=clone_id)
    else:
        preview_text = _build_answers_block(answers)

    lines = ['📝 <b>请确认以下报告内容（预览）：</b>\n', preview_text]
    if photo_file_id:
        lines.append('\n📷 已附带截图')

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton('✅ 确认提交', callback_data='report_confirm_submit'),
        InlineKeyboardButton('✏️ 重新填写', callback_data='report_restart'),
    ]])
    msg = '\n\n'.join(lines)
    await update.message.reply_text(msg, parse_mode='HTML', reply_markup=keyboard)
    return STEP_CONFIRM


async def _show_confirm_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show confirmation summary by editing the persistent question card (q_msg_id)."""
    flask_app = _get_flask_app(context)
    clone_id = _get_clone_id(context)
    questions = context.user_data.get('questions', _DEFAULT_QUESTIONS)
    raw_answers = context.user_data.get('answers', [])
    photo_file_id = context.user_data.get('photo_file_id')

    answers = [
        {"question": questions[i]["text"], "answer": raw_answers[i] if i < len(raw_answers) else ''}
        for i in range(len(questions))
    ]

    if flask_app:
        preview_text = _build_push_caption(flask_app, '预览', answers, clone_id=clone_id)
    else:
        preview_text = _build_answers_block(answers)

    lines = ['📝 <b>请确认以下报告内容（预览）：</b>\n', preview_text]
    if photo_file_id:
        lines.append('\n📷 已附带截图')

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton('✅ 确认提交', callback_data='report_confirm_submit'),
        InlineKeyboardButton('✏️ 重新填写', callback_data='report_restart'),
    ]])
    msg_text = '\n\n'.join(lines)
    chat_id = update.effective_chat.id
    q_msg_id = context.user_data.get('q_msg_id')
    if q_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=q_msg_id,
                text=msg_text, parse_mode='HTML', reply_markup=keyboard,
            )
            return STEP_CONFIRM
        except Exception:
            pass
    sent = await update.message.reply_text(msg_text, parse_mode='HTML', reply_markup=keyboard)
    context.user_data['q_msg_id'] = sent.message_id
    return STEP_CONFIRM


async def report_step_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flask_app = _get_flask_app(context)
    chat_id = update.effective_chat.id
    if flask_app is None:
        await update.message.reply_text('❌ 服务暂时不可用，请稍后再试。')
        return ConversationHandler.END

    # Accept photo or document-as-image
    photo_file_id = None
    if update.message.photo:
        photo_file_id = update.message.photo[-1].file_id
    elif update.message.document and update.message.document.mime_type and \
            update.message.document.mime_type.startswith('image/'):
        photo_file_id = update.message.document.file_id
    else:
        # Delete the invalid message and show an error on the question card.
        await _try_delete_message(context.bot, chat_id, update.message.message_id)
        total = len(context.user_data.get('questions', _DEFAULT_QUESTIONS))
        photo_prompt = _get_photo_prompt(flask_app, clone_id=_get_clone_id(context))
        await _edit_or_reply(
            context, update.message, chat_id,
            f'❌ 请发送一张图片。\n\n第{total + 1}步：{photo_prompt}',
        )
        return STEP_PHOTO

    # Delete the photo message to keep the chat clean.
    await _try_delete_message(context.bot, chat_id, update.message.message_id)
    context.user_data['photo_file_id'] = photo_file_id
    return await _show_confirm_edit(update, context)


async def report_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline-keyboard actions from the confirmation/skip/restart screen."""
    query = update.callback_query
    await query.answer()
    data = query.data or ''

    if data == 'report_skip_photo':
        # The skip button is no longer shown, but handle any stale callbacks
        # gracefully by re-displaying the confirmation screen.
        return await _show_confirm_from_callback(query, context)

    if data == 'report_restart':
        # Restart from question 0 keeping the same target user and questions
        context.user_data['answers'] = []
        context.user_data['current_q_idx'] = 0
        context.user_data.pop('photo_file_id', None)
        questions = context.user_data.get('questions', _DEFAULT_QUESTIONS)
        total = len(questions)
        await query.edit_message_text(
            f'📝 重新填写报告（共{total}步）\n\n' + _prompt_for_question(questions, 0, total)
        )
        # The confirm message is now the new question card.
        context.user_data['q_msg_id'] = query.message.message_id
        return STEP_QUESTION

    if data == 'report_confirm_submit':
        photo_file_id = context.user_data.get('photo_file_id')
        # Replace the confirm message with a "submitting…" notice
        await query.edit_message_text('⏳ 正在提交报告，请稍候…')
        return await _save_and_notify_from_callback(query, context, photo_file_id)

    return STEP_CONFIRM


async def _show_confirm_from_callback(query, context: ContextTypes.DEFAULT_TYPE):
    """Same as _show_confirm but edits an existing message instead of sending a new one."""
    flask_app = _get_flask_app(context)
    clone_id = _get_clone_id(context)
    questions = context.user_data.get('questions', _DEFAULT_QUESTIONS)
    raw_answers = context.user_data.get('answers', [])
    photo_file_id = context.user_data.get('photo_file_id')

    answers = [
        {"question": questions[i]["text"], "answer": raw_answers[i] if i < len(raw_answers) else ''}
        for i in range(len(questions))
    ]

    if flask_app:
        preview_text = _build_push_caption(flask_app, '预览', answers, clone_id=clone_id)
    else:
        preview_text = _build_answers_block(answers)

    lines = ['📝 <b>请确认以下报告内容（预览）：</b>\n', preview_text]
    if photo_file_id:
        lines.append('\n📷 已附带截图')
    else:
        lines.append('\n📷 无截图')

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton('✅ 确认提交', callback_data='report_confirm_submit'),
        InlineKeyboardButton('✏️ 重新填写', callback_data='report_restart'),
    ]])
    await query.edit_message_text('\n\n'.join(lines), parse_mode='HTML', reply_markup=keyboard)
    return STEP_CONFIRM


async def _save_and_notify_from_callback(query, context: ContextTypes.DEFAULT_TYPE, photo_file_id):
    """Like _save_and_notify but called from a CallbackQuery (no update.message)."""
    flask_app = _get_flask_app(context)
    clone_id = _get_clone_id(context)
    questions = context.user_data.get('questions', _DEFAULT_QUESTIONS)
    raw_answers = context.user_data.get('answers', [])

    answers = [
        {"question": questions[i]["text"], "answer": raw_answers[i]}
        for i in range(min(len(questions), len(raw_answers)))
    ]

    submitter_id = query.from_user.id if query.from_user else None
    target_user_id = context.user_data.get(_TARGET_KEY)

    def _save():
        with flask_app.app_context():
            from app.models import UserReport, GroupMember, GroupUser
            from app import db
            r = UserReport(
                user_id=target_user_id,
                submitter_id=submitter_id,
                fault_time=raw_answers[0] if len(raw_answers) > 0 else '',
                fault_desc=raw_answers[1] if len(raw_answers) > 1 else '',
                process_result=raw_answers[2] if len(raw_answers) > 2 else '',
                photo_file_id=photo_file_id,
                answers=json.dumps(answers, ensure_ascii=False),
                status='pending',
            )
            db.session.add(r)
            db.session.commit()
            # Load member info for the target user to enrich the admin notification.
            # Check GroupMember first (Telegram-synced), then fall back to GroupUser
            # (admin-added certified users who may not be in GroupMember).
            target_member = GroupMember.query.filter_by(user_id=target_user_id).first()
            if target_member is None:
                gu = GroupUser.query.filter_by(tg_id=target_user_id).first()
                if gu:
                    target_member = _group_user_to_namespace(gu)
            return r.id, target_member

    loop = asyncio.get_running_loop()
    report_id, target_member = await loop.run_in_executor(None, _save)

    # Notify admin group
    admin_group_id_str = _db_get_config(flask_app, 'admin_group_id', '', clone_id=clone_id)
    if admin_group_id_str:
        try:
            admin_group_id = int(admin_group_id_str)
            template_text = _build_push_caption(flask_app, report_id, answers, clone_id=clone_id)
            target_identity = _format_user_identity(target_user_id, member=target_member)
            submitter_identity = _format_user_identity(submitter_id, from_user=query.from_user)
            caption = (
                f"📋 <b>新报告待审核 #{report_id}</b>\n\n"
                f"👤 被提交人：{target_identity}\n"
                f"📤 提交人：{submitter_identity}\n\n"
                f"{template_text}"
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton('✅ 通过', callback_data=f'audit_approve_{report_id}'),
                    InlineKeyboardButton('❌ 驳回', callback_data=f'audit_reject_{report_id}'),
                ]
            ])
            if photo_file_id:
                await query.get_bot().send_photo(
                    chat_id=admin_group_id,
                    photo=photo_file_id,
                    caption=caption,
                    parse_mode='HTML',
                    reply_markup=keyboard,
                )
            else:
                await query.get_bot().send_message(
                    chat_id=admin_group_id,
                    text=caption,
                    parse_mode='HTML',
                    reply_markup=keyboard,
                )
        except Exception as e:
            logger.error(f'Failed to notify admin group: {e}')

    await query.edit_message_text('🎉 报告已提交！\n\n管理员审核通过后，将发布到报告频道，并通知您。')
    context.user_data.clear()
    return ConversationHandler.END


async def report_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Always clear report state first, regardless of what happens next.
    context.user_data.clear()
    text = update.message.text or ''
    # When /start report_NNN is sent mid-conversation (e.g. user clicked the
    # deep-link again), restart the report flow instead of just canceling so the
    # user doesn't have to click twice.
    import re as _re
    if _re.match(r'^/start report_\d+', text):
        return await report_start(update, context)
    # When a plain /start (without report_ param) is sent mid-conversation,
    # forward directly to cmd_start so the user reaches admin welcome in one step
    # instead of having to send /start twice.  cmd_start does not use user_data,
    # so clearing it above is safe before forwarding.
    if text.startswith('/start') and 'report_' not in text:
        cmd_start_fn = context.application.bot_data.get('cmd_start')
        if cmd_start_fn:
            return await cmd_start_fn(update, context)
        logger.warning('report_cancel: cmd_start not found in bot_data; falling back to cancel message')
    await update.message.reply_text('❌ 已取消报告填写。')
    return ConversationHandler.END


# ──────────────────────────────────────────────────────────────────────────────
# View reports handler
# ──────────────────────────────────────────────────────────────────────────────

async def view_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: /start view_<user_id>"""
    import re as _re
    text = update.message.text or ''
    m = _re.search(r'/start view_(\d+)', text)
    if not m:
        await update.message.reply_text('❌ 链接无效，请重新点击查看链接。')
        return

    target_user_id = int(m.group(1))
    flask_app = _get_flask_app(context)
    if flask_app is None:
        await update.message.reply_text('❌ 服务暂时不可用，请稍后再试。')
        return

    clone_id = _get_clone_id(context)

    def _get_reports():
        with flask_app.app_context():
            from app.models import UserReport, SystemConfig, GroupMember, GroupUser
            reports = UserReport.query.filter_by(
                user_id=target_user_id, status='approved'
            ).order_by(UserReport.created_at.desc()).all()
            channel = SystemConfig.get_value(_scoped_key('report_channel', clone_id), '')
            member = GroupMember.query.filter_by(user_id=target_user_id).first()
            if member is None:
                # Fall back to GroupUser for certified users not present in GroupMember
                gu = GroupUser.query.filter_by(tg_id=target_user_id).first()
                if gu:
                    member = _group_user_to_namespace(gu)
            return reports, channel, member

    loop = asyncio.get_running_loop()
    reports, channel, member = await loop.run_in_executor(None, _get_reports)

    # Build a human-readable identity string
    def _user_identity(member) -> str:
        parts = []
        if member:
            name = ' '.join(filter(None, [member.first_name, member.last_name]))
            if name:
                parts.append(name)
            if member.username:
                parts.append(f'@{member.username}')
        parts.append(f'ID: {target_user_id}')
        return ' ｜ '.join(parts)

    identity = _user_identity(member)

    if not reports:
        await update.message.reply_text(
            f'📭 该认证用户（{identity}）目前还没有任何历史报告。'
        )
        return

    lines = [f'📋 <b>{identity} 的历史报告（共 {len(reports)} 份）：</b>\n']
    for i, r in enumerate(reports, 1):
        date_str = r.created_at.strftime('%Y-%m-%d') if r.created_at else '未知日期'
        line = f'🔹 {i}. {date_str} ｜ {r.fault_time or ""}'
        if r.channel_msg_id and channel and not r.channel_msg_deleted:
            link = _build_channel_link(channel, r.channel_msg_id)
            line += f'\n   👉 <a href="{link}">点击查看报告详情</a>'
        lines.append(line)

    await update.message.reply_text('\n'.join(lines), parse_mode='HTML', disable_web_page_preview=True)


# ──────────────────────────────────────────────────────────────────────────────
# Audit callback handler
# ──────────────────────────────────────────────────────────────────────────────

async def audit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ''
    flask_app = _get_flask_app(context)
    clone_id = _get_clone_id(context)

    # Helper: send a new reply in the admin group with the audit result while
    # keeping the original submission message intact.  Only the inline keyboard
    # on the original message is updated (buttons removed / replaced).
    async def _post_audit_result(text: str, update_keyboard=None):
        """Send a new reply to the original admin message with the audit result.

        ``update_keyboard`` replaces the original message's keyboard (pass None
        to strip the buttons entirely).  A new reply message carries the result text.
        """
        try:
            await query.edit_message_reply_markup(reply_markup=update_keyboard)
        except Exception as e:
            logger.debug('Could not update original message keyboard: %s', e)
        if query.message:
            try:
                await query.get_bot().send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    parse_mode='HTML',
                    reply_to_message_id=query.message.message_id,
                )
            except Exception as e:
                logger.warning('Could not send audit result reply: %s', e)

    if flask_app is None:
        await query.answer('❌ 服务暂时不可用。', show_alert=True)
        return

    if data.startswith('audit_approve_'):
        report_id = int(data.split('audit_approve_')[1])
        action = 'approve'
    elif data.startswith('audit_reject_'):
        report_id = int(data.split('audit_reject_')[1])
        action = 'reject'
    elif data.startswith('audit_delete_channel_'):
        report_id = int(data.split('audit_delete_channel_')[1])
        action = 'delete_channel'
    else:
        return

    loop = asyncio.get_running_loop()

    # Handle channel-message deletion separately (no full report data needed)
    if action == 'delete_channel':
        def _get_channel_info():
            with flask_app.app_context():
                from app.models import UserReport
                r = UserReport.query.get(report_id)
                if r:
                    return r.channel_msg_id, r.channel_msg_deleted
                return None, None

        msg_id, already_deleted = await loop.run_in_executor(None, _get_channel_info)

        if already_deleted:
            await query.answer(f'ℹ️ 报告 #{report_id} 的频道消息已被删除。', show_alert=True)
            return

        channel = _db_get_config(flask_app, 'report_channel', '', clone_id=clone_id)
        if channel and msg_id:
            try:
                await query.get_bot().delete_message(chat_id=channel, message_id=msg_id)
            except Exception as e:
                logger.warning(f'Failed to delete channel message for report #{report_id}: {e}')

        def _mark_deleted():
            with flask_app.app_context():
                from app.models import UserReport
                from app import db
                r = UserReport.query.get(report_id)
                if r:
                    r.channel_msg_deleted = True
                    db.session.commit()

        await loop.run_in_executor(None, _mark_deleted)
        await _post_audit_result(f'🗑️ 报告 #{report_id} 的频道消息已删除。')
        return

    def _get_report():
        with flask_app.app_context():
            from app.models import UserReport, GroupMember
            r = UserReport.query.get(report_id)
            if r is None:
                return None
            answers = []
            if r.answers:
                try:
                    answers = json.loads(r.answers)
                except (ValueError, TypeError):
                    pass
            # Fall back to legacy columns when answers JSON is absent
            if not answers:
                if r.fault_time:
                    answers.append({"question": "故障时间", "answer": r.fault_time})
                if r.fault_desc:
                    answers.append({"question": "故障现象", "answer": r.fault_desc})
                if r.process_result:
                    answers.append({"question": "处理结果", "answer": r.process_result})
            # Load member info for both users in a single query to enrich audit/channel messages
            user_ids = list(filter(None, {r.user_id, r.submitter_id}))
            members = {m.user_id: m for m in GroupMember.query.filter(GroupMember.user_id.in_(user_ids)).all()}
            target_identity = _format_user_identity(r.user_id, member=members.get(r.user_id))
            submitter_identity = _format_user_identity(r.submitter_id, member=members.get(r.submitter_id)) if r.submitter_id else None
            return {
                'id': r.id,
                'user_id': r.user_id,
                'submitter_id': r.submitter_id,
                'photo_file_id': r.photo_file_id,
                'status': r.status,
                'answers': answers,
                'target_identity': target_identity,
                'submitter_identity': submitter_identity,
            }

    report = await loop.run_in_executor(None, _get_report)

    if report is None:
        await query.answer('❌ 报告不存在。', show_alert=True)
        return

    if report['status'] != 'pending':
        await query.answer(
            f'ℹ️ 该报告已被处理（当前状态：{report["status"]}）。', show_alert=True
        )
        return

    reviewer_name = query.from_user.full_name or str(query.from_user.id)

    if action == 'reject':
        def _reject():
            with flask_app.app_context():
                from app.models import UserReport
                from app import db
                r = UserReport.query.get(report_id)
                if r:
                    r.status = 'rejected'
                    db.session.commit()

        await loop.run_in_executor(None, _reject)
        await _post_audit_result(f'❌ 报告 #{report_id} 已被 {reviewer_name} 驳回。')
        submitter_id = report.get('submitter_id')
        if submitter_id:
            try:
                await query.get_bot().send_message(
                    chat_id=submitter_id,
                    text=(
                        f'❌ 抱歉，您提交的报告 #{report_id} 未通过审核。\n\n'
                        '请确保描述清晰并包含清晰的现场图片，欢迎重新提交。'
                    ),
                )
            except Exception as e:
                logger.warning(f'Failed to notify submitter {submitter_id} of rejection: {e}')
        return

    # approve – build caption from template
    channel = _db_get_config(flask_app, 'report_channel', '', clone_id=clone_id)
    channel_msg_id = None
    push_media = _push_media_enabled(flask_app, clone_id=clone_id)
    template_caption = _build_push_caption(flask_app, report_id, report['answers'], clone_id=clone_id)
    # Prepend target/submitter identity to the channel push so readers see who is involved
    identity_header = f"👤 被提交人：{report['target_identity']}"
    if report.get('submitter_identity'):
        identity_header += f"\n📤 提交人：{report['submitter_identity']}"
    caption = f"{identity_header}\n\n{template_caption}"

    if channel:
        try:
            if push_media and report['photo_file_id']:
                sent = await query.get_bot().send_photo(
                    chat_id=channel,
                    photo=report['photo_file_id'],
                    caption=caption,
                    parse_mode='HTML',
                )
            else:
                sent = await query.get_bot().send_message(
                    chat_id=channel,
                    text=caption,
                    parse_mode='HTML',
                )
            channel_msg_id = sent.message_id
        except Exception as e:
            logger.error(f'Failed to publish report to channel: {e}')

    def _approve(msg_id):
        with flask_app.app_context():
            from app.models import UserReport, SystemConfig, UserPoints, PointsLog, BotGroup
            from app import db

            r = UserReport.query.get(report_id)
            if r:
                r.status = 'approved'
                if msg_id:
                    r.channel_msg_id = msg_id
                db.session.commit()

            # Award points to the submitter if configured
            reward_pts = 0
            try:
                reward_pts = int(SystemConfig.get_value(
                    _scoped_key('report_approval_points', clone_id), '0') or '0')
            except (ValueError, TypeError):
                reward_pts = 0

            awarded = 0
            if reward_pts > 0 and report.get('submitter_id'):
                group_chat_id = SystemConfig.get_value(
                    _scoped_key('report_points_group_id', clone_id), '').strip()
                target_group = None
                if group_chat_id:
                    target_group = BotGroup.query.filter_by(chat_id=group_chat_id).first()

                if target_group:
                    pts = UserPoints.query.filter_by(
                        group_id=target_group.id,
                        user_id=report['submitter_id'],
                    ).first()
                    if pts is None:
                        pts = UserPoints(
                            group_id=target_group.id,
                            user_id=report['submitter_id'],
                            points_balance=0,
                        )
                        db.session.add(pts)
                    pts.points_balance += reward_pts
                    db.session.add(PointsLog(
                        group_id=target_group.id,
                        user_id=report['submitter_id'],
                        points_change=reward_pts,
                        reason=f'报告审核通过 #{report_id}',
                        balance_after=pts.points_balance,
                    ))
                    db.session.commit()
                    awarded = reward_pts

            return awarded

    awarded_pts = await loop.run_in_executor(None, _approve, channel_msg_id)
    delete_keyboard = None
    if channel_msg_id:
        delete_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton('🗑️ 从频道删除', callback_data=f'audit_delete_channel_{report_id}'),
        ]])
    await _post_audit_result(
        f'✅ 报告 #{report_id} 已由 {reviewer_name} 审核通过并发布到频道。',
        update_keyboard=delete_keyboard,
    )
    submitter_id = report.get('submitter_id')
    if submitter_id:
        try:
            channel = _db_get_config(flask_app, 'report_channel', '', clone_id=clone_id)
            msg = f'🎉 您提交的报告 #{report_id} 已审核通过'
            if channel_msg_id and channel:
                link = _build_channel_link(channel, channel_msg_id)
                msg += f'！\n\n👉 <a href="{link}">点击查看已发布报告</a>'
            if awarded_pts:
                msg += f'\n\n🎁 奖励积分：+{awarded_pts}'
            await query.get_bot().send_message(
                chat_id=submitter_id,
                text=msg,
                parse_mode='HTML',
            )
        except Exception as e:
            logger.warning(f'Failed to notify submitter {submitter_id} of approval: {e}')


# ──────────────────────────────────────────────────────────────────────────────
# 私聊"写报告"入口 – 用户输入"写报告"后由机器人引导输入被提交人
# ──────────────────────────────────────────────────────────────────────────────

async def report_start_by_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: user sends '写报告' in a private chat."""
    flask_app = _get_flask_app(context)
    if flask_app is None:
        await update.message.reply_text('❌ 服务暂时不可用，请稍后再试。')
        return ConversationHandler.END

    # Delete the trigger message and send a single persistent card.
    await _try_delete_message(context.bot, update.effective_chat.id, update.message.message_id)
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            '📝 请发送被提交人的用户名或 ID，例如：\n'
            '  • <code>@zhangsan</code>\n'
            '  • <code>https://t.me/zhangsan</code>\n'
            '  • <code>123456789</code>\n\n'
            '发送 /cancel 可取消。'
        ),
        parse_mode='HTML',
    )
    context.user_data['q_msg_id'] = msg.message_id
    return STEP_TARGET


async def report_receive_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive @username or numeric user_id, look up GroupMember, then start questions."""
    text = (update.message.text or '').strip()

    flask_app = _get_flask_app(context)
    if flask_app is None:
        await update.message.reply_text('❌ 服务暂时不可用，请稍后再试。')
        return ConversationHandler.END

    # Parse input: "@username" / "https://t.me/username" / "t.me/username" → extract username;
    # plain digits → int user_id
    lookup_username = None
    lookup_user_id = None
    if text.startswith('@'):
        lookup_username = text[1:].strip()
    else:
        m = _TG_URL_RE.match(text)
        if m:
            lookup_username = m.group(1)
        elif text.lstrip('-').isdigit():
            try:
                lookup_user_id = int(text)
            except ValueError:
                pass

    if not lookup_username and lookup_user_id is None:
        await _try_delete_message(context.bot, update.effective_chat.id, update.message.message_id)
        await _edit_or_reply(
            context, update.message, update.effective_chat.id,
            '⚠️ 格式不正确，请发送用户名（如 @zhangsan 或 https://t.me/zhangsan）或数字 ID（如 123456789）。\n\n'
            '📝 请发送被提交人的用户名或 ID，例如：\n'
            '  • <code>@zhangsan</code>\n'
            '  • <code>https://t.me/zhangsan</code>\n'
            '  • <code>123456789</code>\n\n'
            '发送 /cancel 可取消。',
            parse_mode='HTML',
        )
        return STEP_TARGET

    def _find_member():
        import json as _json
        from app.models import GroupMember, GroupUser
        from sqlalchemy import func
        with flask_app.app_context():
            if lookup_user_id is not None:
                m = GroupMember.query.filter_by(user_id=lookup_user_id).first()
            else:
                # Username lookup – case-insensitive
                m = GroupMember.query.filter(
                    func.lower(GroupMember.username) == lookup_username.lower()
                ).first()
            if m is not None:
                return m
            # Fallback: search GroupUser (admin-added verified users) which may not
            # be present in the GroupMember (Telegram-sync) table.
            if lookup_user_id is not None:
                gu = GroupUser.query.filter_by(tg_id=lookup_user_id).first()
            else:
                # GroupUser stores name/username inside profile_data JSON.
                # Escape SQL wildcard characters in the username before using ILIKE,
                # then confirm the exact match by parsing the JSON.
                # Note: admin may store the username with a leading '@' in any field
                # (not just 'username'/'tg_username'), so we strip '@' from each
                # string value before comparing.
                escaped = lookup_username.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                candidates = GroupUser.query.filter(
                    GroupUser.profile_data.ilike(f'%{escaped}%', escape='\\')
                ).all()
                gu = None
                for c in candidates:
                    try:
                        pd = _json.loads(c.profile_data or '{}')
                    except (ValueError, TypeError):
                        pd = {}
                    # Check all string values in profile_data; strip exactly one
                    # leading '@' so both "@zhangsan" and "zhangsan" match.
                    for v in pd.values():
                        normalized = v[1:] if isinstance(v, str) and v.startswith('@') else v
                        if isinstance(v, str) and normalized.lower() == lookup_username.lower():
                            gu = c
                            break
                    if gu is not None:
                        break
            if gu is None:
                return None
            return _group_user_to_namespace(gu)

    loop = asyncio.get_running_loop()
    member = await loop.run_in_executor(None, _find_member)

    # Fallback: try Telegram API when user not found in GroupMember/GroupUser tables
    if member is None and lookup_username:
        try:
            tg_chat = await context.bot.get_chat(f'@{lookup_username}')
        except Exception as e:
            logger.debug('Telegram get_chat fallback failed for @%s: %s', lookup_username, e)
            tg_chat = None
        if tg_chat and tg_chat.type == 'private':
            tg_user_id = tg_chat.id
            # Re-try DB lookup by user_id in case the record exists under a different username
            def _find_by_tg_id():
                from app.models import GroupMember, GroupUser
                from app import db
                with flask_app.app_context():
                    m = GroupMember.query.filter_by(user_id=tg_user_id).first()
                    if m is not None:
                        return m
                    gu = GroupUser.query.filter_by(tg_id=tg_user_id).first()
                    if gu is None:
                        return None
                    # Cache tg_username in profile_data so future lookups work
                    # without requiring a Telegram API call.
                    if tg_chat and tg_chat.username:
                        try:
                            pd = json.loads(gu.profile_data or '{}')
                            if not pd.get('tg_username'):
                                pd['tg_username'] = tg_chat.username
                                gu.profile_data = json.dumps(pd, ensure_ascii=False)
                                db.session.commit()
                        except Exception:
                            logger.warning(
                                'Failed to cache tg_username for GroupUser tg_id=%s',
                                tg_user_id, exc_info=True,
                            )
                    return _group_user_to_namespace(gu)
            member = await loop.run_in_executor(None, _find_by_tg_id)
            if member is None:
                # Synthesise a lightweight object from Telegram data so the rest of the
                # flow (identity display, report saving) works normally.
                # Required attributes: user_id, username, first_name, last_name
                from types import SimpleNamespace
                member = SimpleNamespace(
                    user_id=tg_chat.id,
                    username=tg_chat.username or '',
                    first_name=tg_chat.first_name or '',
                    last_name=tg_chat.last_name or '',
                )

    if member is None:
        hint = f'@{lookup_username}' if lookup_username else str(lookup_user_id)
        await _try_delete_message(context.bot, update.effective_chat.id, update.message.message_id)
        await _edit_or_reply(
            context, update.message, update.effective_chat.id,
            f'❌ 未找到用户 {hint}，请确认用户名或 ID 正确，然后重新发送。\n\n'
            '📝 请发送被提交人的用户名或 ID，例如：\n'
            '  • <code>@zhangsan</code>\n'
            '  • <code>https://t.me/zhangsan</code>\n'
            '  • <code>123456789</code>\n\n'
            '发送 /cancel 可取消。',
            parse_mode='HTML',
        )
        return STEP_TARGET

    target_user_id = member.user_id
    context.user_data[_TARGET_KEY] = target_user_id

    clone_id = _get_clone_id(context)
    questions = _load_questions(flask_app, clone_id=clone_id)
    context.user_data['questions'] = questions
    context.user_data['answers'] = []
    context.user_data['current_q_idx'] = 0

    # Delete the user's target input and edit the persistent card to show question 1.
    await _try_delete_message(context.bot, update.effective_chat.id, update.message.message_id)
    identity = _format_user_identity(target_user_id, member=member)
    total = len(questions)
    await _edit_or_reply(
        context, update.message, update.effective_chat.id,
        f'✅ 已找到被提交人：{identity}\n\n'
        f'📝 开始填写报告（共{total}步）\n\n'
        + _prompt_for_question(questions, 0, total),
        parse_mode='HTML',
    )
    return STEP_QUESTION


# ──────────────────────────────────────────────────────────────────────────────
# Handler factory – call this for every Application that needs report handlers
# (main bot AND each clone bot).  Each Application must receive its *own*
# handler instances; sharing a single ConversationHandler object across
# multiple Application instances causes the clone bots' conversation flow to
# break because the handler's internal state becomes shared/corrupted.
# ──────────────────────────────────────────────────────────────────────────────

def make_report_handlers():
    """Return fresh (report_conv_handler, view_reports_handler, audit_callback_handler) instances."""
    # per_message=False (default) is intentional: conversation is tracked per user+chat.
    # Suppress the PTBUserWarning that fires when CallbackQueryHandler is used in this mode.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*per_message=False.*",
            category=UserWarning,
        )
        conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(r'^/start report_\d+') & filters.ChatType.PRIVATE,
                report_start,
            ),
            MessageHandler(
                filters.Regex(r'^写报告$') & filters.ChatType.PRIVATE,
                report_start_by_text,
            ),
        ],
        states={
            STEP_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_receive_target)],
            STEP_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_step_question)],
            STEP_PHOTO: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, report_step_photo),
                # Text fallback: tell the user to send a photo (or skip)
                MessageHandler(filters.TEXT & ~filters.COMMAND, report_step_photo),
                # Inline-keyboard: skip photo or restart
                CallbackQueryHandler(report_confirm_callback,
                                     pattern=r'^report_(skip_photo|restart|confirm_submit)$'),
            ],
            STEP_CONFIRM: [
                CallbackQueryHandler(report_confirm_callback,
                                     pattern=r'^report_(skip_photo|restart|confirm_submit)$'),
            ],
        },
        fallbacks=[
            MessageHandler(filters.COMMAND, report_cancel),
        ],
        name='report_conversation',
        persistent=False,
    )
    view_handler = MessageHandler(
        filters.Regex(r'^/start view_\d+') & filters.ChatType.PRIVATE,
        view_reports,
    )
    audit_handler = CallbackQueryHandler(
        audit_callback,
        pattern=r'^audit_(approve|reject|delete_channel)_\d+$',
    )
    return conv, view_handler, audit_handler


# Module-level singletons kept for backward compatibility (used by main bot setup).
report_conv_handler, view_reports_handler, audit_callback_handler = make_report_handlers()
