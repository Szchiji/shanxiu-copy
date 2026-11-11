"""
app/bot/handlers/commands.py
-----------------------------
Bot command handlers for group moderation and query commands.

Canonical implementations used by ``run_bot`` / ``setup_clone_handlers``.
Kept behavior-identical to the former inline handlers in
``app.modules.core.routes`` (plugin gates, auto-delete replies, unmute
permanent-flag clear, etc.).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import func

from app import db
from app.bot import state as _state
from app.models import (
    BotClone,
    BotGroup,
    GroupUser,
    InvitationRecord,
    MemberLevel,
    MessageStatistics,
    PointsLog,
    UserPoints,
)
from app.utils import (
    is_user_admin_in_group,
    get_muted_permissions,
    get_unrestricted_permissions,
    log_admin_action,
    get_beijing_now,
    safe_int,
)


# ---------------------------------------------------------------------------
# Helpers (match former routes.py behavior)
# ---------------------------------------------------------------------------

async def _is_unrestrictable_member(bot, chat_id, user_id) -> bool:
    """True if user cannot be restricted (creator or administrator).

    Matches the historical ``is_user_chat_owner`` helper in routes.py, which
    treated both statuses as unrestrictable — not utils.is_user_chat_owner
    (creator-only).
    """
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ('creator', 'administrator')
    except Exception as e:
        if 'Chat not found' in str(e):
            raise
        print(f"Error checking chat owner status: {e}")
        return False


async def _auto_delete_msg(context):
    """Job callback: delete the message stored in job.data."""
    try:
        await context.job.data.delete()
    except Exception:
        pass


def _sched_del(context, msg, delay: int):
    """Schedule *msg* for deletion after *delay* seconds via the job queue."""
    try:
        context.job_queue.run_once(_auto_delete_msg, delay, data=msg)
    except Exception:
        pass


async def _ensure_plugin_enabled(update: Update, context, plugin_name: str) -> bool:
    """Return True if *plugin_name* should run for this chat (default-on).

    When the plugin is explicitly disabled for the group, return False so the
    caller can skip feature logic. Missing group / no Flask app → True
    (preserve historical behavior until settings exist).
    """
    if not _state.global_flask_app:
        return True
    chat = update.effective_chat
    if not chat:
        return True

    def _check():
        with _state.global_flask_app.app_context():
            from app.plugins import is_plugin_enabled_for_chat as _chat_plugin_on
            return _chat_plugin_on(
                chat.id,
                context.application.bot_data.get('clone_id'),
                plugin_name,
            )

    return await asyncio.get_running_loop().run_in_executor(None, _check)



# ---------------------------------------------------------------------------
# Moderation commands
# ---------------------------------------------------------------------------

async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """踢出群成员命令 /kick"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'moderation'):
        return
    
    # Check if user is admin
    is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
    if not is_admin:
        _sched_del(context, await update.message.reply_text("❌ 只有管理员才能使用此命令"), 20)
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message:
        _sched_del(context, await update.message.reply_text("❌ 请回复要踢出的用户消息"), 20)
        return
    
    target_user = update.message.reply_to_message.from_user
    try:
        await context.bot.ban_chat_member(chat.id, target_user.id)
        await context.bot.unban_chat_member(chat.id, target_user.id)
        _sched_del(context, await update.message.reply_text(f"✅ 已将 {target_user.first_name} 踢出群组"), 30)
        
        # Log admin action
        def _log():
            with _state.global_flask_app.app_context():
                group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                if group:
                    log_admin_action(
                        group.id,
                        user.id,
                        user.first_name + (f" {user.last_name}" if user.last_name else ""),
                        'kick',
                        target_user.id,
                        target_user.first_name + (f" {target_user.last_name}" if target_user.last_name else "")
                    )
        
        if _state.global_flask_app:
            await asyncio.get_running_loop().run_in_executor(None, _log)
    except Exception as e:
        _sched_del(context, await update.message.reply_text(f"❌ 操作失败: {str(e)}"), 20)


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """封禁群成员命令 /ban"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'moderation'):
        return
    
    # Check if user is admin
    is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
    if not is_admin:
        _sched_del(context, await update.message.reply_text("❌ 只有管理员才能使用此命令"), 20)
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message:
        _sched_del(context, await update.message.reply_text("❌ 请回复要封禁的用户消息"), 20)
        return
    
    target_user = update.message.reply_to_message.from_user
    try:
        await context.bot.ban_chat_member(chat.id, target_user.id)
        _sched_del(context, await update.message.reply_text(f"✅ 已将 {target_user.first_name} 封禁"), 30)
        
        # Log admin action
        def _log():
            with _state.global_flask_app.app_context():
                group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                if group:
                    log_admin_action(
                        group.id,
                        user.id,
                        user.first_name + (f" {user.last_name}" if user.last_name else ""),
                        'ban',
                        target_user.id,
                        target_user.first_name + (f" {target_user.last_name}" if target_user.last_name else "")
                    )
        
        if _state.global_flask_app:
            await asyncio.get_running_loop().run_in_executor(None, _log)
    except Exception as e:
        _sched_del(context, await update.message.reply_text(f"❌ 操作失败: {str(e)}"), 20)


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """解封群成员命令 /unban"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'moderation'):
        return
    
    # Check if user is admin
    is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
    if not is_admin:
        _sched_del(context, await update.message.reply_text("❌ 只有管理员才能使用此命令"), 20)
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message:
        _sched_del(context, await update.message.reply_text("❌ 请回复要解封的用户消息"), 20)
        return
    
    target_user = update.message.reply_to_message.from_user
    try:
        await context.bot.unban_chat_member(chat.id, target_user.id)
        _sched_del(context, await update.message.reply_text(f"✅ 已将 {target_user.first_name} 解封"), 30)
    except Exception as e:
        _sched_del(context, await update.message.reply_text(f"❌ 操作失败: {str(e)}"), 20)


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """禁言群成员命令 /mute [时间(分钟)]"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'moderation'):
        return
    
    # Check if user is admin
    is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
    if not is_admin:
        _sched_del(context, await update.message.reply_text("❌ 只有管理员才能使用此命令"), 20)
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message:
        _sched_del(context, await update.message.reply_text("❌ 请回复要禁言的用户消息"), 20)
        return
    
    target_user = update.message.reply_to_message.from_user
    
    # Parse duration (default: 60 minutes)
    duration = 60
    if context.args:
        try:
            duration = int(context.args[0])
            if duration <= 0:
                duration = 60
        except:
            duration = 60
    
    try:
        # Restrict user from sending messages
        permissions = get_muted_permissions()
        until_date = datetime.now() + timedelta(minutes=duration)
        await context.bot.restrict_chat_member(chat.id, target_user.id, permissions, until_date=until_date)
        _sched_del(context, await update.message.reply_text(f"✅ 已将 {target_user.first_name} 禁言 {duration} 分钟"), 30)
    except Exception as e:
        _sched_del(context, await update.message.reply_text(f"❌ 操作失败: {str(e)}"), 20)


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """解除禁言命令 /unmute"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'moderation'):
        return
    
    # Check if user is admin
    is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
    if not is_admin:
        _sched_del(context, await update.message.reply_text("❌ 只有管理员才能使用此命令"), 20)
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message:
        _sched_del(context, await update.message.reply_text("❌ 请回复要解除禁言的用户消息"), 20)
        return
    
    target_user = update.message.reply_to_message.from_user
    
    # Check if target user is chat owner - skip unmute for chat owners
    is_owner = await _is_unrestrictable_member(context.bot, chat.id, target_user.id)
    if is_owner:
        _sched_del(context, await update.message.reply_text(f"⏭️ 无法解除群主 {target_user.first_name} 的禁言 - 群主权限无需解除禁言"), 20)
        print(f"⏭️ [解除禁言命令] 跳过解除禁言操作 - 用户 {target_user.id} 是群主 (Chat Owner) in group {chat.id}", flush=True)
        return
    
    try:
        # Restore default permissions
        permissions = get_unrestricted_permissions()
        await context.bot.restrict_chat_member(chat.id, target_user.id, permissions)
        
        # Clear permanent mute flag in database if exists
        if _state.global_flask_app:
            def _clear_permanent_mute():
                with _state.global_flask_app.app_context():
                    try:
                        group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                        if group:
                            group_user = GroupUser.query.filter_by(
                                group_id=group.id,
                                tg_id=target_user.id
                            ).first()
                            if group_user and group_user.is_muted_permanent:
                                group_user.is_muted_permanent = False
                                group_user.mute_reason = None
                                db.session.commit()
                                print(f"✅ Cleared permanent mute flag for user {target_user.id} in group {group.id}")
                    except Exception as e:
                        print(f"Error clearing permanent mute flag: {e}")
                        db.session.rollback()
            await asyncio.get_running_loop().run_in_executor(None, _clear_permanent_mute)
        
        _sched_del(context, await update.message.reply_text(f"✅ 已解除 {target_user.first_name} 的禁言"), 30)
    except Exception as e:
        _sched_del(context, await update.message.reply_text(f"❌ 操作失败: {str(e)}"), 20)


async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """置顶消息命令 /pin"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'moderation'):
        return
    
    # Check if user is admin
    is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
    if not is_admin:
        _sched_del(context, await update.message.reply_text("❌ 只有管理员才能使用此命令"), 20)
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message:
        _sched_del(context, await update.message.reply_text("❌ 请回复要置顶的消息"), 20)
        return
    
    try:
        await context.bot.pin_chat_message(chat.id, update.message.reply_to_message.message_id)
        _sched_del(context, await update.message.reply_text("✅ 消息已置顶"), 30)
    except Exception as e:
        _sched_del(context, await update.message.reply_text(f"❌ 操作失败: {str(e)}"), 20)


async def cmd_unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消置顶消息命令 /unpin"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'moderation'):
        return
    
    # Check if user is admin
    is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
    if not is_admin:
        _sched_del(context, await update.message.reply_text("❌ 只有管理员才能使用此命令"), 20)
        return
    
    try:
        if update.message.reply_to_message:
            # Unpin specific message
            await context.bot.unpin_chat_message(chat.id, update.message.reply_to_message.message_id)
        else:
            # Unpin all messages
            await context.bot.unpin_all_chat_messages(chat.id)
        _sched_del(context, await update.message.reply_text("✅ 已取消置顶"), 30)
    except Exception as e:
        _sched_del(context, await update.message.reply_text(f"❌ 操作失败: {str(e)}"), 20)


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """警告用户命令 /warn [原因] — 落库计次并在回复中展示累计次数"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'moderation'):
        return
    
    # Check if user is admin
    is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
    if not is_admin:
        _sched_del(context, await update.message.reply_text("❌ 只有管理员才能使用此命令"), 20)
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message:
        _sched_del(context, await update.message.reply_text("❌ 请回复要警告的用户消息"), 20)
        return
    
    target_user = update.message.reply_to_message.from_user
    reason = " ".join(context.args) if context.args else "违反群规"

    warn_count = 0
    if _state.global_flask_app:
        def _persist():
            from app.services.warning_service import record_warning
            with _state.global_flask_app.app_context():
                group = BotGroup.query.filter_by(
                    chat_id=str(chat.id),
                    clone_id=context.application.bot_data.get('clone_id'),
                ).first()
                if not group:
                    return 0
                count = record_warning(
                    group.id,
                    target_user.id,
                    admin_id=user.id,
                    reason=reason,
                    source='command',
                )
                admin_name = user.first_name + (f" {user.last_name}" if user.last_name else "")
                target_name = target_user.first_name + (f" {target_user.last_name}" if target_user.last_name else "")
                log_admin_action(
                    group.id,
                    user.id,
                    admin_name,
                    'warn',
                    target_user.id,
                    target_name,
                    details=f"reason={reason}; count={count}",
                )
                return count
        try:
            warn_count = await asyncio.get_running_loop().run_in_executor(None, _persist)
        except Exception as e:
            print(f"Error persisting warning: {e}")
            warn_count = 0

    from app.services.warning_service import format_command_warn_reply
    warning_text = format_command_warn_reply(target_user.first_name, reason, warn_count or 1)
    await update.message.reply_text(warning_text)


async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询用户详细信息命令 /userinfo"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    
    # Check if user is admin
    is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
    if not is_admin:
        _sched_del(context, await update.message.reply_text("❌ 只有管理员才能使用此命令"), 20)
        return
    
    # Check if replying to a message or forwarded message
    target_user = None
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
    else:
        target_user = getattr(update.message, 'forward_from', None)
    
    if not target_user:
        _sched_del(context, await update.message.reply_text("❌ 请回复或转发用户的消息来查看详情"), 20)
        return
    
    # Get user info from database
    def _get_user_info():
        with _state.global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return None, None, None
            
            group_user = GroupUser.query.filter_by(
                group_id=group.id,
                tg_id=target_user.id
            ).first()
            
            # Get user points if available
            user_points = None
            total_earned = 0
            total_spent = 0
            try:
                user_points = UserPoints.query.filter_by(
                    group_id=group.id,
                    user_id=target_user.id
                ).first()
                
                # Calculate total earned and spent using database aggregation
                from sqlalchemy import func, case
                result = db.session.query(
                    func.sum(case((PointsLog.points_change > 0, PointsLog.points_change), else_=0)).label('earned'),
                    func.sum(case((PointsLog.points_change < 0, func.abs(PointsLog.points_change)), else_=0)).label('spent')
                ).filter(
                    PointsLog.group_id == group.id,
                    PointsLog.user_id == target_user.id
                ).first()
                
                if result:
                    total_earned = result.earned or 0
                    total_spent = result.spent or 0
            except Exception as e:
                print(f"Error getting user points: {e}")
            
            return group_user, user_points, group, total_earned, total_spent
    
    group_user, user_points, group, total_earned, total_spent = await asyncio.get_running_loop().run_in_executor(None, _get_user_info)
    
    # Build user info message
    info_lines = ["👤 用户详细信息\n"]
    info_lines.append(f"━━━━━━━━━━━━━━━━")
    info_lines.append(f"📛 用户名: {target_user.first_name or '-'}")
    if target_user.last_name:
        info_lines.append(f"   姓氏: {target_user.last_name}")
    if target_user.username:
        info_lines.append(f"🔗 Username: @{target_user.username}")
    info_lines.append(f"🆔 用户ID: <code>{target_user.id}</code>")
    info_lines.append(f"🤖 机器人: {'是' if target_user.is_bot else '否'}")
    
    if group_user:
        info_lines.append(f"\n📊 群组信息")
        info_lines.append(f"━━━━━━━━━━━━━━━━")
        
        # Parse profile data
        try:
            profile_data = json.loads(group_user.profile_data or '{}')
            if profile_data:
                for key, value in profile_data.items():
                    info_lines.append(f"   {key}: {value}")
        except:
            pass
        
        if group_user.expiration_date:
            info_lines.append(f"⏰ 到期时间: {group_user.expiration_date.strftime('%Y-%m-%d %H:%M:%S')}")
        
        info_lines.append(f"🚫 封禁状态: {'已封禁' if group_user.is_banned else '正常'}")
        
        if group_user.checkin_time:
            info_lines.append(f"✅ 最后签到: {group_user.checkin_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        info_lines.append(f"🟢 在线状态: {'在线' if group_user.online else '离线'}")
    
    if user_points:
        info_lines.append(f"\n💰 积分信息")
        info_lines.append(f"━━━━━━━━━━━━━━━━")
        info_lines.append(f"💎 当前积分: {user_points.points_balance}")
        if total_earned > 0 or total_spent > 0:
            info_lines.append(f"📈 总获得: {total_earned}")
            info_lines.append(f"📉 总消耗: {total_spent}")
        
        # 🆕 Show member level badge
        try:
            def _get_member_level():
                with _state.global_flask_app.app_context():
                    levels = MemberLevel.query.filter_by(
                        group_id=group.id
                    ).order_by(MemberLevel.required_points.desc()).all()
                    
                    current_level = None
                    for level in levels:
                        if user_points.points_balance >= level.required_points:
                            current_level = level
                            break
                    return current_level
            
            member_level = await asyncio.get_running_loop().run_in_executor(None, _get_member_level)
            if member_level:
                badge = member_level.badge_emoji or "🎖️"
                info_lines.append(f"{badge} 会员等级: {member_level.level_name}")
        except Exception as e:
            print(f"Error getting member level: {e}")
    
    # Get member info from Telegram
    try:
        member = await context.bot.get_chat_member(chat.id, target_user.id)
        info_lines.append(f"\n👥 群组权限")
        info_lines.append(f"━━━━━━━━━━━━━━━━")
        info_lines.append(f"📌 身份: {member.status}")
        if member.status == 'creator':
            info_lines.append(f"   (群主)")
        elif member.status == 'administrator':
            info_lines.append(f"   (管理员)")
        elif member.status == 'member':
            info_lines.append(f"   (普通成员)")
        elif member.status == 'restricted':
            info_lines.append(f"   (受限用户)")
        elif member.status == 'left':
            info_lines.append(f"   (已退出)")
        elif member.status == 'kicked':
            info_lines.append(f"   (已被移除)")
    except Exception as e:
        print(f"Error getting member info: {e}")
    
    info_text = "\n".join(info_lines)
    _sched_del(context, await update.message.reply_html(info_text), 90)


# ---------------------------------------------------------------------------
# Ranking commands
# ---------------------------------------------------------------------------

async def cmd_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """积分排行榜命令 /rank [数量]"""
    chat = update.effective_chat
    
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'points'):
        return
    
    # Parse limit (default 10, max 50)
    limit = 10
    if context.args:
        try:
            limit = int(context.args[0])
            if limit < 1 or limit > 50:
                limit = 10
        except ValueError:
            pass
    
    if not _state.global_flask_app:
        return
    
    def _get_rankings():
        with _state.global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return None, "群组不存在"
            
            # Get top users by points
            rankings = db.session.query(
                UserPoints,
                GroupUser
            ).join(
                GroupUser,
                db.and_(
                    UserPoints.group_id == GroupUser.group_id,
                    UserPoints.user_id == GroupUser.tg_id
                )
            ).filter(
                UserPoints.group_id == group.id,
                UserPoints.points_balance > 0
            ).order_by(
                UserPoints.points_balance.desc()
            ).limit(limit).all()
            
            results = []
            for user_points, group_user in rankings:
                # Get user's level badge
                level_badge = ""
                if user_points.current_level:
                    level = MemberLevel.query.get(user_points.current_level_id)
                    if level and level.badge_emoji:
                        level_badge = level.badge_emoji
                
                # Get user profile data
                try:
                    profile = json.loads(group_user.profile_data) if group_user.profile_data else {}
                    name = profile.get('name', f'User_{user_points.user_id}')
                except Exception:
                    name = f'User_{user_points.user_id}'
                
                results.append({
                    'name': name,
                    'points': user_points.points_balance,
                    'badge': level_badge,
                    'user_id': user_points.user_id
                })
            
            return results, None
    
    results, error = await asyncio.get_running_loop().run_in_executor(None, _get_rankings)
    
    if error:
        _sched_del(context, await update.message.reply_text(f"❌ {error}"), 20)
        return
    
    if not results:
        _sched_del(context, await update.message.reply_text("📊 暂无积分排行数据"), 20)
        return
    
    # Build ranking message
    msg = f"🏆 <b>积分排行榜 TOP {len(results)}</b>\n\n"
    
    medals = ["🥇", "🥈", "🥉"]
    for idx, user_data in enumerate(results):
        rank_icon = medals[idx] if idx < 3 else f"{idx + 1}."
        badge = user_data['badge'] + " " if user_data['badge'] else ""
        msg += f"{rank_icon} {badge}{user_data['name']} - {user_data['points']} 积分\n"
    
    _sched_del(context, await update.message.reply_text(msg, parse_mode='HTML'), 90)


async def cmd_invite_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """邀请排行榜命令 /invite_rank [数量]"""
    chat = update.effective_chat
    
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'invitation'):
        return
    
    # Parse limit (default 10, max 50)
    limit = 10
    if context.args:
        try:
            limit = int(context.args[0])
            if limit < 1 or limit > 50:
                limit = 10
        except ValueError:
            pass
    
    if not _state.global_flask_app:
        return
    
    def _get_invite_rankings():
        with _state.global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return None, "群组不存在"
            
            # Get top inviters
            rankings = db.session.query(
                InvitationRecord.inviter_id,
                func.count(InvitationRecord.id).label('invite_count'),
                func.sum(InvitationRecord.points_awarded).label('total_points')
            ).filter(
                InvitationRecord.group_id == group.id
            ).group_by(
                InvitationRecord.inviter_id
            ).order_by(
                func.count(InvitationRecord.id).desc()
            ).limit(limit).all()
            
            results = []
            for inviter_id, invite_count, total_points in rankings:
                # Try to get user name from GroupUser
                group_user = GroupUser.query.filter_by(
                    group_id=group.id,
                    tg_id=inviter_id
                ).first()
                
                name = f'User_{inviter_id}'
                if group_user:
                    try:
                        profile = json.loads(group_user.profile_data) if group_user.profile_data else {}
                        name = profile.get('name', f'User_{inviter_id}')
                    except Exception:
                        pass
                
                results.append({
                    'name': name,
                    'invite_count': invite_count,
                    'total_points': total_points or 0,
                    'user_id': inviter_id
                })
            
            return results, None
    
    results, error = await asyncio.get_running_loop().run_in_executor(None, _get_invite_rankings)
    
    if error:
        _sched_del(context, await update.message.reply_text(f"❌ {error}"), 20)
        return
    
    if not results:
        _sched_del(context, await update.message.reply_text("📊 暂无邀请排行数据"), 20)
        return
    
    # Build ranking message
    msg = f"🏆 <b>邀请排行榜 TOP {len(results)}</b>\n\n"
    
    medals = ["🥇", "🥈", "🥉"]
    for idx, user_data in enumerate(results):
        rank_icon = medals[idx] if idx < 3 else f"{idx + 1}."
        msg += f"{rank_icon} {user_data['name']} - 邀请 {user_data['invite_count']} 人 ({user_data['total_points']} 积分)\n"
    
    _sched_del(context, await update.message.reply_text(msg, parse_mode='HTML'), 90)


async def cmd_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """活跃排行榜命令 /active [period]"""
    chat = update.effective_chat
    
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    
    # Parse period (week/month, default week)
    period = 'week'
    if context.args and context.args[0].lower() in ['week', 'month']:
        period = context.args[0].lower()
    
    if not _state.global_flask_app:
        return
    
    def _get_active_rankings():
        with _state.global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return None, "群组不存在"
            
            # Calculate date range
            now = get_beijing_now()
            if period == 'week':
                start_date = (now - timedelta(days=7)).date()
                period_label = "本周"
            else:
                start_date = (now - timedelta(days=30)).date()
                period_label = "本月"
            
            # Get message statistics
            rankings = db.session.query(
                MessageStatistics.user_id,
                db.func.sum(MessageStatistics.message_count).label('total_messages'),
                GroupUser
            ).join(
                GroupUser,
                db.and_(
                    MessageStatistics.group_id == GroupUser.group_id,
                    MessageStatistics.user_id == GroupUser.tg_id
                )
            ).filter(
                MessageStatistics.group_id == group.id,
                MessageStatistics.date >= start_date
            ).group_by(
                MessageStatistics.user_id,
                GroupUser.id
            ).order_by(
                db.text('total_messages DESC')
            ).limit(20).all()
            
            results = []
            for user_id, total_messages, group_user in rankings:
                # Get user profile data
                try:
                    profile = json.loads(group_user.profile_data) if group_user.profile_data else {}
                    name = profile.get('name', f'User_{user_id}')
                except Exception:
                    name = f'User_{user_id}'
                
                results.append({
                    'name': name,
                    'messages': int(total_messages),
                    'user_id': user_id
                })
            
            return {'results': results, 'period_label': period_label}, None
    
    data, error = await asyncio.get_running_loop().run_in_executor(None, _get_active_rankings)
    
    if error:
        _sched_del(context, await update.message.reply_text(f"❌ {error}"), 20)
        return
    
    if not data['results']:
        _sched_del(context, await update.message.reply_text(f"📊 暂无{data['period_label']}活跃数据"), 20)
        return
    
    # Build ranking message
    msg = f"📈 <b>{data['period_label']}活跃排行榜 TOP {len(data['results'])}</b>\n\n"
    
    medals = ["🥇", "🥈", "🥉"]
    for idx, user_data in enumerate(data['results']):
        rank_icon = medals[idx] if idx < 3 else f"{idx + 1}."
        msg += f"{rank_icon} {user_data['name']} - {user_data['messages']} 条消息\n"
    
    _sched_del(context, await update.message.reply_text(msg, parse_mode='HTML'), 90)


# ---------------------------------------------------------------------------
# Clone management command (admin only, private chat)
# ---------------------------------------------------------------------------

async def cmd_clones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看和管理克隆机器人 /clones - 仅管理员可用"""
    user_id = update.effective_user.id
    chat = update.effective_chat
    admin_id = safe_int(os.getenv('ADMIN_ID', 0))
    
    # Only work in private chat
    if chat.type != 'private':
        await update.message.reply_text("❌ 此命令只能在私聊中使用")
        return
    
    # Check if user is admin
    if user_id != admin_id:
        await update.message.reply_text("❌ 只有管理员才能使用此命令")
        return
    
    if not _state.global_flask_app:
        await update.message.reply_text("❌ 系统未就绪")
        return
    
    def _get_clones():
        with _state.global_flask_app.app_context():
            clones = BotClone.query.order_by(BotClone.created_at.desc()).all()
            return [{
                'id': c.id,
                'clone_name': c.clone_name,
                'owner_user_id': c.owner_user_id,
                'is_active': c.is_active,
                'expiration_date': c.expiration_date.strftime('%Y-%m-%d %H:%M:%S') if c.expiration_date else '无期限',
                'description': c.description or '无描述'
            } for c in clones]
    
    clones = await asyncio.get_running_loop().run_in_executor(None, _get_clones)
    
    if not clones:
        await update.message.reply_text("📝 当前没有克隆机器人")
        return
    
    # Format the list
    message = "🤖 <b>克隆机器人列表</b>\n\n"
    for clone in clones:
        status = "✅ 活跃" if clone['is_active'] else "❌ 停用"
        message += f"<b>ID:</b> {clone['id']}\n"
        message += f"<b>名称:</b> {clone['clone_name']}\n"
        message += f"<b>状态:</b> {status}\n"
        message += f"<b>拥有者ID:</b> {clone['owner_user_id'] or '无'}\n"
        message += f"<b>有效期:</b> {clone['expiration_date']}\n"
        message += f"<b>描述:</b> {clone['description']}\n"
        message += "─────────────────\n"
    
    message += f"\n💡 <b>提示:</b> 在管理后台可以管理克隆机器人\n"
    message += f"访问: {os.getenv('RAILWAY_PUBLIC_DOMAIN', 'localhost:5000')}/core/bot_clones"
    
    await update.message.reply_html(message)
