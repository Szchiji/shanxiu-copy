from flask import Blueprint, render_template, request, redirect, session, jsonify, abort, Response
from app import db, limiter
from app import get_bot_instance_role, is_clone_instance, is_main_instance
from app.models import (BotGroup, GroupUser, DEFAULT_FIELDS, DEFAULT_SYSTEM, AuthSession, AutoReply, ScheduledMessage, StartMessage,
                        GroupEntryExitSettings, SpamProtection, TimedGroupControl, InvitationActivity, ForcedChannelSubscription,
                        PointsRule, PointsAutoReply, PointsAuction, PointsLog, UserPoints, GroupLottery, MemberLevel,
                        UserNameChange, GroupBottomButton, SyncGroupMessages, SyncMessageLog, OtherSettings, BotClone,
                        ChannelForwardRule, ChannelMessageTemplate, ChannelCoupon, ChannelPostButtonConfig, ChannelMessageStats, LotteryMessageCount,
                        InactiveUserSettings, KeywordFilter, MessageStatistics, GroupVote, VoteRecord, QuizGame, QuizSession, 
                        QuizAnswer, RedPacket, RedPacketClaim, AdminActionLog, GroupWarning, GroupMember, InvitationRecord, PendingReferral,
                        PointsExchangeItem, PointsExchangeRecord)
from app.services import sanitize_html_for_telegram
from app.log_redaction import redact_secrets
from app.services.points_service import clamp_award_to_daily_cap
from app.utils import (
    encrypt_token,
    decrypt_token,
    normalize_like_emoji,
    is_valid_like_emoji,
    TELEGRAM_REACTION_EMOJIS,
    DEFAULT_LIKE_EMOJI,
    parse_member_tags,
    serialize_member_tags,
    format_member_tags,
    normalize_telegram_member_tag,
    resolve_telegram_member_tag,
)
from app import bot_clone_manager
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions, ChatMember, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, LinkPreviewOptions, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, filters
from sqlalchemy.orm import joinedload
from sqlalchemy import or_, cast, String, func, case
from sqlalchemy.exc import ProgrammingError, OperationalError
import os, jwt, time, json, asyncio, re, requests, math, secrets, string, hmac, csv, io, logging, traceback, random
from datetime import datetime, timedelta
import pytz
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from io import BytesIO
from functools import wraps
from types import SimpleNamespace
from urllib.parse import urlencode

core_bp = Blueprint('core', __name__, url_prefix='/core', template_folder='templates')

# --- 全局变量 ---
global_ptb_app = None
global_bot_loop = None
global_flask_app = None  # 🆕 新增：持有 Flask App 实例
_background_tasks: set = set()  # strong-ref holder for fire-and-forget asyncio tasks

# Constants
EXPIRATION_CHECK_INTERVAL = 3600  # Check expired users every hour (in seconds)
JWT_TOKEN_EXPIRY_DAYS = 7  # JWT token validity for group access (in days)
MAX_CONCURRENT_BANS = 5  # Maximum concurrent ban operations to avoid rate limits
EXPIRED_USERS_BATCH_SIZE = 100  # Process expired users in batches to avoid memory issues
AUTH_SESSION_EXPIRY_MINUTES = 5  # Authentication session expiry time
SCHEDULED_MESSAGE_CHECK_INTERVAL = 60  # Check scheduled messages every minute (in seconds)
MAX_LOTTERY_PARTICIPANTS = 100  # Maximum participants to consider in lottery fallback
MAX_LOTTERY_MESSAGE_COUNT_RECORDS = 10000  # Maximum message count records to load for lottery
MAX_AUCTION_WINNERS = 100  # Maximum winners in auction/lottery ranking
CLONE_START_TIMEOUT = 10  # Timeout for starting clone bots (in seconds)
CLONE_STOP_TIMEOUT = 10  # Timeout for stopping clone bots (in seconds)
CLONE_RESTART_TIMEOUT = 15  # Timeout for restarting clone bots (in seconds)
SUBSCRIPTION_CHECK_BATCH_SIZE = 10  # Users checked per run in forced-channel subscription task

# Simple in-memory cache for Telegram photos: key -> (timestamp, bytes, content_type)
_tg_photo_cache: dict = {}
TG_PHOTO_CACHE_TTL = 300  # seconds (5 minutes)

# Mute reasons (internationalization support)
MUTE_REASON_INACTIVE = '不活跃用户'  # Inactive user
MUTE_REASON_SPAM = '垃圾信息'  # Spam

def get_clone_webhook_url(clone_id: int, stored_url=None):
    """
    获取克隆机器人的 Webhook URL。
    如果数据库中已存储 webhook_url，则直接使用；
    否则在 RAILWAY_PUBLIC_DOMAIN 存在时自动生成。
    """
    if stored_url:
        return stored_url
    domain = os.getenv('RAILWAY_PUBLIC_DOMAIN', '').strip()
    if domain:
        return f"https://{domain}/core/clone_webhook/{clone_id}"
    return None

# Beijing timezone
BEIJING_TZ = pytz.timezone('Asia/Shanghai')

# --- 实例角色管理装饰器 ---
def require_main_instance(f):
    """
    装饰器：要求主实例才能访问
    克隆实例访问将返回403错误
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_clone_instance():
            if request.is_json or request.path.startswith('/core/api/'):
                return jsonify({
                    'success': False,
                    'error': '克隆实例无权限访问此功能。Clone instances are not allowed to access this feature.'
                }), 403
            else:
                abort(403, description='克隆实例无权限访问此页面。此功能仅限主实例使用。')
        return f(*args, **kwargs)
    return decorated_function

def generate_verification_code():
    """Generate a random 6-digit verification code"""
    return ''.join(secrets.choice(string.digits) for _ in range(6))

def generate_session_token():
    """Generate a secure random session token"""
    return secrets.token_urlsafe(32)

# Reason strings used in PointsLog – kept as constants to avoid silent mismatches
_REASON_MESSAGE = "发送消息"
_REASON_CHECKIN = "每日签到"
_REASON_SHARE = "转发消息"
_REASON_CHANNEL_SUB = "频道订阅奖励"

def get_beijing_now():
    """Get current time in Beijing timezone as naive datetime (for database storage)"""
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)

def get_beijing_today():
    """Get today's date at midnight in Beijing timezone as naive datetime"""
    now = datetime.now(BEIJING_TZ)
    return now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

def compute_aligned_next_send_at(start_time, repeat_interval_minutes, reference_time):
    """Compute the next send time aligned to the original start_time cadence.

    Advances from *start_time* by multiples of *repeat_interval_minutes* until
    the result is strictly after *reference_time*.  This keeps the schedule
    locked to the user-configured cadence (e.g. every-3-hours starting at
    21:00 → 00:00, 03:00, 06:00 …) regardless of when the message was
    actually delivered.

    Falls back to ``reference_time + interval`` when *start_time* is None or
    *repeat_interval_minutes* is <= 0.
    """
    if not repeat_interval_minutes or repeat_interval_minutes <= 0:
        return None
    interval_td = timedelta(minutes=repeat_interval_minutes)
    if start_time is not None:
        next_at = start_time
        while next_at <= reference_time:
            next_at += interval_td
        return next_at
    # No start_time available – best-effort: offset from reference_time
    return reference_time + interval_td


# 单次定时消息连续发送失败达到该次数后自动停用，避免每分钟无限重试
SCHEDULED_MSG_MAX_FAILURES = 5


def advance_next_send_at(prev_next, repeat_interval_minutes, reference_time):
    """Advance *prev_next* by whole intervals only while it is <= *reference_time*.

    If *prev_next* is already in the future it is returned unchanged, so a
    send that happened before the listed "下次发送" slot never skips that slot.
    """
    if prev_next is None or not repeat_interval_minutes or repeat_interval_minutes <= 0:
        return None
    interval_td = timedelta(minutes=repeat_interval_minutes)
    next_at = prev_next
    while next_at <= reference_time:
        next_at += interval_td
    return next_at


def scheduled_should_send(last_sent_at, next_send_at, repeat_interval, now):
    """Decide whether a scheduled message is due at *now* (Beijing naive time).

    * ``next_send_at`` in the future always means "wait" – including recurring
      messages that were never sent but whose start_time is already past
      (they wait for the aligned slot instead of firing immediately).
    * Never sent and no pending slot → send now.
    * Recurring → due once ``now >= next_send_at`` (legacy rows without
      next_send_at fall back to elapsed time since last_sent_at).
    """
    if next_send_at is not None and now < next_send_at:
        return False
    if last_sent_at is None:
        return True
    if repeat_interval and repeat_interval > 0:
        if next_send_at is not None:
            return True
        elapsed_minutes = (now - last_sent_at).total_seconds() / 60
        return elapsed_minutes >= repeat_interval
    return False


_SCHED_HTML_TAG_RE = re.compile(r'<[^>]+>')


def scheduled_message_has_payload(media_type, media_urls, content):
    """True when the message has visible text or (for media types) at least one media URL."""
    text = _SCHED_HTML_TAG_RE.sub('', content or '')
    text = text.replace('&nbsp;', ' ').replace('\u200b', '').strip()
    if text:
        return True
    return media_type in ('image', 'video', 'media') and bool(media_urls)


_MEDIA_SPLIT_WS_RE = re.compile(r'\s+')
_MEDIA_SPLIT_SEP_RE = re.compile(r'[,，;；](?=\s*(?:https?://|t\.me/|tg://))')


def split_media_url_entry(value):
    """Split one form entry that may contain several pasted URLs into a list."""
    parts = []
    for chunk in _MEDIA_SPLIT_WS_RE.split(str(value or '').strip()):
        for piece in _MEDIA_SPLIT_SEP_RE.split(chunk):
            piece = piece.strip().strip(',，;；')
            if piece:
                parts.append(piece)
    return parts


def apply_scheduled_media_fields(item, d):
    """Copy media_type / media_url / media_urls from request JSON *d* onto *item*.

    Multiple URLs pasted into a single input (newline / space / comma separated)
    are split into separate entries so they are sent as one album.
    """
    item.media_type = d.get('media_type', 'text')
    item.media_url = (d.get('media_url') or '').strip() or None
    media_urls_raw = d.get('media_urls', [])
    if not isinstance(media_urls_raw, list):
        return
    if item.media_type == 'media':
        # 混合媒体类型：存储为对象数组 [{"url": "...", "type": "image|video"}, ...]
        media_urls_clean = []
        for u in media_urls_raw:
            if isinstance(u, dict):
                mtype = u.get('type', 'image')
                for url in split_media_url_entry(u.get('url')):
                    media_urls_clean.append({'url': url, 'type': mtype})
            elif u and str(u).strip():
                for url in split_media_url_entry(u):
                    media_urls_clean.append({'url': url, 'type': 'image'})
        item.media_urls = json.dumps(media_urls_clean)
        if media_urls_clean:
            item.media_url = media_urls_clean[0]['url']
    else:
        # 单一类型：存储为字符串数组 ["url1", "url2", ...]
        media_urls_clean = []
        for u in media_urls_raw:
            if u and isinstance(u, str):
                media_urls_clean.extend(split_media_url_entry(u))
        item.media_urls = json.dumps(media_urls_clean)
        if media_urls_clean:
            item.media_url = media_urls_clean[0]


def prepare_scheduled_message_enable(item, now):
    """Reset scheduling state when a scheduled message is (re-)enabled.

    * Recurring: next slot aligned to start_time; a never-sent message now
      waits for that slot (the scheduler honours next_send_at) instead of
      firing immediately.
    * One-shot: clear last_sent_at so it can fire again – at start_time if
      that is still in the future, otherwise on the next scheduler tick.
    """
    item.fail_count = 0
    if item.repeat_interval and item.repeat_interval > 0:
        item.next_send_at = compute_aligned_next_send_at(
            item.start_time, item.repeat_interval, now
        )
    else:
        item.next_send_at = item.start_time if (item.start_time and item.start_time > now) else None
        item.last_sent_at = None

def parse_telegram_message_link(url):
    """
    Parse a t.me message link and return (from_chat_id, message_id) or (None, None).
    Supports:
      https://t.me/username/message_id          → ('@username', message_id)
      https://t.me/c/channel_id/message_id      → (-100channel_id, message_id)
      t.me/username/message_id                  → same as above (no protocol)
      t.me/c/channel_id/message_id              → same as above (no protocol)
    """
    if not url:
        return None, None
    m = re.match(r'(?:https?://)?t\.me/c/(\d+)/(\d+)', url)
    if m:
        return int(f"-100{m.group(1)}"), int(m.group(2))
    m = re.match(r'(?:https?://)?t\.me/([A-Za-z0-9_]+)/(\d+)', url)
    if m:
        return f"@{m.group(1)}", int(m.group(2))
    return None, None

MAX_MEDIA_GROUP_SIZE = 10  # Telegram sendMediaGroup 每组 2–10 项

# ---------------------------------------------------------------------------
# t.me 消息链接 → 可放进相册的媒体
#
# copyMessages 只有在原消息本来就是同一个相册时才会保持分组；频道里分别发布的
# 几条单图/单视频帖子（例如 t.me/xxx/1417、1418、1419）复制过来仍是一条一条的。
# 想要合成一个相册，必须拿到每条帖子的媒体本身再用 sendMediaGroup 重新组装：
#   1. 缓存：同一机器人之前解析/上传过的 file_id 直接复用；
#   2. MEDIA_STAGING_CHAT_ID（可选）：把原帖转发到这个中转会话，读出 file_id 后删除；
#   3. 公开频道（@username）：读取 t.me 公开预览页拿到图片/视频地址并下载上传；
#   4. 以上都不行：转发到目标会话（静音）读出 file_id 后立即删除；
#   5. 仍然失败：退回 copyMessages 逐条复制（旧行为）。
# ---------------------------------------------------------------------------
_TME_MEDIA_CACHE = {}
_TME_MEDIA_CACHE_MAX = 2000
_TME_SCRAPE_MAX_BYTES = 20 * 1024 * 1024
_TME_SCRAPE_TIMEOUT = 15


def _tme_bot_key(bot):
    try:
        bid = bot.id
        if isinstance(bid, int):
            return bid
    except Exception:
        pass
    return id(bot)


def _tme_cache_key(bot, from_chat, msg_id):
    return (_tme_bot_key(bot), str(from_chat).lower(), int(msg_id))


def _tme_cache_put(key, info):
    if not info or not isinstance(info.get('media'), str):
        return
    if len(_TME_MEDIA_CACHE) >= _TME_MEDIA_CACHE_MAX:
        try:
            _TME_MEDIA_CACHE.pop(next(iter(_TME_MEDIA_CACHE)))
        except Exception:
            _TME_MEDIA_CACHE.clear()
    _TME_MEDIA_CACHE[key] = dict(info)


def _media_info_from_message(msg, with_caption=False):
    """从 Telegram Message 中读出可放进相册的媒体（仅图片/视频）。"""
    if msg is None:
        return None
    info = None
    photo = getattr(msg, 'photo', None)
    if isinstance(photo, (list, tuple)) and photo:
        fid = getattr(photo[-1], 'file_id', None)
        if isinstance(fid, str) and fid:
            info = {'kind': 'image', 'media': fid}
    if info is None:
        video = getattr(msg, 'video', None)
        fid = getattr(video, 'file_id', None) if video is not None else None
        if isinstance(fid, str) and fid:
            info = {'kind': 'video', 'media': fid}
    if info and with_caption:
        cap = getattr(msg, 'caption', None)
        if isinstance(cap, str) and cap:
            info['caption'] = cap
            ents = getattr(msg, 'caption_entities', None)
            if isinstance(ents, (list, tuple)) and ents:
                info['caption_entities'] = tuple(ents)
    return info


def _scrape_public_tme_media(username, msg_id):
    """读取公开频道帖子的预览页，下载其中的单张图片/单个视频。失败返回 None。"""
    import html as _html
    url = f"https://t.me/{username}/{int(msg_id)}?embed=1&mode=tme&single=1"
    try:
        r = requests.get(url, timeout=_TME_SCRAPE_TIMEOUT, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code != 200 or 'tgme_widget_message' not in r.text:
            return None
        page = r.text
    except Exception:
        return None
    kind = media_url = None
    m = re.search(r'<video[^>]+src="(https://[^"]+)"', page)
    if m and 'tgme_widget_message_video' in page:
        kind, media_url = 'video', _html.unescape(m.group(1))
    else:
        photos = re.findall(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('(https://[^']+)'\)", page)
        if len(photos) == 1:
            kind, media_url = 'image', _html.unescape(photos[0])
    if not media_url:
        return None
    try:
        with requests.get(media_url, timeout=_TME_SCRAPE_TIMEOUT, stream=True) as resp:
            if resp.status_code != 200:
                return None
            buf = bytearray()
            for chunk in resp.iter_content(64 * 1024):
                buf.extend(chunk)
                if len(buf) > _TME_SCRAPE_MAX_BYTES:
                    return None
        if not buf:
            return None
    except Exception:
        return None
    ext = 'mp4' if kind == 'video' else 'jpg'
    return {'kind': kind, 'media': bytes(buf), 'filename': f"{username}_{int(msg_id)}.{ext}"}


async def _forward_probe_media(bot, probe_chat_id, from_chat, msg_id, extra_kwargs=None):
    """转发原帖到 probe 会话以读取 file_id（含原说明文字），然后立即删除转发的消息。"""
    fwd = None
    try:
        fwd = await bot.forward_message(
            chat_id=probe_chat_id, from_chat_id=from_chat, message_id=msg_id,
            disable_notification=True, **(extra_kwargs or {})
        )
        return _media_info_from_message(fwd, with_caption=True)
    except Exception as e:
        print(f"⚠️ [album] 读取 t.me 媒体失败 from={from_chat} id={msg_id} via={probe_chat_id}: {redact_secrets(str(e))}", flush=True)
        return None
    finally:
        mid = getattr(fwd, 'message_id', None) if fwd is not None else None
        if isinstance(mid, int):
            try:
                await bot.delete_message(chat_id=probe_chat_id, message_id=mid)
            except Exception:
                pass


async def resolve_tme_media(bot, target_chat_id, from_chat, msg_id, extra_kwargs=None):
    """把一个 t.me 消息链接解析为 {'kind','media',...}；无法放进相册时返回 None。

    优先顺序：缓存 → MEDIA_STAGING_CHAT_ID 转发探测 → 目标会话转发探测
    → 公开频道预览页下载。转发探测直接拿到 file_id，比下载再上传更稳，
    也更适合随后用 sendMediaGroup 合成相册。
    """
    key = _tme_cache_key(bot, from_chat, msg_id)
    cached = _TME_MEDIA_CACHE.get(key)
    if cached:
        return dict(cached)

    staging = (os.environ.get('MEDIA_STAGING_CHAT_ID') or '').strip()
    if staging:
        info = await _forward_probe_media(bot, staging, from_chat, msg_id)
        if info:
            _tme_cache_put(key, info)
            return info

    # Prefer forwarding into the destination chat (public channels work without
    # membership) so we get a reusable file_id without downloading the file.
    info = await _forward_probe_media(bot, target_chat_id, from_chat, msg_id, extra_kwargs)
    if info:
        _tme_cache_put(key, info)
        return info

    if isinstance(from_chat, str) and from_chat.startswith('@'):
        try:
            info = await asyncio.to_thread(_scrape_public_tme_media, from_chat[1:], msg_id)
        except Exception:
            info = None
        if info:
            # bytes payload — caller will upload; do not cache until we have file_id
            return info
    return None


def _build_input_media(kind, media, caption=None, parse_mode=None, caption_entities=None, filename=None):
    from telegram import InputFile
    if isinstance(media, (bytes, bytearray)):
        media = InputFile(bytes(media), filename=filename or ('video.mp4' if kind == 'video' else 'photo.jpg'))
    kw = {'media': media, 'caption': caption}
    if caption_entities:
        kw['caption_entities'] = list(caption_entities)
    else:
        kw['parse_mode'] = parse_mode
    if kind == 'video':
        return InputMediaVideo(**kw)
    return InputMediaPhoto(**kw)


async def _send_single_media(bot, chat_id, kind, media, caption=None, parse_mode=None,
                             caption_entities=None, filename=None, extra_kwargs=None):
    from telegram import InputFile
    if isinstance(media, (bytes, bytearray)):
        media = InputFile(bytes(media), filename=filename or ('video.mp4' if kind == 'video' else 'photo.jpg'))
    kw = dict(chat_id=chat_id, caption=caption, **(extra_kwargs or {}))
    if caption_entities:
        kw['caption_entities'] = list(caption_entities)
    else:
        kw['parse_mode'] = parse_mode
    if kind == 'video':
        return await bot.send_video(video=media, **kw)
    return await bot.send_photo(photo=media, **kw)


async def send_multi_media(bot, chat_id, media_type, media_urls, content=None, reply_markup=None, message_thread_id=None, media_url_types=None, collected_ids=None):
    """
    发送多个多媒体消息。当只有一个URL时使用单独发送，多个URL时使用 send_media_group。
    支持混合类型（图片+视频）通过 media_url_types 参数指定每个URL的类型。
    返回发送的最后一条消息对象（用于置顶、删除等）。
    
    Args:
        media_url_types: 可选，每个URL对应的类型列表 ['image', 'video', ...]。
                         当 media_type='media' 时使用此参数确定每个URL的类型。
        collected_ids: 可选，传入一个列表，函数会将本次发送的所有消息ID追加到其中（用于 delete_previous 删除整组消息）。
    """
    if not media_urls:
        return None

    extra_kwargs = {}
    if message_thread_id:
        extra_kwargs['message_thread_id'] = message_thread_id

    def _get_url_type(index):
        """获取指定索引的URL类型"""
        if media_url_types and index < len(media_url_types):
            return media_url_types[index]
        if media_type in ('image', 'video'):
            return media_type
        return 'image'  # 默认为图片

    if len(media_urls) == 1:
        # 单个媒体，使用原始方式发送（支持 reply_markup）
        url = media_urls[0]
        url_type = _get_url_type(0)
        tg_from_chat, tg_msg_id = parse_telegram_message_link(url)
        if url_type in ('image', 'video') and tg_from_chat and tg_msg_id:
            copy_kwargs = dict(chat_id=chat_id, from_chat_id=tg_from_chat, message_id=tg_msg_id, reply_markup=reply_markup, **extra_kwargs)
            if content:
                copy_kwargs['caption'] = content
                copy_kwargs['parse_mode'] = 'HTML'
            try:
                msg = await bot.copy_message(**copy_kwargs)
                if collected_ids is not None and msg:
                    collected_ids.append(msg.message_id)
                return msg
            except Exception:
                # forward_message 不支持 reply_markup，跳过直接尝试 send_photo/send_video
                pass
            # 尝试直接发送媒体（带按钮）
            try:
                if url_type == 'image':
                    msg = await bot.send_photo(chat_id=chat_id, photo=url, caption=content if content else None, parse_mode='HTML' if content else None, reply_markup=reply_markup, **extra_kwargs)
                elif url_type == 'video':
                    msg = await bot.send_video(chat_id=chat_id, video=url, caption=content if content else None, parse_mode='HTML' if content else None, reply_markup=reply_markup, **extra_kwargs)
                else:
                    msg = None
                if collected_ids is not None and msg:
                    collected_ids.append(msg.message_id)
                return msg
            except Exception:
                fallback_text = f"{content}\n{url}" if content else url
                msg = await bot.send_message(chat_id=chat_id, text=fallback_text, parse_mode='HTML', reply_markup=reply_markup, **extra_kwargs)
                if collected_ids is not None and msg:
                    collected_ids.append(msg.message_id)
                return msg
        elif url_type == 'image':
            msg = await bot.send_photo(chat_id=chat_id, photo=url, caption=content if content else None, parse_mode='HTML' if content else None, reply_markup=reply_markup, **extra_kwargs)
            if collected_ids is not None and msg:
                collected_ids.append(msg.message_id)
            return msg
        elif url_type == 'video':
            msg = await bot.send_video(chat_id=chat_id, video=url, caption=content if content else None, parse_mode='HTML' if content else None, reply_markup=reply_markup, **extra_kwargs)
            if collected_ids is not None and msg:
                collected_ids.append(msg.message_id)
            return msg
        return None

    # ---------------- 多个媒体：以相册（媒体组）发送 ----------------
    # 规则：
    #  * 普通 URL 与 t.me 链接按原顺序合并成相册，用 send_media_group 发送，
    #    每组最多 10 个，超出自动拆成多组；只剩 1 个的分组改用 send_photo/send_video。
    #  * t.me 消息链接先解析出媒体本身（见 resolve_tme_media），这样频道里分别
    #    发布的多条帖子也能合成一个相册；解析不了的链接才退回 copy_messages。
    #  * Telegram 不支持相册带内联按钮：有按钮时，相册不带说明文字，文字+按钮
    #    作为相册下方的一条单独消息发送（避免文字重复出现）。
    #  * 无按钮时文字作为第一项的 caption（HTML）；文字超过 1024 字符时改为单独消息。
    has_buttons = bool(reply_markup)

    parsed = []
    for i, url in enumerate(media_urls):
        tg_from_chat, tg_msg_id = parse_telegram_message_link(url)
        parsed.append((url, _get_url_type(i), tg_from_chat, tg_msg_id))

    tme_targets = [(fc, mid) for _, _, fc, mid in parsed if fc and mid]
    resolved = {}
    if tme_targets:
        unique = list(dict.fromkeys(tme_targets))
        results = await asyncio.gather(
            *[resolve_tme_media(bot, chat_id, fc, mid, extra_kwargs) for fc, mid in unique],
            return_exceptions=True
        )
        for target, res in zip(unique, results):
            if isinstance(res, dict) and res.get('media') is not None:
                resolved[target] = res
        print(f"[album] t.me 链接 {len(unique)} 个，可合并为相册 {len(resolved)} 个 chat={chat_id}", flush=True)

    # entries: {'t': 'media', kind, media, src, caption, caption_entities, filename} | {'t': 'copy', from_chat, msg_id}
    entries = []
    for url, url_type, fc, mid in parsed:
        if fc and mid:
            info = resolved.get((fc, mid))
            if info:
                entries.append({'t': 'media', 'kind': info.get('kind') or 'image', 'media': info['media'],
                                'src': (fc, mid), 'caption': info.get('caption'),
                                'caption_entities': info.get('caption_entities'),
                                'filename': info.get('filename')})
            else:
                entries.append({'t': 'copy', 'from_chat': fc, 'msg_id': mid})
        else:
            entries.append({'t': 'media', 'kind': url_type if url_type in ('image', 'video') else 'image',
                            'media': url, 'src': None})

    # 连续的同类条目分段
    segments = []  # ('media', [entries]) | ('copy', from_chat, [ids])
    for e in entries:
        if e['t'] == 'media':
            if segments and segments[-1][0] == 'media':
                segments[-1][1].append(e)
            else:
                segments.append(('media', [e]))
        else:
            if segments and segments[-1][0] == 'copy' and segments[-1][1] == e['from_chat']:
                segments[-1][2].append(e['msg_id'])
            else:
                segments.append(('copy', e['from_chat'], [e['msg_id']]))

    if not segments:
        return None

    has_media_segment = any(seg[0] == 'media' for seg in segments)
    caption_on_album = bool(content) and not has_buttons and has_media_segment and len(content) <= 1024
    caption_used = False
    sent_all = []

    def _entry_caption(e, cap):
        """返回 (caption, parse_mode, caption_entities)。有自定义文字时不保留原说明。"""
        if cap:
            return cap, 'HTML', None
        if not content and e.get('caption'):
            return e['caption'], None, e.get('caption_entities')
        return None, None, None

    def _remember(chunk, msgs):
        # 下载上传的 t.me 媒体：记住返回的 file_id，下次直接复用
        try:
            for e, m in zip(chunk, msgs or []):
                if e.get('src') and not isinstance(e['media'], str):
                    info = _media_info_from_message(m)
                    if info:
                        _tme_cache_put(_tme_cache_key(bot, *e['src']), info)
        except Exception:
            pass

    async def _copy_one(e):
        fc, mid = e['src']
        kw = dict(chat_id=chat_id, from_chat_id=fc, message_id=mid, **extra_kwargs)
        if content:
            kw['caption'] = ''  # 有自定义文字时去掉原说明（文字由跟随消息发送）
        return await bot.copy_message(**kw)

    for seg in segments:
        if seg[0] == 'copy':
            # copyMessages 要求消息 ID 严格递增；原本就是同一相册的会保持分组
            ids = sorted(set(seg[2]))
            print(f"[album] method=copy_messages n={len(ids)} chat={chat_id} (未解析到可组相册的媒体，原帖若非同一相册会一张一张出现)", flush=True)
            copied = await bot.copy_messages(
                chat_id=chat_id, from_chat_id=seg[1], message_ids=ids,
                remove_caption=bool(content), **extra_kwargs
            )
            sent_all.extend(copied or [])
            continue
        items = seg[1]
        for start in range(0, len(items), MAX_MEDIA_GROUP_SIZE):
            chunk = items[start:start + MAX_MEDIA_GROUP_SIZE]
            cap = content if (caption_on_album and not caption_used) else None
            if len(chunk) == 1:
                e = chunk[0]
                c, pm, ents = _entry_caption(e, cap)
                try:
                    msg = await _send_single_media(bot, chat_id, e['kind'], e['media'], caption=c, parse_mode=pm,
                                                   caption_entities=ents, filename=e.get('filename'),
                                                   extra_kwargs=extra_kwargs)
                except Exception:
                    if not e.get('src'):
                        raise
                    msg = await _copy_one(e)
                if msg:
                    sent_all.append(msg)
                    _remember(chunk, [msg])
            else:
                media_items = []
                for j, e in enumerate(chunk):
                    c, pm, ents = _entry_caption(e, cap if j == 0 else None)
                    media_items.append(_build_input_media(e['kind'], e['media'], caption=c, parse_mode=pm,
                                                          caption_entities=ents, filename=e.get('filename')))
                group_msgs = None
                try:
                    group_msgs = await bot.send_media_group(chat_id=chat_id, media=media_items, **extra_kwargs)
                    print(f"[album] method=send_media_group n={len(group_msgs or [])} chat={chat_id}", flush=True)
                    _remember(chunk, group_msgs)
                except Exception as e_group:
                    print(f"⚠️ [album] send_media_group 失败，尝试 file_id 重传: {redact_secrets(str(e_group))}", flush=True)
                    rebuilt = []
                    for e in chunk:
                        e2 = e
                        if isinstance(e.get('media'), (bytes, bytearray)):
                            try:
                                up = await _send_single_media(
                                    bot, chat_id, e['kind'], e['media'], caption=None,
                                    filename=e.get('filename'), extra_kwargs=extra_kwargs)
                                info = _media_info_from_message(up)
                                if info:
                                    e2 = dict(e, media=info['media'], kind=info.get('kind') or e['kind'])
                                    if e2.get('src'):
                                        _tme_cache_put(_tme_cache_key(bot, *e2['src']), info)
                                    try:
                                        await bot.delete_message(chat_id=chat_id, message_id=up.message_id)
                                    except Exception:
                                        pass
                            except Exception as up_err:
                                print(f"⚠️ [album] 预上传失败: {redact_secrets(str(up_err))}", flush=True)
                        rebuilt.append(e2)
                    retry_items = []
                    for j, e in enumerate(rebuilt):
                        c, pm, ents = _entry_caption(e, cap if j == 0 else None)
                        retry_items.append(_build_input_media(
                            e['kind'], e['media'], caption=c, parse_mode=pm,
                            caption_entities=ents, filename=e.get('filename')))
                    try:
                        group_msgs = await bot.send_media_group(chat_id=chat_id, media=retry_items, **extra_kwargs)
                        print(f"[album] method=send_media_group_retry n={len(group_msgs or [])} chat={chat_id}", flush=True)
                        _remember(chunk, group_msgs)
                    except Exception as e_retry:
                        print(f"⚠️ [album] 重传相册仍失败，改为逐条发送已解析媒体: {redact_secrets(str(e_retry))}", flush=True)
                        group_msgs = []
                        for j, e in enumerate(rebuilt):
                            c, pm, ents = _entry_caption(e, cap if j == 0 else None)
                            try:
                                msg = await _send_single_media(
                                    bot, chat_id, e['kind'], e['media'], caption=c, parse_mode=pm,
                                    caption_entities=ents, filename=e.get('filename'),
                                    extra_kwargs=extra_kwargs)
                            except Exception:
                                if e.get('src'):
                                    msg = await _copy_one(e)
                                else:
                                    raise
                            if msg:
                                group_msgs.append(msg)
                        if cap:
                            cap = None
                        _remember(chunk, group_msgs)
                sent_all.extend(group_msgs or [])
            if cap:
                caption_used = True

    # 收集所有已发送消息的ID（用于 delete_previous 整组删除）
    if collected_ids is not None and sent_all:
        collected_ids.extend(m.message_id for m in sent_all)

    last_sent = sent_all[-1] if sent_all else None

    # 跟随消息：携带按钮，以及未能放进 caption 的文字
    need_follow = has_buttons or (bool(content) and not caption_used)
    if need_follow and sent_all:
        follow_text = content if content else '\u200b'
        if content and len(follow_text) > 4096:
            follow_text = follow_text[:4090] + '…'
        try:
            follow_msg = await bot.send_message(
                chat_id=chat_id,
                text=follow_text,
                parse_mode='HTML' if content else None,
                reply_markup=reply_markup,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                **extra_kwargs
            )
            if collected_ids is not None and follow_msg:
                collected_ids.append(follow_msg.message_id)
            last_sent = follow_msg
        except Exception as e:
            print(f"⚠️ 无法发送媒体组跟随消息（文字/按钮）: {e}", flush=True)

    # 返回最后一条消息（用于 auto_pin、last_message_id 存储等）
    return last_sent

def build_inline_keyboard_from_links(links):
    """
    将链接列表转换为内联键盘，支持多个按钮在一行显示
    
    Args:
        links: 链接列表，每个链接应包含 text, url, 以及可选的 row 和 order 字段
        
    Returns:
        List of button rows for InlineKeyboardMarkup
    """
    if not links:
        return []
    
    # Group buttons by row
    rows_dict = {}
    for link in links:
        if not link.get('text') or not link.get('url'):
            continue
        
        row_num = link.get('row', 0)
        order = link.get('order', 0)
        
        if row_num not in rows_dict:
            rows_dict[row_num] = []
        
        rows_dict[row_num].append({
            'button': InlineKeyboardButton(link['text'], url=link['url']),
            'order': order
        })
    
    # Sort rows and buttons within rows
    keyboard = []
    for row_num in sorted(rows_dict.keys()):
        # Sort buttons in this row by order
        row_buttons = sorted(rows_dict[row_num], key=lambda x: x['order'])
        keyboard.append([btn['button'] for btn in row_buttons])
    
    return keyboard


def parse_keyword_list(raw_keywords):
    """Parse comma/newline separated keywords into a normalized list."""
    if not raw_keywords:
        return []
    if isinstance(raw_keywords, (list, tuple)):
        items = raw_keywords
    else:
        items = re.split(r'[\n,，]+', str(raw_keywords))
    return [str(item).strip().lower() for item in items if str(item).strip()]


def text_matches_keywords(text, raw_keywords):
    """Return True when text contains any configured keyword, or no keyword is configured."""
    keywords = parse_keyword_list(raw_keywords)
    if not keywords:
        return True
    haystack = (text or '').lower()
    return any(keyword in haystack for keyword in keywords)

async def is_user_admin_in_group(bot, chat_id, user_id):
    """Check if a user is an administrator in a specific group"""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ['creator', 'administrator']
    except Exception as e:
        print(f"Error checking admin status: {e}")
        return False

async def is_user_chat_owner(bot, chat_id, user_id):
    """Check if a user is the chat owner (creator) or administrator in a specific group.

    Both creators and administrators cannot be restricted via restrictChatMember,
    so this helper returns True for either status.
    
    Args:
        bot: Telegram bot instance
        chat_id: The chat/group ID
        user_id: The user's Telegram ID
        
    Returns:
        bool: True if user is chat owner or administrator, False otherwise
    """
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ('creator', 'administrator')
    except Exception as e:
        if 'Chat not found' in str(e):
            raise
        print(f"Error checking chat owner status: {e}")
        return False

def get_muted_permissions():
    """返回完全禁言的权限设置（统一管理以避免代码重复）
    
    使用最简单、最兼容的配置：只禁止发送消息
    这是最兼容的方式，避免某些 Telegram 版本或配置下的 API 调用失败
    
    Returns:
        ChatPermissions: 禁止发送消息的权限对象
    """
    return ChatPermissions(
        can_send_messages=False
    )

def get_unrestricted_permissions():
    """返回完全开放的权限设置（统一管理以避免代码重复）
    
    Returns:
        ChatPermissions: 所有权限都被允许的权限对象
    """
    return ChatPermissions(
        can_send_messages=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True
    )

def log_admin_action(group_id, admin_id, admin_name, action_type, target_user_id=None, target_user_name=None, details=None):
    """记录管理员操作到数据库
    
    Args:
        group_id: 群组ID (数据库ID)
        admin_id: 管理员Telegram ID
        admin_name: 管理员名称
        action_type: 操作类型 (kick, ban, mute, unmute, etc.)
        target_user_id: 目标用户Telegram ID (可选)
        target_user_name: 目标用户名称 (可选)
        details: 详细信息 (可选)
    """
    if not global_flask_app:
        return
    
    try:
        with global_flask_app.app_context():
            log = AdminActionLog(
                group_id=group_id,
                admin_id=admin_id,
                admin_name=admin_name,
                action_type=action_type,
                target_user_id=target_user_id,
                target_user_name=target_user_name,
                details=details
            )
            db.session.add(log)
            db.session.commit()
    except Exception as e:
        print(f"Error logging admin action: {e}")


def convert_chat_id_to_int(chat_id, group_id=None, group_title=None):
    """Helper function to convert chat_id to integer for Telegram API.
    
    Args:
        chat_id: The chat_id string to convert
        group_id: Optional group ID for error messages
        group_title: Optional group title for error messages
        
    Returns:
        The chat_id as an integer, or None if conversion fails
    """
    try:
        return int(chat_id)
    except (ValueError, TypeError) as e:
        error_msg = f"Invalid chat_id '{chat_id}'"
        if group_id:
            error_msg += f" for group {group_id}"
        if group_title:
            error_msg += f" ({group_title})"
        error_msg += f": {e}"
        print(error_msg)
        return None

def unban_user_in_group(group_id, user_tg_id):
    """Helper function to unban a user in a Telegram group by lifting all restrictions.
    
    Args:
        group_id: The database group ID
        user_tg_id: The Telegram user ID
        
    Returns:
        True if successful, False otherwise
    """
    try:
        group = BotGroup.query.get(group_id)
        if not group:
            print(f"Group {group_id} not found for unbanning user {user_tg_id}")
            return False
        
        # Convert chat_id to integer for Telegram API
        chat_id_int = convert_chat_id_to_int(group.chat_id, group.id)
        if chat_id_int is None:
            return False
        
        # Check if user is chat owner/admin - skip unmute for them (cannot restrict admins)
        async def _check_and_unban():
            is_owner = await is_user_chat_owner(global_ptb_app.bot, chat_id_int, user_tg_id)
            if is_owner:
                print(f"⏭️ [解除禁言] 跳过解除禁言操作 - 用户 {user_tg_id} 是群主/管理员 (Owner/Admin) in group {group.chat_id} (chat_id={chat_id_int})", flush=True)
                return True  # Return True since no action needed for chat owner
            
            await global_ptb_app.bot.restrict_chat_member(
                chat_id=chat_id_int,
                user_id=user_tg_id,
                permissions=get_unrestricted_permissions()
            )
            print(f"✅ User {user_tg_id} unbanned in group {group.chat_id}")
            return True
        
        asyncio.run_coroutine_threadsafe(
            _check_and_unban(),
            global_bot_loop
        ).result(timeout=5)
        return True
    except Exception as e:
        print(f"Failed to unban user {user_tg_id} in group {group.chat_id} (chat_id type: {type(group.chat_id).__name__}): {e}")
        return False

# --- Webhook ---
@core_bp.route('/webhook', methods=['POST'])
def webhook():
    if not global_ptb_app or not global_bot_loop: return "Bot Not Ready", 503
    try:
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, global_ptb_app.bot)
        
        # 添加 Future 回调以捕获异步任务中的异常
        future = asyncio.run_coroutine_threadsafe(global_ptb_app.process_update(update), global_bot_loop)
        
        def check_future_exception(fut):
            try:
                exc = fut.exception()
                if exc:
                    import traceback
                    print(f"❌ Webhook 异步任务异常:")
                    print(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            except Exception as e:
                print(f"❌ 回调函数本身异常: {e}")
        
        future.add_done_callback(check_future_exception)
        return "OK", 200
    except Exception as e:
        print(f"❌ Webhook Error: {e}")
        return "Error", 200

@core_bp.route('/clone_webhook/<int:clone_id>', methods=['POST'])
def clone_webhook(clone_id):
    """接收克隆机器人的 Webhook 更新并分发给对应的 Application 实例"""
    if not global_bot_loop: return "Bot Not Ready", 503
    clone_info = bot_clone_manager.active_clones.get(clone_id)
    if not clone_info: return "Clone Not Found", 404
    try:
        json_data = request.get_json(force=True)
        clone_app = clone_info['app']
        update = Update.de_json(json_data, clone_app.bot)

        future = asyncio.run_coroutine_threadsafe(clone_app.process_update(update), global_bot_loop)

        def check_future_exception(fut):
            try:
                exc = fut.exception()
                if exc:
                    print(f"❌ Clone Webhook 异步任务异常 (clone_id={clone_id}):")
                    print(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            except Exception as e:
                print(f"❌ Clone Webhook 回调函数本身异常: {e}")

        future.add_done_callback(check_future_exception)
        return "OK", 200
    except Exception as e:
        print(f"❌ Clone Webhook Error (clone_id={clone_id}): {e}")
        return "Error", 200

# --- Context ---
@core_bp.context_processor
def inject_context():
    data = {'all_groups': []}
    if session.get('logged_in'):
        clone_id = session.get('clone_id')
        data['all_groups'] = BotGroup.query.filter_by(clone_id=clone_id).order_by(BotGroup.is_active.desc(), BotGroup.updated_at.desc()).all()
    gid = session.get('current_group_id')
    if gid: data['current_group'] = BotGroup.query.get(gid)
    return data

def safe_int(val, default=0):
    if val is None: return default
    if isinstance(val, str) and val.strip() == '': return default
    try: return int(val)
    except: return default

# --- Security helpers ---

def get_group_or_403(gid: int) -> BotGroup:
    """Fetch a BotGroup by its primary key, aborting with 403 if the
    currently logged-in session does not have access to it.

    Clone admins (session['clone_id'] is set) may only access groups that
    belong to their own clone.  Global admins (no clone_id in session) may
    access all groups.
    """
    group = BotGroup.query.get_or_404(gid)
    session_clone_id = session.get('clone_id')
    if session_clone_id and group.clone_id != session_clone_id:
        abort(403)
    return group


def _api_check_group_access(group: BotGroup):
    """Return a JSON error response if the session cannot access *group*,
    otherwise return ``None``.  Intended for use in API (JSON) endpoints.
    """
    session_clone_id = session.get('clone_id')
    if session_clone_id and group.clone_id != session_clone_id:
        return jsonify({'status': 'error', 'msg': '无权操作此群组'})
    return None


def _delete_group_cascade(group_id: int) -> None:
    """Delete all database records associated with *group_id*.

    Handles both direct children (FK → bot_groups.id) and indirect
    grandchildren (e.g. VoteRecord → GroupVote, QuizAnswer → QuizSession,
    RedPacketClaim → RedPacket) in the correct order so that FK constraints
    are not violated.
    """
    # --- Grandchildren first (FK references a child, not the group directly) ---

    # vote_record → group_vote
    vote_ids = db.session.query(GroupVote.id).filter_by(group_id=group_id).subquery()
    VoteRecord.query.filter(VoteRecord.vote_id.in_(vote_ids)).delete(synchronize_session=False)

    # quiz_answer → quiz_session
    session_ids = db.session.query(QuizSession.id).filter_by(group_id=group_id).subquery()
    QuizAnswer.query.filter(QuizAnswer.session_id.in_(session_ids)).delete(synchronize_session=False)

    # red_packet_claim → red_packet
    packet_ids = db.session.query(RedPacket.id).filter_by(group_id=group_id).subquery()
    RedPacketClaim.query.filter(RedPacketClaim.packet_id.in_(packet_ids)).delete(synchronize_session=False)

    # --- Direct children (FK column = group_id) ---
    for model in (
        GroupUser, AutoReply, ScheduledMessage, StartMessage,
        GroupEntryExitSettings, SpamProtection, TimedGroupControl,
        InvitationActivity, InvitationRecord, ForcedChannelSubscription,
        PointsRule, PointsAutoReply, PointsAuction, PointsLog, UserPoints,
        PointsExchangeItem, PointsExchangeRecord,
        GroupLottery, LotteryMessageCount,
        MemberLevel, UserNameChange, GroupBottomButton,
        OtherSettings, InactiveUserSettings, KeywordFilter, MessageStatistics,
        GroupVote, QuizGame, QuizSession, RedPacket,
        AdminActionLog, GroupMember,
        ChannelForwardRule, ChannelMessageTemplate, ChannelCoupon,
        ChannelPostButtonConfig, ChannelMessageStats,
    ):
        model.query.filter_by(group_id=group_id).delete(synchronize_session=False)

    # --- Different FK column name ---
    SyncGroupMessages.query.filter_by(source_group_id=group_id).delete(synchronize_session=False)
    SyncMessageLog.query.filter_by(source_group_id=group_id).delete(synchronize_session=False)


def get_group_conf(group):
    conf = DEFAULT_SYSTEM.copy()
    if group and group.config:
        try:
            c = json.loads(group.config)
            if isinstance(c, dict) and 'config' in c: c = c['config']
            for k, v in c.items():
                if v is not None: conf[k] = v
        except: pass
    # Always expose a Telegram-valid reaction emoji in conf (legacy ❤️ etc.).
    conf['like_emoji'] = normalize_like_emoji(conf.get('like_emoji', DEFAULT_LIKE_EMOJI))
    return conf

def get_group_fields(group):
    if group and group.fields_config:
        try: return json.loads(group.fields_config)
        except: pass
    return DEFAULT_FIELDS


def _inject_report_links(text: str, tg_id, bot_username: str = None) -> str:
    """Replace {写报告链接} and {查报告链接} placeholders with Bot deep-links.

    ``bot_username`` should be the username of the bot that will handle the
    deep-link (clone bot for clone-managed groups, main bot otherwise).
    Falls back to the global main-bot username when not provided.
    """
    username = bot_username or (global_ptb_app.bot.username if global_ptb_app else None)
    if username:
        text = text.replace('{写报告链接}', f'https://t.me/{username}?start=report_{tg_id}')
        text = text.replace('{查报告链接}', f'https://t.me/{username}?start=view_{tg_id}')
    return text


# --- Web Routes ---
@core_bp.route('/')
def index(): return redirect('/core/select_group') if session.get('logged_in') else render_template('base.html', page='login')

@core_bp.route('/select_group')
def page_select_group():
    if not session.get('logged_in'): return redirect('/core')
    session.pop('current_group_id', None)
    clone_id = session.get('clone_id')
    all_entries = BotGroup.query.filter_by(clone_id=clone_id).order_by(BotGroup.is_active.desc(), BotGroup.updated_at.desc()).all()
    groups = [g for g in all_entries if g.type in ('group', 'supergroup')]
    channels = [g for g in all_entries if g.type == 'channel']

    # For clone backends, retrieve the bot's @username so the template can
    # display it in the manual-registration hint (helps users verify they
    # added the correct bot account to their group).
    clone_bot_username = None
    if clone_id:
        # Prefer the live bot_data cached by python-telegram-bot (no extra
        # HTTP request needed when the clone is already running).
        clone_info = bot_clone_manager.active_clones.get(clone_id)
        if clone_info:
            try:
                clone_bot_username = clone_info['app'].bot.username
            except Exception:
                pass
        # Fallback: one synchronous getMe call when the clone is not running.
        if not clone_bot_username:
            try:
                clone = BotClone.query.get(clone_id)
                if clone:
                    bot_token = clone.get_bot_token()
                    me_resp = requests.get(
                        f"https://api.telegram.org/bot{bot_token}/getMe",
                        timeout=5,
                    )
                    me_data = me_resp.json()
                    if me_data.get('ok'):
                        clone_bot_username = me_data['result'].get('username')
            except Exception:
                pass

    return render_template('select_group.html', groups=groups, channels=channels,
                           clone_bot_username=clone_bot_username)

@core_bp.route('/group/<int:gid>/dashboard')
def page_dashboard(gid):
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    
    # Enhanced statistics
    total_users = GroupUser.query.filter_by(group_id=gid).count()
    online_users = GroupUser.query.filter_by(group_id=gid, online=True).count()
    
    # Get today's check-ins
    today = get_beijing_today()
    today_checkins = GroupUser.query.filter(
        GroupUser.group_id == gid,
        GroupUser.checkin_time >= today
    ).count()
    
    # Get expired/banned users
    now = get_beijing_now()
    expired_users = GroupUser.query.filter(
        GroupUser.group_id == gid,
        GroupUser.expiration_date.isnot(None),
        GroupUser.expiration_date < now
    ).count()
    
    banned_users = GroupUser.query.filter_by(group_id=gid, is_banned=True).count()
    
    # Get module counts
    auto_replies_count = AutoReply.query.filter_by(group_id=gid, is_active=True).count()
    scheduled_msgs_count = ScheduledMessage.query.filter_by(group_id=gid, is_active=True).count()
    start_msgs_count = StartMessage.query.filter_by(group_id=gid, is_active=True).count()
    
    # 🆕 Enhanced statistics - Total points
    total_points = db.session.query(func.sum(UserPoints.points_balance)).filter(
        UserPoints.group_id == gid
    ).scalar() or 0
    
    # 🆕 Get today's message count
    today_messages = db.session.query(func.sum(MessageStatistics.message_count)).filter(
        MessageStatistics.group_id == gid,
        MessageStatistics.date == today.date()
    ).scalar() or 0
    
    # 🆕 Get this week's statistics
    week_ago = today - timedelta(days=7)
    week_messages = db.session.query(func.sum(MessageStatistics.message_count)).filter(
        MessageStatistics.group_id == gid,
        MessageStatistics.date >= week_ago.date()
    ).scalar() or 0
    
    # 🆕 Get new users this week
    new_users_week = GroupUser.query.filter(
        GroupUser.group_id == gid,
        GroupUser.created_at >= week_ago
    ).count()
    
    # 🆕 Get invitations count (users who invited others)
    # Wrapped in try-except to handle cases where announce_in_group column doesn't exist yet
    try:
        invitation_activity = InvitationActivity.query.filter_by(group_id=gid, enabled=True).first()
    except (ProgrammingError, OperationalError) as e:
        # Column might not exist in database yet - run migration
        logging.warning(f"Could not query invitation_activity table: {e}")
        logging.warning("This usually means the announce_in_group column doesn't exist yet.")
        logging.warning("Run 'python migrate_database.py' to add missing columns.")
        invitation_activity = None
        # Rollback the session to prevent transaction errors
        db.session.rollback()
    
    # 🆕 Top points users
    top_points_users = db.session.query(UserPoints.user_id, UserPoints.points_balance).filter(
        UserPoints.group_id == gid
    ).order_by(UserPoints.points_balance.desc()).limit(5).all()
    
    # 🆕 Security module status
    spam_protection = SpamProtection.query.filter_by(group_id=gid).first()
    forced_subscription = ForcedChannelSubscription.query.filter_by(group_id=gid).first()
    keyword_filter_count = KeywordFilter.query.filter_by(group_id=gid, is_active=True).count()
    
    stats = {
        'users': total_users,
        'online': online_users,
        'today_checkins': today_checkins,
        'expired': expired_users,
        'banned': banned_users,
        'auto_replies': auto_replies_count,
        'scheduled_msgs': scheduled_msgs_count,
        'start_msgs': start_msgs_count,
        # 🆕 Enhanced statistics
        'total_points': total_points,
        'today_messages': today_messages,
        'week_messages': week_messages,
        'new_users_week': new_users_week,
        'invitation_enabled': invitation_activity.enabled if invitation_activity else False,
        'top_points_users': top_points_users,
        'spam_protection_enabled': spam_protection.enabled if spam_protection else False,
        'forced_subscription_enabled': forced_subscription.enabled if forced_subscription else False,
        'keyword_filters': keyword_filter_count
    }
    
    return render_template('dashboard.html', page='dashboard', group=group, stats=stats)

@core_bp.route('/group/<int:gid>/users')
def page_users(gid):
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    
    # Pagination parameters with enhanced options
    page = safe_int(request.args.get('page', 1), 1)
    per_page = safe_int(request.args.get('per_page', 20), 20)
    # Support larger page sizes including 100
    if per_page not in [10, 20, 50, 100] or per_page <= 0: per_page = 20
    if page < 1: page = 1
    
    # Search parameter
    search_query = request.args.get('search', '').strip()
    
    # Build query with optional search filter
    query = GroupUser.query.filter_by(group_id=gid)
    if search_query:
        # Search in tg_id, profile_data, and custom member tags
        query = query.filter(
            or_(
                cast(GroupUser.tg_id, String).contains(search_query),
                GroupUser.profile_data.contains(search_query),
                GroupUser.member_tags.contains(search_query),
            )
        )
    
    # Get total count and paginated users
    total_users = query.count()
    total_pages = math.ceil(total_users / per_page) if total_users > 0 else 1
    if page > total_pages: page = total_pages
    
    users = query.order_by(GroupUser.id.desc()).offset((page-1)*per_page).limit(per_page).all()
    for u in users:
        try: u.profile_dict = json.loads(u.profile_data) if u.profile_data else {}
        except: u.profile_dict = {}
        u.member_tag_list = parse_member_tags(getattr(u, 'member_tags', None))
    
    # 批量查询 GroupMember 以获取昵称和用户名（限定本群组，避免跨群组数据污染）
    tg_ids = [u.tg_id for u in users]
    member_map = {}
    if tg_ids:
        members_info = GroupMember.query.filter(
            GroupMember.user_id.in_(tg_ids),
            GroupMember.group_id == gid
        ).all()
        member_map = {m.user_id: m for m in members_info}
    for u in users:
        m = member_map.get(u.tg_id)
        u.tg_display_name = f"{m.first_name or ''} {m.last_name or ''}".strip() if m else ''
        u.tg_username = m.username if m else ''

    return render_template('users.html', page='users', group=group, users=users, fields=get_group_fields(group), 
                         current_page=page, total_pages=total_pages, per_page=per_page, total_users=total_users, search=search_query)

@core_bp.route('/group/<int:gid>/members')
def page_group_members(gid):
    """群成员列表页面 - 显示从 Telegram 同步的实际群成员"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    
    # Pagination parameters
    page = safe_int(request.args.get('page', 1), 1)
    per_page = safe_int(request.args.get('per_page', 20), 20)
    if per_page not in [10, 20, 50, 100] or per_page <= 0: per_page = 20
    if page < 1: page = 1
    
    # Search and filter parameters
    search_query = request.args.get('search', '').strip()
    filter_type = request.args.get('filter', 'all')  # all, admin, member, bot
    
    # 从 GroupMember 表查询实际群成员
    query = GroupMember.query.filter_by(group_id=gid)
    
    # 搜索过滤
    if search_query:
        query = query.filter(
            db.or_(
                cast(GroupMember.user_id, String).contains(search_query),
                GroupMember.username.ilike(f'%{search_query}%'),
                GroupMember.first_name.ilike(f'%{search_query}%')
            )
        )
    
    # 身份过滤
    if filter_type == 'admin':
        query = query.filter(GroupMember.status.in_(['creator', 'administrator']))
    elif filter_type == 'member':
        query = query.filter(GroupMember.status == 'member')
    elif filter_type == 'bot':
        query = query.filter(GroupMember.is_bot == True)
    
    # 分页
    pagination = query.order_by(
        case(
            (GroupMember.status == 'creator', 1),
            (GroupMember.status == 'administrator', 2),
            else_=3
        ),
        GroupMember.first_name
    ).paginate(page=page, per_page=per_page, error_out=False)
    
    members = pagination.items
    
    # 获取成员积分和认证状态（批量查询避免N+1问题）
    member_info = {}
    if members:
        user_ids = [member.user_id for member in members]
        
        # 批量查询积分
        points_list = UserPoints.query.filter(
            UserPoints.group_id == gid,
            UserPoints.user_id.in_(user_ids)
        ).all()
        points_dict = {p.user_id: p for p in points_list}
        
        # 批量查询认证用户
        verified_list = GroupUser.query.filter(
            GroupUser.group_id == gid,
            GroupUser.tg_id.in_(user_ids)
        ).all()
        verified_dict = {u.tg_id: u for u in verified_list}
        
        # 构建成员信息字典
        for member in members:
            points = points_dict.get(member.user_id)
            verified_user = verified_dict.get(member.user_id)
            member_info[member.user_id] = {
                'points': points.points_balance if points else 0,
                'is_verified': verified_user is not None,
                'verified_user': verified_user
            }
    
    # 统计
    stats = {
        'total': GroupMember.query.filter_by(group_id=gid).count(),
        'admins': GroupMember.query.filter_by(group_id=gid).filter(
            GroupMember.status.in_(['creator', 'administrator'])
        ).count(),
        'bots': GroupMember.query.filter_by(group_id=gid, is_bot=True).count(),
        'verified': GroupUser.query.filter_by(group_id=gid).count(),
        'last_sync': GroupMember.query.filter_by(group_id=gid).order_by(
            GroupMember.synced_at.desc()
        ).first()
    }
    
    return render_template('group_members.html', 
                         page='group_members', 
                         group=group, 
                         members=members,
                         pagination=pagination,
                         member_info=member_info,
                         search=search_query,
                         filter_type=filter_type,
                         stats=stats,
                         now=datetime.now())

@core_bp.route('/group/<int:gid>/fields')
def page_fields(gid):
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    return render_template('fields.html', page='fields', group=group, fields_json=json.dumps(get_group_fields(group)))

@core_bp.route('/group/<int:gid>/settings')
def page_settings(gid):
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    return render_template(
        'settings.html',
        page='settings',
        group=group,
        conf=get_group_conf(group),
        fields=get_group_fields(group),
        reaction_emojis=TELEGRAM_REACTION_EMOJIS,
    )

@core_bp.route('/group/<int:gid>/auto_replies')
def page_auto_replies(gid):
    """自动回复管理页面"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    
    # Pagination parameters with enhanced options
    page = safe_int(request.args.get('page', 1), 1)
    per_page = safe_int(request.args.get('per_page', 20), 20)
    # Support larger page sizes including 100
    if per_page not in [10, 20, 50, 100] or per_page <= 0: per_page = 20
    if page < 1: page = 1
    
    # Search parameter
    search_query = request.args.get('search', '').strip()
    
    # Build query with optional search filter
    query = AutoReply.query.filter_by(group_id=gid)
    if search_query:
        # Search in trigger_keyword and remark
        query = query.filter(
            or_(
                AutoReply.trigger_keyword.contains(search_query),
                AutoReply.remark.contains(search_query)
            )
        )
    
    # Get total count and paginated results
    total_items = query.count()
    total_pages = math.ceil(total_items / per_page) if total_items > 0 else 1
    if page > total_pages: page = total_pages
    
    auto_replies = query.order_by(AutoReply.id.desc()).offset((page-1)*per_page).limit(per_page).all()
    
    return render_template('auto_replies.html', page='auto_replies', group=group, 
                          auto_replies=auto_replies,
                          current_page=page, total_pages=total_pages, per_page=per_page, total_items=total_items, search=search_query)

@core_bp.route('/group/<int:gid>/scheduled_messages')
def page_scheduled_messages(gid):
    """定时消息管理页面"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    
    # Pagination parameters with enhanced options
    page = safe_int(request.args.get('page', 1), 1)
    per_page = safe_int(request.args.get('per_page', 20), 20)
    # Support larger page sizes including 100
    if per_page not in [10, 20, 50, 100] or per_page <= 0: per_page = 20
    if page < 1: page = 1
    
    # Search parameter
    search_query = request.args.get('search', '').strip()
    
    # Build query with optional search filter
    query = ScheduledMessage.query.filter_by(group_id=gid)
    if search_query:
        # Search in content and remark
        query = query.filter(
            or_(
                ScheduledMessage.content.contains(search_query),
                ScheduledMessage.remark.contains(search_query)
            )
        )
    
    # Get total count and paginated results
    total_items = query.count()
    total_pages = math.ceil(total_items / per_page) if total_items > 0 else 1
    if page > total_pages: page = total_pages
    
    scheduled_messages = query.order_by(ScheduledMessage.id.desc()).offset((page-1)*per_page).limit(per_page).all()
    
    return render_template('scheduled_messages.html', page='scheduled_messages', group=group,
                          scheduled_messages=scheduled_messages,
                          current_page=page, total_pages=total_pages, per_page=per_page, total_items=total_items, search=search_query)

@core_bp.route('/group/<int:gid>/start_messages')
def page_start_messages(gid):
    """自定义 /start 消息管理页面"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    
    # Pagination parameters with enhanced options
    page = safe_int(request.args.get('page', 1), 1)
    per_page = safe_int(request.args.get('per_page', 20), 20)
    # Support larger page sizes including 100
    if per_page not in [10, 20, 50, 100] or per_page <= 0: per_page = 20
    if page < 1: page = 1
    
    # Get total count and paginated results
    total_items = StartMessage.query.filter_by(group_id=gid).count()
    total_pages = math.ceil(total_items / per_page) if total_items > 0 else 1
    if page > total_pages: page = total_pages
    
    start_messages = StartMessage.query.filter_by(group_id=gid).order_by(StartMessage.message_type.asc(), StartMessage.id.desc()).offset((page-1)*per_page).limit(per_page).all()
    
    # 转换为JSON供前端使用
    start_messages_json = json.dumps([{
        'id': sm.id,
        'message_type': sm.message_type,
        'media_type': sm.media_type,
        'media_url': sm.media_url,
        'content': sm.content,
        'links': sm.links,
        'is_active': sm.is_active
    } for sm in start_messages], ensure_ascii=False)
    
    return render_template('start_messages.html', page='start_messages', group=group,
                          start_messages=start_messages, start_messages_json=start_messages_json,
                          current_page=page, total_pages=total_pages, per_page=per_page, total_items=total_items,
                          conf=get_group_conf(group))

@core_bp.route('/group/<int:gid>/entry_exit_settings')
def page_entry_exit_settings(gid):
    """进退群设置"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    settings = GroupEntryExitSettings.query.filter_by(group_id=gid).first()
    if not settings:
        settings = GroupEntryExitSettings(group_id=gid)
        db.session.add(settings)
        db.session.commit()
    # Pre-process verification_options into a newline-separated string for the template textarea
    verification_options_text = ''
    if settings.verification_options:
        try:
            opts = json.loads(settings.verification_options)
            if isinstance(opts, list):
                verification_options_text = '\n'.join(opts)
        except (ValueError, TypeError):
            pass
    return render_template('entry_exit_settings.html', page='entry_exit_settings', group=group,
                           settings=settings, verification_options_text=verification_options_text)

@core_bp.route('/group/<int:gid>/spam_protection')
def page_spam_protection(gid):
    """垃圾防护"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    settings = SpamProtection.query.filter_by(group_id=gid).first()
    whitelist_users = json.loads(settings.whitelist_users if settings and settings.whitelist_users else '[]')
    return render_template('spam_protection.html', page='spam_protection', group=group, settings=settings, whitelist_users=whitelist_users)

@core_bp.route('/group/<int:gid>/timed_group_control')
def page_timed_group_control(gid):
    """定时开关群"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    settings = TimedGroupControl.query.filter_by(group_id=gid).first()
    return render_template('timed_group_control.html', page='timed_group_control', group=group, settings=settings)

@core_bp.route('/group/<int:gid>/other_settings')
def page_other_settings(gid):
    """其他设置"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    settings = OtherSettings.query.filter_by(group_id=gid).first()
    if not settings:
        settings = OtherSettings(group_id=gid)
        db.session.add(settings)
        db.session.commit()
    return render_template('other_settings.html', page='other_settings', group=group, settings=settings)

@core_bp.route('/group/<int:gid>/invitation_activity')
def page_invitation_activity(gid):
    """邀请活动"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    
    # Wrapped in try-except to handle cases where announce_in_group column doesn't exist yet
    try:
        settings = InvitationActivity.query.filter_by(group_id=gid).first()
        if not settings:
            settings = InvitationActivity(group_id=gid)
            db.session.add(settings)
            db.session.commit()
    except (ProgrammingError, OperationalError) as e:
        # Column might not exist in database yet - run migration
        logging.error(f"Could not query invitation_activity table: {e}")
        logging.error("This usually means the announce_in_group column doesn't exist yet.")
        logging.error("Run 'python migrate_database.py' to add missing columns.")
        # Rollback the session to prevent transaction errors
        db.session.rollback()
        # Return error page with helpful message
        return render_template('error.html', 
                             error_title='Database Migration Required',
                             error_message='The invitation_activity table is missing required columns. Please run database migration: python migrate_database.py'), 500
    
    # Get invitation leaderboard (top 10 inviters)
    invitation_leaderboard = db.session.query(
        InvitationRecord.inviter_id,
        func.count(InvitationRecord.id).label('invite_count'),
        func.sum(InvitationRecord.points_awarded).label('total_points')
    ).filter(
        InvitationRecord.group_id == gid
    ).group_by(
        InvitationRecord.inviter_id
    ).order_by(
        func.count(InvitationRecord.id).desc()
    ).limit(10).all()
    
    # Get recent invitation records
    recent_invitations = InvitationRecord.query.filter_by(
        group_id=gid
    ).order_by(InvitationRecord.created_at.desc()).limit(20).all()
    
    # Get total invitations count
    total_invitations = InvitationRecord.query.filter_by(group_id=gid).count()
    
    return render_template('invitation_activity.html', 
                          page='invitation_activity', 
                          group=group, 
                          settings=settings,
                          leaderboard=invitation_leaderboard,
                          recent_invitations=recent_invitations,
                          total_invitations=total_invitations)

@core_bp.route('/group/<int:gid>/forced_channel_subscription')
def page_forced_channel_subscription(gid):
    """强制订阅频道"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    settings = ForcedChannelSubscription.query.filter_by(group_id=gid).first()
    if not settings:
        settings = ForcedChannelSubscription(group_id=gid)
        db.session.add(settings)
        db.session.commit()
    return render_template('forced_channel_subscription.html', page='forced_channel_subscription', group=group, settings=settings)

@core_bp.route('/group/<int:gid>/points_rules')
def page_points_rules(gid):
    """积分规则"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    rules = PointsRule.query.filter_by(group_id=gid).all()
    return render_template('points_rules.html', page='points_rules', group=group, rules=rules)

@core_bp.route('/group/<int:gid>/points_auto_reply')
def page_points_auto_reply(gid):
    """积分自动回复"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    replies = PointsAutoReply.query.filter_by(group_id=gid).all()
    return render_template('points_auto_reply.html', page='points_auto_reply', group=group, replies=replies)

@core_bp.route('/group/<int:gid>/points_auction')
def page_points_auction(gid):
    """积分竞拍 - redirect to unified points shop, auction tab"""
    if not session.get('logged_in'): return redirect('/core')
    return redirect(f'/core/group/{int(gid)}/points_shop#auction')

@core_bp.route('/group/<int:gid>/points_shop')
def page_points_shop(gid):
    """积分商城 — 积分兑换 + 积分竞拍统一入口"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    conf = get_group_conf(group)
    # Exchange data
    items = PointsExchangeItem.query.filter_by(group_id=gid).order_by(PointsExchangeItem.created_at.desc()).all()
    records = (PointsExchangeRecord.query
               .filter_by(group_id=gid)
               .order_by(PointsExchangeRecord.created_at.desc())
               .limit(100).all())
    # Auction data
    auctions = PointsAuction.query.filter_by(group_id=gid).order_by(PointsAuction.created_at.desc()).all()
    auctions_json = [
        {
            'item_name': a.item_name,
            'item_description': a.item_description or '',
            'starting_price': a.starting_price,
            'auction_start': a.auction_start.strftime('%Y-%m-%dT%H:%M') if a.auction_start else '',
            'auction_end': a.auction_end.strftime('%Y-%m-%dT%H:%M') if a.auction_end else '',
            'status': a.status,
        }
        for a in auctions
    ]
    # Determine which tab to open based on the 'tab' query param (used by server-side redirects)
    active_tab = request.args.get('tab', '')
    return render_template('points_shop.html', page='points_shop', group=group,
                           conf=conf, items=items, records=records,
                           auctions=auctions, auctions_json=auctions_json,
                           active_tab=active_tab)

@core_bp.route('/group/<int:gid>/points_log')
def page_points_log(gid):
    """积分日志"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)

    page = request.args.get('page', 1, type=int)
    per_page = 50
    search_user = request.args.get('search_user', '').strip()
    search_reason = request.args.get('search_reason', '').strip()

    q = PointsLog.query.filter_by(group_id=gid)
    if search_user:
        try:
            uid = int(search_user)
            q = q.filter(PointsLog.user_id == uid)
        except ValueError:
            pass
    if search_reason:
        q = q.filter(PointsLog.reason.ilike(f'%{search_reason}%'))

    pagination = q.order_by(PointsLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    logs = pagination.items
    filter_qs = urlencode({'search_user': search_user, 'search_reason': search_reason})

    # Top 10 leaderboard for points_log sidebar
    top_users = db.session.query(UserPoints.user_id, UserPoints.points_balance).filter(
        UserPoints.group_id == gid
    ).order_by(UserPoints.points_balance.desc()).limit(10).all()
    top_users = [{'user_id': u.user_id, 'total_points': u.points_balance} for u in top_users]

    return render_template('points_log.html', page='points_log', group=group, logs=logs,
                           pagination=pagination, search_user=search_user, search_reason=search_reason,
                           filter_qs=filter_qs, top_users=top_users)

@core_bp.route('/group/<int:gid>/points_exchange')
def page_points_exchange(gid):
    """积分兑换 - redirect to unified points shop"""
    if not session.get('logged_in'): return redirect('/core')
    return redirect(f'/core/group/{int(gid)}/points_shop')

@core_bp.route('/group/<int:gid>/group_lottery')
def page_group_lottery(gid):
    """群抽奖"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    lotteries = GroupLottery.query.filter_by(group_id=gid).order_by(GroupLottery.created_at.desc()).all()
    
    # Convert to JSON for JavaScript
    lotteries_json = json.dumps([{
        'id': l.id,
        'lottery_name': l.lottery_name,
        'lottery_type': l.lottery_type,
        'min_messages': l.min_messages,
        'top_n_winners': l.top_n_winners,
        'prize_description': l.prize_description or '',
        'start_time': l.start_time.strftime('%Y-%m-%dT%H:%M') if l.start_time else '',
        'end_time': l.end_time.strftime('%Y-%m-%dT%H:%M') if l.end_time else '',
        'announce_channel_id': l.announce_channel_id or '',
        'status': l.status,
        'winner_ids': l.winner_ids
    } for l in lotteries], ensure_ascii=False)
    
    return render_template('group_lottery.html', page='group_lottery', group=group, lotteries=lotteries, lotteries_json=lotteries_json)

@core_bp.route('/group/<int:gid>/member_level')
def page_member_level(gid):
    """成员等级"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    levels = MemberLevel.query.filter_by(group_id=gid).order_by(MemberLevel.required_points).all()
    return render_template('member_level.html', page='member_level', group=group, levels=levels)

@core_bp.route('/group/<int:gid>/user_name_change')
def page_user_name_change(gid):
    """用户改名监控"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    changes = UserNameChange.query.filter_by(group_id=gid).order_by(UserNameChange.changed_at.desc()).limit(100).all()
    return render_template('user_name_change.html', page='user_name_change', group=group, changes=changes)

@core_bp.route('/group/<int:gid>/group_bottom_button')
def page_group_bottom_button(gid):
    """群底部按钮"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    buttons = GroupBottomButton.query.filter_by(group_id=gid).order_by(GroupBottomButton.row_position, GroupBottomButton.button_order).all()
    
    # Convert buttons to JSON-serializable dictionaries
    buttons_json = json.dumps([{
        'id': b.id,
        'button_text': b.button_text,
        'button_url': b.button_url,
        'button_callback': b.button_callback,
        'button_order': b.button_order,
        'row_position': b.row_position,
        'trigger_keyword': b.trigger_keyword,
        'is_active': b.is_active
    } for b in buttons], ensure_ascii=False)
    
    return render_template('group_bottom_button.html', page='group_bottom_button', group=group, buttons=buttons, buttons_json=buttons_json)

def _sync_page_context(gid):
    """Shared context for group/channel sync settings pages (multi-target list)."""
    targets = (
        SyncGroupMessages.query.filter_by(source_group_id=gid)
        .order_by(SyncGroupMessages.id.asc())
        .all()
    )
    # Shared options: first row if any, else sensible defaults for empty form
    settings = targets[0] if targets else None
    return targets, settings


@core_bp.route('/group/<int:gid>/sync_group_messages')
def page_sync_group_messages(gid):
    """同步群消息（仅群组；频道请用 sync_channel_messages）"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    if getattr(group, 'type', None) == 'channel':
        return redirect(f'/core/group/{gid}/sync_channel_messages')
    targets, settings = _sync_page_context(gid)
    return render_template(
        'sync_group_messages.html',
        page='sync_group_messages',
        group=group,
        settings=settings,
        targets=targets,
    )


@core_bp.route('/group/<int:gid>/sync_channel_messages')
def page_sync_channel_messages(gid):
    """同步频道帖子（仅频道；群组请用 sync_group_messages）"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    if getattr(group, 'type', None) != 'channel':
        return redirect(f'/core/group/{gid}/sync_group_messages')
    targets, settings = _sync_page_context(gid)
    return render_template(
        'sync_channel_messages.html',
        page='sync_channel_messages',
        group=group,
        settings=settings,
        targets=targets,
    )


@core_bp.route('/group/<int:gid>/sync_message_logs')
def page_sync_message_logs(gid):
    """同步消息日志"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    
    page = safe_int(request.args.get('page', 1), 1)
    per_page = safe_int(request.args.get('per_page', 20), 20)
    if per_page not in [10, 20, 50, 100]: per_page = 20
    
    # Query sync logs ordered by most recent first
    query = SyncMessageLog.query.filter_by(source_group_id=gid).order_by(SyncMessageLog.synced_at.desc())
    total = query.count()
    logs = query.offset((page - 1) * per_page).limit(per_page).all()
    
    total_pages = math.ceil(total / per_page) if total > 0 else 1
    
    # 批量查询 GroupMember 以获取昵称
    log_user_ids = [l.user_id for l in logs if l.user_id]
    log_member_map = {}
    if log_user_ids:
        log_members = GroupMember.query.filter(GroupMember.user_id.in_(log_user_ids)).all()
        log_member_map = {m.user_id: m for m in log_members}
    
    from app.services.sync_log_retention import PRESETS, describe_hours, get_group_retention_hours
    retention_hours = get_group_retention_hours(gid)
    return render_template('sync_message_logs.html', page='sync_message_logs', group=group,
                         logs=logs, current_page=page, per_page=per_page,
                         total_pages=total_pages, total_items=total,
                         log_member_map=log_member_map,
                         retention_hours=retention_hours,
                         retention_label=describe_hours(retention_hours),
                         retention_presets=PRESETS)


@core_bp.route('/api/sync_log_retention', methods=['POST'])
def api_save_sync_log_retention():
    """保存同步日志保留时长（按源群/频道）。hours=0 表示永久保留。"""
    if not session.get('logged_in'): return jsonify({'status': 'error', 'msg': 'Auth required'})
    d = request.json or {}
    group = BotGroup.query.get(safe_int(d.get('group_id'), 0))
    if not group: return jsonify({'status': 'error', 'msg': 'Group not found'})
    err = _api_check_group_access(group)
    if err: return err
    from app.services.sync_log_retention import describe_hours, set_group_retention_hours
    try:
        hours = set_group_retention_hours(group.id, d.get('hours'))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'msg': '请输入有效的小时数'})
    return jsonify({'status': 'ok', 'hours': hours, 'label': describe_hours(hours)})

@core_bp.route('/bot_clones')
@require_main_instance
def page_bot_clones():
    """机器人克隆管理 - 只在主机器人后台显示"""
    if not session.get('logged_in'): return redirect('/core')
    if session.get('clone_id'): return redirect('/core/select_group')
    
    clones = BotClone.query.order_by(BotClone.created_at.desc()).all()
    
    # Convert to JSON for JavaScript
    clones_json = json.dumps([{
        'id': c.id,
        'clone_name': c.clone_name,
        'bot_token': c.get_bot_token(),
        'owner_user_id': c.owner_user_id,
        'admin_user_ids': c.admin_user_ids,
        'is_active': c.is_active,
        'expiration_date': c.expiration_date.isoformat() if c.expiration_date else None,
        'webhook_url': c.webhook_url,
        'description': c.description
    } for c in clones], ensure_ascii=False)
    
    railway_domain = os.getenv('RAILWAY_PUBLIC_DOMAIN', '').strip()
    return render_template('bot_clones.html', page='bot_clones', clones=clones, clones_json=clones_json, beijing_now=get_beijing_now(), railway_domain=railway_domain)

@core_bp.route('/group/<int:gid>/inactive_user_settings')
def page_inactive_user_settings(gid):
    """不活跃用户设置"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    settings = InactiveUserSettings.query.filter_by(group_id=gid).first()
    return render_template('inactive_user_settings.html', page='inactive_user_settings', group=group, settings=settings)

@core_bp.route('/group/<int:gid>/keyword_filter')
def page_keyword_filter(gid):
    """关键词过滤"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    filters_list = KeywordFilter.query.filter_by(group_id=gid).order_by(KeywordFilter.created_at.desc()).all()
    
    # Convert to JSON for JavaScript
    filters_json = json.dumps([{
        'id': f.id,
        'keyword': f.keyword,
        'filter_type': f.filter_type,
        'match_type': f.match_type,
        'action': f.action,
        'is_active': f.is_active
    } for f in filters_list], ensure_ascii=False)
    
    return render_template('keyword_filter.html', page='keyword_filter', group=group, 
                         filters=filters_list, filters_json=filters_json)

@core_bp.route('/group/<int:gid>/message_statistics')
def page_message_statistics(gid):
    """消息统计"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    
    # Get statistics for the last 7 days
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=7)
    
    stats = db.session.query(
        MessageStatistics.date,
        db.func.sum(MessageStatistics.message_count).label('total_messages'),
        db.func.count(db.func.distinct(MessageStatistics.user_id)).label('active_users')
    ).filter(
        MessageStatistics.group_id == gid,
        MessageStatistics.date >= start_date,
        MessageStatistics.date <= end_date
    ).group_by(MessageStatistics.date).order_by(MessageStatistics.date.desc()).all()
    
    # Get top users
    top_users = db.session.query(
        MessageStatistics.user_id,
        db.func.sum(MessageStatistics.message_count).label('total_messages')
    ).filter(
        MessageStatistics.group_id == gid,
        MessageStatistics.date >= start_date,
        MessageStatistics.date <= end_date
    ).group_by(MessageStatistics.user_id).order_by(
        db.func.sum(MessageStatistics.message_count).desc()
    ).limit(10).all()
    
    return render_template('message_statistics.html', page='message_statistics', 
                         group=group, stats=stats, top_users=top_users, 
                         start_date=start_date, end_date=end_date)

@core_bp.route('/group/<int:gid>/channel_forward_rules')
def page_channel_forward_rules(gid):
    """频道转发规则"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    rules = ChannelForwardRule.query.filter_by(group_id=gid).order_by(ChannelForwardRule.updated_at.desc(), ChannelForwardRule.id.desc()).all()
    return render_template('channel_forward_rules.html', page='channel_forward_rules', group=group, rules=rules)


@core_bp.route('/group/<int:gid>/channel_templates')
def page_channel_templates(gid):
    """频道消息模板"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    templates = ChannelMessageTemplate.query.filter_by(group_id=gid).order_by(ChannelMessageTemplate.updated_at.desc(), ChannelMessageTemplate.id.desc()).all()
    return render_template('channel_templates.html', page='channel_templates', group=group, templates=templates)


@core_bp.route('/group/<int:gid>/channel_coupons')
def page_channel_coupons(gid):
    """频道优惠券"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    coupons = ChannelCoupon.query.filter_by(group_id=gid).order_by(ChannelCoupon.sort_order.asc(), ChannelCoupon.id.desc()).all()
    return render_template('channel_coupons.html', page='channel_coupons', group=group, coupons=coupons)


@core_bp.route('/group/<int:gid>/channel_post_buttons')
def page_channel_post_buttons(gid):
    """频道帖自动加按钮（编辑原帖 reply_markup）"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    config = ChannelPostButtonConfig.query.filter_by(group_id=gid).first()
    if not config:
        config = ChannelPostButtonConfig(group_id=gid, enabled=False, links='[]')
    from app.services.channel_post_buttons import normalize_button_links
    links_list = normalize_button_links(config.get_links_list() if hasattr(config, 'get_links_list') else config.links)
    return render_template(
        'channel_post_buttons.html',
        page='channel_post_buttons',
        group=group,
        config=config,
        links_json=json.dumps(links_list, ensure_ascii=False),
    )


@core_bp.route('/group/<int:gid>/channel_stats')
def page_channel_stats(gid):
    """频道消息统计"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    stats_entries = ChannelMessageStats.query.filter_by(group_id=gid).order_by(ChannelMessageStats.created_at.desc()).limit(100).all()
    stats_summary = {
        'total_messages': ChannelMessageStats.query.filter_by(group_id=gid).count(),
        'today_messages': ChannelMessageStats.query.filter(
            ChannelMessageStats.group_id == gid,
            ChannelMessageStats.created_at >= get_beijing_today()
        ).count(),
        'forward_rules': ChannelForwardRule.query.filter_by(group_id=gid, is_active=True).count(),
        'active_templates': ChannelMessageTemplate.query.filter_by(group_id=gid, is_active=True).count(),
        'active_coupons': ChannelCoupon.query.filter_by(group_id=gid, is_active=True).count(),
    }
    return render_template('channel_stats.html', page='channel_stats', group=group, stats_entries=stats_entries, stats_summary=stats_summary)

@core_bp.route('/group/<int:gid>/group_votes')
def page_group_votes(gid):
    """群投票管理"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    votes = GroupVote.query.filter_by(group_id=gid).order_by(GroupVote.created_at.desc()).all()
    
    # Build a plain Python list so the template can use the safe `tojson` filter
    votes_data = [{
        'id': v.id,
        'title': v.title,
        'description': v.description,
        'options': json.loads(v.options),
        'vote_type': v.vote_type,
        'max_choices': v.max_choices,
        'is_anonymous': v.is_anonymous,
        'allow_revote': v.allow_revote,
        'start_time': v.start_time.isoformat() if v.start_time else None,
        'end_time': v.end_time.isoformat() if v.end_time else None,
        'status': v.status
    } for v in votes]
    
    return render_template('group_votes.html', page='group_votes', group=group, 
                         votes=votes, votes_data=votes_data)

@core_bp.route('/group/<int:gid>/quiz_games')
def page_quiz_games(gid):
    """问答游戏管理"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    quizzes = QuizGame.query.filter_by(group_id=gid).order_by(QuizGame.created_at.desc()).all()
    
    # Convert to JSON for JavaScript
    quizzes_json = json.dumps([{
        'id': q.id,
        'question': q.question,
        'answers': json.loads(q.answers),
        'correct_answer_index': q.correct_answer_index,
        'explanation': q.explanation,
        'points_reward': q.points_reward,
        'time_limit': q.time_limit,
        'difficulty': q.difficulty,
        'category': q.category,
        'is_active': q.is_active
    } for q in quizzes], ensure_ascii=False)
    
    return render_template('quiz_games.html', page='quiz_games', group=group, 
                         quizzes=quizzes, quizzes_json=quizzes_json)

@core_bp.route('/group/<int:gid>/red_packet_settings')
def page_red_packet_settings(gid):
    """红包设置"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    conf = get_group_conf(group)
    
    # Get recent red packets
    packets = RedPacket.query.filter_by(group_id=gid).order_by(
        RedPacket.created_at.desc()
    ).limit(20).all()
    
    return render_template('red_packet_settings.html', page='red_packet_settings', 
                         group=group, conf=conf, packets=packets)


@core_bp.route('/group/<int:gid>/admin_logs')
def page_admin_logs(gid):
    """管理员操作日志页面"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)

    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = 50

    # Search / filter parameters
    search_admin = request.args.get('search_admin', '').strip()
    search_target = request.args.get('search_target', '').strip()
    action_type   = request.args.get('action_type', '').strip()
    date_from     = request.args.get('date_from', '').strip()
    date_to       = request.args.get('date_to', '').strip()

    q = AdminActionLog.query.filter_by(group_id=gid)
    if search_admin:
        try:
            admin_id_int = int(search_admin)
            q = q.filter(
                or_(
                    AdminActionLog.admin_name.ilike(f'%{search_admin}%'),
                    AdminActionLog.admin_id == admin_id_int
                )
            )
        except ValueError:
            q = q.filter(AdminActionLog.admin_name.ilike(f'%{search_admin}%'))
    if search_target:
        q = q.filter(AdminActionLog.target_user_name.ilike(f'%{search_target}%'))
    if action_type:
        q = q.filter(AdminActionLog.action_type == action_type)
    if date_from:
        try:
            q = q.filter(AdminActionLog.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(AdminActionLog.created_at < datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass

    pagination = q.order_by(
        AdminActionLog.created_at.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)

    logs = pagination.items

    # Distinct action types for the filter dropdown
    action_types = [
        r[0] for r in db.session.query(AdminActionLog.action_type)
        .filter_by(group_id=gid).distinct().order_by(AdminActionLog.action_type).all()
        if r[0]
    ]

    filter_qs = urlencode({
        'search_admin': search_admin, 'search_target': search_target,
        'action_type': action_type, 'date_from': date_from, 'date_to': date_to,
    })

    return render_template('admin_logs.html', page='admin_logs',
                          group=group, logs=logs, pagination=pagination,
                          search_admin=search_admin, search_target=search_target,
                          action_type=action_type, date_from=date_from, date_to=date_to,
                          action_types=action_types, filter_qs=filter_qs)


@core_bp.route('/group/<int:gid>/plugins')
def page_plugins(gid):
    """插件管理页面：为该群组开启/关闭功能插件"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)

    from app.plugins import get_all_plugins
    from app.models import GroupPluginSettings

    all_plugins = get_all_plugins()
    # Attach current enabled state to each plugin dict
    for plugin in all_plugins:
        plugin['enabled'] = GroupPluginSettings.is_enabled(gid, plugin['name'])

    return render_template('plugins.html', page='plugins', group=group, plugins=all_plugins)


@core_bp.route('/api/toggle_plugin', methods=['POST'])
def api_toggle_plugin():
    """API: 开启或关闭某个群组的插件"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': '未登录'}), 401

    data = request.get_json(force=True) or {}
    gid = data.get('group_id')
    plugin_name = data.get('plugin_name', '').strip()
    enabled = data.get('enabled')

    if not gid or not plugin_name or enabled is None:
        return jsonify({'success': False, 'error': '缺少参数'}), 400

    group = BotGroup.query.get(gid)
    if not group:
        return jsonify({'success': False, 'error': '群组不存在'}), 404
    access_err = _api_check_group_access(group)
    if access_err:
        return jsonify({'success': False, 'error': '无权操作此群组'}), 403

    from app.plugins import is_plugin_registered
    if not is_plugin_registered(plugin_name):
        return jsonify({'success': False, 'error': f'未知插件: {plugin_name}'}), 400

    from app.models import GroupPluginSettings, SyncGroupMessages
    try:
        GroupPluginSettings.set_enabled(db.session, gid, plugin_name, bool(enabled))
        # Keep sync settings.enabled aligned with the plugins page kill-switch
        if plugin_name == 'sync':
            for row in SyncGroupMessages.query.filter_by(source_group_id=int(gid)).all():
                row.enabled = bool(enabled)
            db.session.commit()
        return jsonify({'success': True, 'enabled': bool(enabled)})
    except Exception as exc:
        db.session.rollback()
        # Log the full error internally; return a safe generic message to the client.
        print(f"api_toggle_plugin error (group={gid}, plugin={plugin_name}): {exc}")
        return jsonify({'success': False, 'error': '保存失败，请稍后重试'}), 500


@core_bp.route('/group/<int:gid>/backup')
def page_backup(gid):
    """配置备份页面"""
    if not session.get('logged_in'): return redirect('/core')
    session['current_group_id'] = gid
    group = get_group_or_403(gid)
    return render_template('backup.html', page='backup', group=group)


@core_bp.route('/group/<int:gid>/backup/export')
def api_export_config(gid):
    """导出群组配置为 JSON"""
    if not session.get('logged_in'): 
        return jsonify({'error': 'Not authenticated'}), 401
    
    group = get_group_or_403(gid)
    
    # Build configuration object
    config = {
        'group_info': {
            'title': group.title,
            'chat_id': group.chat_id,
            'type': group.type
        },
        'config': json.loads(group.config) if group.config else {},
        'fields_config': json.loads(group.fields_config) if group.fields_config else [],
        'auto_replies': [
            {
                'trigger_keyword': ar.trigger_keyword,
                'media_type': ar.media_type,
                'media_url': ar.media_url,
                'content': ar.content,
                'links': json.loads(ar.links) if ar.links else [],
                'delete_after': ar.delete_after,
                'remark': ar.remark,
                'is_active': ar.is_active
            }
            for ar in AutoReply.query.filter_by(group_id=gid).all()
        ],
        'scheduled_messages': [
            {
                'media_type': sm.media_type,
                'media_url': sm.media_url,
                'media_urls': json.loads(sm.media_urls) if sm.media_urls else [],
                'content': sm.content,
                'links': json.loads(sm.links) if sm.links else [],
                'repeat_interval': sm.repeat_interval,
                'delete_previous': sm.delete_previous,
                'start_time': sm.start_time.isoformat() if sm.start_time else None,
                'stop_time': sm.stop_time.isoformat() if sm.stop_time else None,
                'remark': sm.remark,
                'auto_pin': getattr(sm, 'auto_pin', False),
                'is_active': sm.is_active
            }
            for sm in ScheduledMessage.query.filter_by(group_id=gid).all()
        ],
        'points_rules': [
            {
                'rule_name': pr.rule_name,
                'rule_type': pr.rule_type,
                'points_amount': pr.points_amount,
                'is_active': pr.is_active
            }
            for pr in PointsRule.query.filter_by(group_id=gid).all()
        ],
        'member_levels': [
            {
                'level_name': ml.level_name,
                'required_points': ml.required_points,
                'permissions': json.loads(ml.permissions) if ml.permissions else {},
                'badge_emoji': ml.badge_emoji
            }
            for ml in MemberLevel.query.filter_by(group_id=gid).all()
        ],
        'export_time': datetime.now().isoformat()
    }
    
    return jsonify(config)


@core_bp.route('/group/<int:gid>/backup/import', methods=['POST'])
def api_import_config(gid):
    """导入群组配置"""
    if not session.get('logged_in'): 
        return jsonify({'error': 'Not authenticated'}), 401
    
    group = get_group_or_403(gid)
    
    try:
        config = request.get_json()
        
        # Validate config structure
        if not isinstance(config, dict):
            return jsonify({'error': '配置格式错误'}), 400
        
        # Update group config
        if 'config' in config and isinstance(config['config'], dict):
            group.config = json.dumps(config['config'], ensure_ascii=False)
        
        if 'fields_config' in config and isinstance(config['fields_config'], list):
            group.fields_config = json.dumps(config['fields_config'], ensure_ascii=False)
        
        # Import auto_replies (optional: user can choose to import or not)
        if 'auto_replies' in config and isinstance(config['auto_replies'], list):
            for ar_data in config['auto_replies']:
                if not isinstance(ar_data, dict):
                    continue
                # Check if auto reply with same keyword exists
                existing = AutoReply.query.filter_by(
                    group_id=gid,
                    trigger_keyword=ar_data.get('trigger_keyword', '')
                ).first()
                if not existing:
                    ar = AutoReply(
                        group_id=gid,
                        trigger_keyword=ar_data.get('trigger_keyword', ''),
                        media_type=ar_data.get('media_type', 'text'),
                        media_url=ar_data.get('media_url'),
                        content=ar_data.get('content'),
                        links=json.dumps(ar_data.get('links', []), ensure_ascii=False),
                        delete_after=ar_data.get('delete_after', 0),
                        remark=ar_data.get('remark'),
                        is_active=ar_data.get('is_active', True)
                    )
                    db.session.add(ar)
        
        # Import scheduled_messages (optional)
        if 'scheduled_messages' in config and isinstance(config['scheduled_messages'], list):
            for sm_data in config['scheduled_messages']:
                if not isinstance(sm_data, dict):
                    continue
                media_urls_val = sm_data.get('media_urls')
                if isinstance(media_urls_val, list):
                    media_urls_json = json.dumps(media_urls_val, ensure_ascii=False)
                elif isinstance(media_urls_val, str) and media_urls_val.strip():
                    media_urls_json = media_urls_val
                elif sm_data.get('media_url'):
                    media_urls_json = json.dumps([sm_data.get('media_url')])
                else:
                    media_urls_json = '[]'
                sm = ScheduledMessage(
                    group_id=gid,
                    media_type=sm_data.get('media_type', 'text'),
                    media_url=sm_data.get('media_url'),
                    media_urls=media_urls_json,
                    content=sm_data.get('content'),
                    links=json.dumps(sm_data.get('links', []), ensure_ascii=False),
                    repeat_interval=sm_data.get('repeat_interval', 0),
                    delete_previous=sm_data.get('delete_previous', False),
                    start_time=datetime.fromisoformat(sm_data['start_time']) if sm_data.get('start_time') else None,
                    stop_time=datetime.fromisoformat(sm_data['stop_time']) if sm_data.get('stop_time') else None,
                    remark=sm_data.get('remark'),
                    auto_pin=sm_data.get('auto_pin', False),
                    is_active=sm_data.get('is_active', True)
                )
                # Compute initial next_send_at aligned to start_time cadence
                if sm.start_time and sm.is_active:
                    now = get_beijing_now()
                    if sm.start_time > now:
                        sm.next_send_at = sm.start_time
                    elif sm.repeat_interval > 0:
                        sm.next_send_at = compute_aligned_next_send_at(
                            sm.start_time, sm.repeat_interval, now
                        )
                db.session.add(sm)
        
        # Import points_rules (optional)
        if 'points_rules' in config and isinstance(config['points_rules'], list):
            for pr_data in config['points_rules']:
                if not isinstance(pr_data, dict):
                    continue
                # Check if rule with same name exists
                existing = PointsRule.query.filter_by(
                    group_id=gid,
                    rule_name=pr_data.get('rule_name', '')
                ).first()
                if not existing:
                    pr = PointsRule(
                        group_id=gid,
                        rule_name=pr_data.get('rule_name', ''),
                        rule_type=pr_data.get('rule_type', 'custom'),
                        points_amount=pr_data.get('points_amount', 1),
                        is_active=pr_data.get('is_active', True)
                    )
                    db.session.add(pr)
        
        # Import member_levels (optional)
        if 'member_levels' in config and isinstance(config['member_levels'], list):
            for ml_data in config['member_levels']:
                if not isinstance(ml_data, dict):
                    continue
                # Check if level with same name exists
                existing = MemberLevel.query.filter_by(
                    group_id=gid,
                    level_name=ml_data.get('level_name', '')
                ).first()
                if not existing:
                    ml = MemberLevel(
                        group_id=gid,
                        level_name=ml_data.get('level_name', ''),
                        required_points=ml_data.get('required_points', 0),
                        permissions=json.dumps(ml_data.get('permissions', {}), ensure_ascii=False),
                        badge_emoji=ml_data.get('badge_emoji')
                    )
                    db.session.add(ml)
        
        db.session.commit()
        
        return jsonify({'success': True, 'message': '配置导入成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@core_bp.route('/health')
def health_check():
    """健康检查端点 - 需要认证或仅返回基本信息"""
    # Check if authenticated for detailed information
    is_authenticated = session.get('logged_in', False)
    
    try:
        # Check database connection
        db.session.execute(db.text('SELECT 1'))
        db_status = 'ok'
    except Exception as e:
        db_status = 'error'
    
    # Check bot status
    bot_status = 'running' if global_ptb_app else 'not_started'
    
    # Basic health status (always available)
    result = {
        'status': 'healthy' if db_status == 'ok' and bot_status == 'running' else 'degraded',
        'timestamp': datetime.now().isoformat()
    }
    
    # Detailed information (only when authenticated)
    if is_authenticated:
        try:
            last_stat = MessageStatistics.query.order_by(
                MessageStatistics.updated_at.desc()
            ).first()
            last_message_time = last_stat.updated_at.isoformat() if last_stat else None
        except Exception:
            last_message_time = None
        
        result.update({
            'database': db_status,
            'bot': bot_status,
            'last_message_time': last_message_time
        })
    
    return jsonify(result)


# --- API Routes ---
@core_bp.route('/api/toggle_group', methods=['POST'])
def api_toggle_group():
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d:
        return jsonify({'status':'error','msg':'Missing request body'})
    if 'id' not in d:
        return jsonify({'status':'error','msg':'Missing group ID'})
    if 'action' not in d:
        return jsonify({'status':'error','msg':'Missing action'})
    
    try:
        group_id = int(d['id'])
    except (ValueError, TypeError):
        return jsonify({'status':'error','msg':'Invalid group ID'})
    
    g = BotGroup.query.get(group_id)
    if not g: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(g)
    if err: return err
    
    if d['action'] == 'delete':
        _delete_group_cascade(g.id)
        db.session.delete(g)
    elif d['action'] == 'toggle':
        g.is_active = not g.is_active
    else:
        return jsonify({'status':'error','msg':'Invalid action'})
    
    db.session.commit()
    return jsonify({'status':'ok'})

@core_bp.route('/api/sync_group_members', methods=['POST'])
def api_sync_group_members():
    """触发从 Telegram 同步群成员"""
    if not session.get('logged_in'):
        return jsonify({'status': 'error', 'msg': 'Auth required'})
    
    group_id = request.json.get('group_id')
    if not group_id:
        return jsonify({'status': 'error', 'msg': 'Missing group_id'})
    
    group = BotGroup.query.get(group_id)
    if not group:
        return jsonify({'status': 'error', 'msg': 'Group not found'})
    
    # 触发同步 - 调用异步函数并等待结果
    try:
        if not global_ptb_app or not global_bot_loop:
            return jsonify({'status': 'error', 'msg': 'Bot not initialized'})
        
        # Submit the coroutine and wait for result (with timeout)
        future = asyncio.run_coroutine_threadsafe(
            sync_group_members_task(group),
            global_bot_loop
        )
        
        # Wait for the task to complete with a timeout (30 seconds)
        try:
            success, message, synced_count = future.result(timeout=30)
            
            if success:
                return jsonify({
                    'status': 'ok', 
                    'msg': message,
                    'synced_count': synced_count
                })
            else:
                return jsonify({'status': 'error', 'msg': message})
                
        except asyncio.TimeoutError:
            return jsonify({'status': 'error', 'msg': '同步超时，请稍后重试'})
            
    except Exception as e:
        print(f"❌ API sync_group_members error: {e}")
        traceback.print_exc()
        return jsonify({'status': 'error', 'msg': f'同步失败: {str(e)}'})

@core_bp.route('/api/save_fields', methods=['POST'])
def api_save_fields():
    if not session.get('logged_in'): return jsonify({'status':'error'})
    d = request.json
    group = BotGroup.query.get(session.get('current_group_id'))
    if not group:
        return jsonify({'status': 'error', 'msg': 'Group not found'})
    err = _api_check_group_access(group)
    if err: return err
    
    fields_data = d.get('fields', d) if isinstance(d, dict) else d
    
    group.fields_config = json.dumps(fields_data, ensure_ascii=False)
    db.session.commit()
    return jsonify({'status':'ok'})

@core_bp.route('/api/save_settings', methods=['POST'])
def api_save_settings():
    if not session.get('logged_in'): return jsonify({'status':'error'})
    d = request.json
    if not d or 'group_id' not in d:
        return jsonify({'status': 'error', 'msg': 'Missing group_id'})
    group = BotGroup.query.get(d['group_id'])
    if not group:
        return jsonify({'status': 'error', 'msg': 'Group not found'})
    err = _api_check_group_access(group)
    if err: return err
    conf = d.get('config') or {}
    if not isinstance(conf, dict):
        return jsonify({'status': 'error', 'msg': 'Invalid config'})
    # Telegram only accepts reaction-whitelist emojis; normalize so auto-like keeps working.
    raw_like = conf.get('like_emoji', DEFAULT_LIKE_EMOJI)
    if raw_like and not is_valid_like_emoji(raw_like):
        return jsonify({
            'status': 'error',
            'msg': '点赞表情无效：只能使用 Telegram 支持的回应表情（如 👍❤🔥🎉），不能用任意图标/自定义表情。',
        })
    conf['like_emoji'] = normalize_like_emoji(raw_like)
    # Nickname member tag: 0–16 chars, no emoji (setChatMemberTag)
    conf['default_member_tag'] = normalize_telegram_member_tag(
        conf.get('default_member_tag', '认证'),
        default='认证',
    )
    if 'auto_member_tag' in conf:
        conf['auto_member_tag'] = bool(conf.get('auto_member_tag'))
    d['config'] = conf
    group.config = json.dumps(conf, ensure_ascii=False)
    db.session.commit()
    return jsonify({
        'status': 'ok',
        'like_emoji': conf['like_emoji'],
        'default_member_tag': conf['default_member_tag'],
    })

@core_bp.route('/api/save_user', methods=['POST'])
def api_save_user():
    if not session.get('logged_in'): return jsonify({'status':'error'})
    d = request.json
    gid = d['group_id']
    uid_raw = d.get('tg_id')
    if uid_raw is None or str(uid_raw).strip() == '':
        return jsonify({'status':'error','msg':'No ID'})
    try:
        uid = int(str(uid_raw).strip())
    except (TypeError, ValueError):
        return jsonify({'status':'error','msg':'TG ID 必须是数字'})
    group = BotGroup.query.get(gid)
    if not group:
        return jsonify({'status': 'error', 'msg': 'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    # Edit existing user by primary key so tg_id can be changed in place
    existing_id = d.get('id')
    old_tg_id = None
    if existing_id not in (None, '', 0, '0'):
        try:
            existing_id = int(existing_id)
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'msg': '无效的用户记录 ID'})
        u = GroupUser.query.filter_by(id=existing_id, group_id=gid).first()
        if not u:
            return jsonify({'status': 'error', 'msg': 'User not found'})
        old_tg_id = u.tg_id
        if old_tg_id != uid:
            conflict = GroupUser.query.filter_by(group_id=gid, tg_id=uid).first()
            if conflict and conflict.id != u.id:
                return jsonify({'status': 'error', 'msg': f'TG ID {uid} 已被其他认证用户使用'})
            u.tg_id = uid
            # Keep points balance/logs attached to the same person after ID change
            points_row = UserPoints.query.filter_by(group_id=gid, user_id=old_tg_id).first()
            if points_row:
                target_points = UserPoints.query.filter_by(group_id=gid, user_id=uid).first()
                if target_points and target_points.id != points_row.id:
                    target_points.points_balance = (target_points.points_balance or 0) + (points_row.points_balance or 0)
                    db.session.delete(points_row)
                else:
                    points_row.user_id = uid
            PointsLog.query.filter_by(group_id=gid, user_id=old_tg_id).update(
                {PointsLog.user_id: uid}, synchronize_session=False
            )
        is_new_user = False
    else:
        u = GroupUser.query.filter_by(group_id=gid, tg_id=uid).first()
        is_new_user = u is None
        if not u:
            u = GroupUser(group_id=gid, tg_id=uid)
            db.session.add(u)
    
    u.profile_data = json.dumps(d.get('profile') or {}, ensure_ascii=False)
    # Custom member tags (JSON array). Accept list or comma-separated string from UI.
    if 'member_tags' in d:
        u.member_tags = serialize_member_tags(d.get('member_tags'))
    elif u.member_tags is None:
        u.member_tags = '[]'
    
    # Handle expiration_date - directly set from datetime picker
    # Note: datetime-local input returns browser local time, which we treat as Beijing time
    # since the UI is in Chinese and the database stores naive datetimes in Beijing time
    expiration_date_str = d.get('expiration_date')
    
    if expiration_date_str:
        try:
            # Parse datetime-local format: YYYY-MM-DDTHH:MM (treated as Beijing time)
            new_expiration = datetime.strptime(expiration_date_str, '%Y-%m-%dT%H:%M')
            u.expiration_date = new_expiration
            
            # If expiration is extended to future and user was banned, unban them
            now = get_beijing_now()
            if new_expiration > now and u.is_banned:
                u.is_banned = False
                unban_user_in_group(gid, u.tg_id)
        except ValueError as e:
            return jsonify({'status':'error','msg':f'日期格式错误: {e}'})
    else:
        # Clear expiration (set to permanent)
        u.expiration_date = None
        # If user was banned due to expiration, unban them when set to permanent
        if u.is_banned:
            u.is_banned = False
            unban_user_in_group(gid, u.tg_id)

    db.session.commit()

    # Apply Telegram nickname member tag for authenticated users (setChatMemberTag)
    try:
        group_for_tag = BotGroup.query.get(gid)
        conf_for_tag = get_group_conf(group_for_tag)
        if conf_for_tag.get('auto_member_tag', True):
            apply_telegram_member_tag_for_user(
                group_for_tag, u, conf_for_tag, token=None, force=True
            )
    except Exception as e:
        logging.warning(f'Apply member tag failed for group={gid} user={uid}: {e}')

    # 添加新认证用户时自动推送到频道
    if is_new_user:
        try:
            group = BotGroup.query.get(gid)
            conf = get_group_conf(group)
            if conf.get('auto_push_on_add') and conf.get('push_channel_id') and global_bot_loop:
                # Select the correct bot: clone bot for clone groups, main bot otherwise
                if group.clone_id is not None:
                    clone_info = bot_clone_manager.active_clones.get(group.clone_id)
                    ptb_app = clone_info.get('app') if clone_info else None
                    if not ptb_app:
                        logging.warning(f'Auto push skipped: clone bot {group.clone_id} not running for group={gid}')
                else:
                    ptb_app = global_ptb_app
                if ptb_app:
                    cid = conf['push_channel_id']
                    tpl = conf.get('push_template', '用户: {tg_id}')
                    text = tpl.replace('{tg_id}', str(u.tg_id)).replace('{onlineEmoji}', '🟢' if u.online or False else '🔴').replace('{序号}', str(u.id))
                    _member_tags_text = format_member_tags(getattr(u, 'member_tags', None))
                    text = text.replace('{成员标签}', _member_tags_text).replace('{标签}', _member_tags_text)
                    p = json.loads(u.profile_data or '{}')
                    for k, v in p.items():
                        text = text.replace(f'{{{k}}}', str(v))
                    fields = get_group_fields(group)
                    for f in fields:
                        val = p.get(f['key'], '')
                        text = text.replace(f"{{{f['label']}}}", str(val))
                    # Inject report deep-link variables using the correct bot's username
                    text = _inject_report_links(text, u.tg_id, ptb_app.bot.username)
                    text = sanitize_html_for_telegram(text)
                    asyncio.run_coroutine_threadsafe(
                        ptb_app.bot.send_message(chat_id=cid, text=text, parse_mode='HTML'),
                        global_bot_loop
                    )
        except Exception as e:
            logging.warning(f'Auto push on add failed for group={gid} user={uid}: {e}')

    return jsonify({'status':'ok'})

@core_bp.route('/api/delete_user', methods=['POST'])
def api_delete_user():
    if not session.get('logged_in'): return jsonify({'status':'error'})
    d = request.json
    if not d or 'id' not in d:
        return jsonify({'status':'error', 'msg':'Missing required parameter: id'})
    user = GroupUser.query.get(d['id'])
    if not user:
        return jsonify({'status':'error', 'msg':'User not found'})
    group = BotGroup.query.get(user.group_id)
    err = _api_check_group_access(group)
    if err: return err
    # Clear Telegram nickname tag before removing authenticated status
    try:
        clear_telegram_member_tag_for_user(group, user)
    except Exception as e:
        logging.warning(f'Clear member tag failed on delete user={user.id}: {e}')
    db.session.delete(user)
    db.session.commit()
    return jsonify({'status':'ok'})

@core_bp.route('/api/get_user_info')
def api_get_user_info():
    """获取用户详细信息"""
    if not session.get('logged_in'):
        return jsonify({'status': 'error', 'msg': 'Auth required'})
    
    group_id = safe_int(request.args.get('group_id'), 0)
    user_id = safe_int(request.args.get('user_id'), 0)
    
    if not group_id or not user_id:
        return jsonify({'status': 'error', 'msg': 'Missing parameters'})
    
    user = GroupUser.query.filter_by(id=user_id, group_id=group_id).first()
    if not user:
        return jsonify({'status': 'error', 'msg': 'User not found'})
    
    points = UserPoints.query.filter_by(group_id=group_id, user_id=user.tg_id).first()
    
    # Parse profile data
    try:
        profile_data = json.loads(user.profile_data) if user.profile_data else {}
    except:
        profile_data = {}
    
    return jsonify({
        'status': 'ok',
        'user': {
            'tg_id': user.tg_id,
            'username': profile_data.get('username'),
            'first_name': profile_data.get('first_name') or profile_data.get('name'),
            'last_name': profile_data.get('last_name'),
            'is_banned': user.is_banned,
            'online': user.online,
            'points': points.points_balance if points else 0,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'last_activity': user.last_activity.isoformat() if user.last_activity else None,
            'expiration_date': user.expiration_date.isoformat() if user.expiration_date else None,
            'checkin_time': user.checkin_time.isoformat() if user.checkin_time else None,
            'profile_data': profile_data,
            'member_tags': parse_member_tags(getattr(user, 'member_tags', None)),
        }
    })

@core_bp.route('/api/tg-photo')
def api_tg_photo():
    """代理 Telegram 用户/群组头像。
    参数:
      id   - Telegram user_id 或 chat_id（字符串）
      type - 'user' | 'group'（默认 'user'）
    """
    if not session.get('logged_in'):
        return '', 403

    entity_id = request.args.get('id', '').strip()
    entity_type = request.args.get('type', 'user')

    if not entity_id:
        return '', 400

    cache_key = f"{entity_type}:{entity_id}"
    now = time.time()

    # Return cached bytes if still fresh
    cached = _tg_photo_cache.get(cache_key)
    if cached and (now - cached[0]) < TG_PHOTO_CACHE_TTL:
        return Response(cached[1], mimetype=cached[2])

    # Determine which bot to use
    bot = None
    if global_ptb_app:
        bot = global_ptb_app.bot

    # For group photos, prefer the clone bot that manages the group
    if entity_type == 'group' and entity_id:
        group = BotGroup.query.filter_by(chat_id=str(entity_id)).first()
        if group and group.clone_id:
            clone_info = bot_clone_manager.active_clones.get(group.clone_id)
            if clone_info:
                bot = clone_info['app'].bot

    if not bot or not global_bot_loop:
        return '', 503

    try:
        async def _fetch_photo():
            if entity_type == 'group':
                chat = await bot.get_chat(entity_id)
                if not chat.photo:
                    return None, None
                file = await bot.get_file(chat.photo.small_file_id)
            else:
                photos = await bot.get_user_profile_photos(int(entity_id), limit=1)
                if not photos or photos.total_count == 0:
                    return None, None
                file = await bot.get_file(photos.photos[0][-1].file_id)
            # Download raw bytes
            bio = BytesIO()
            await file.download_to_memory(bio)
            bio.seek(0)
            return bio.read(), 'image/jpeg'

        future = asyncio.run_coroutine_threadsafe(_fetch_photo(), global_bot_loop)
        img_bytes, mime = future.result(timeout=10)

        if not img_bytes:
            return '', 404

        _tg_photo_cache[cache_key] = (now, img_bytes, mime)
        return Response(img_bytes, mimetype=mime)

    except Exception as e:
        logging.warning(f"tg-photo fetch failed for {entity_type}:{entity_id}: {e}")
        return '', 404


@core_bp.route('/api/bulk_import_users', methods=['POST'])
def api_bulk_import_users():
    """Bulk import users from XLSX data"""
    if not session.get('logged_in'): return jsonify({'status':'error', 'msg': 'Not logged in'})
    
    d = request.json
    group_id = d.get('group_id')
    
    # Check if it's XLSX import (base64 encoded) or old text format
    xlsx_content = d.get('xlsx_content')
    users_data = d.get('users', [])
    add_days = safe_int(d.get('add_days', 0), 0)
    
    # Validate add_days parameter (reasonable bounds: 0-3650 days / 10 years)
    if add_days < 0 or add_days > 3650:
        return jsonify({'status':'error', 'msg':'有效天数必须在 0-3650 之间'})
    
    if not group_id:
        return jsonify({'status':'error', 'msg':'缺少必要参数'})
    
    group = BotGroup.query.get(group_id)
    if not group:
        return jsonify({'status':'error', 'msg':'群组不存在'})
    
    fields = get_group_fields(group)
    
    # Parse XLSX if provided
    if xlsx_content:
        try:
            import base64
            xlsx_bytes = base64.b64decode(xlsx_content)
            wb = load_workbook(BytesIO(xlsx_bytes))
            ws = wb.active
            
            users_data = []
            # Skip header row
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not row or not row[0]:
                    continue
                
                tg_id = str(row[0]).strip()
                if not tg_id or not tg_id.isdigit():
                    continue
                
                profile = {}
                for idx, field in enumerate(fields):
                    col_idx = idx + 1
                    if col_idx < len(row):
                        value = row[col_idx]
                        profile[field['key']] = str(value) if value else ''
                # Optional tags column right after profile fields (export header: 成员标签; older exports: 标签)
                tags_col = len(fields) + 1
                member_tags = ''
                if tags_col < len(row) and row[tags_col] is not None:
                    member_tags = str(row[tags_col]).strip()
                    # Skip if this cell looks like a status column from older exports
                    if member_tags in ('封禁', '正常', '永久', '是', '否'):
                        member_tags = ''
                
                users_data.append({
                    'tg_id': tg_id,
                    'profile': profile,
                    'member_tags': member_tags,
                })
        except Exception as e:
            return jsonify({'status':'error', 'msg': f'解析 XLSX 文件失败: {str(e)}'})
    
    if not users_data:
        return jsonify({'status':'error', 'msg':'没有有效的用户数据'})
    
    success_count = 0
    skipped_count = 0
    
    try:
        for user_data in users_data:
            tg_id = user_data.get('tg_id')
            profile = user_data.get('profile', {})
            member_tags = serialize_member_tags(user_data.get('member_tags'))
            
            if not tg_id:
                continue
            
            # Check if user already exists
            existing_user = GroupUser.query.filter_by(group_id=group_id, tg_id=tg_id).first()
            if existing_user:
                skipped_count += 1
                continue
            
            # Create new user
            expiration_date = None
            if add_days > 0:
                expiration_date = get_beijing_now() + timedelta(days=add_days)
            
            new_user = GroupUser(
                group_id=group_id,
                tg_id=tg_id,
                profile_data=json.dumps(profile, ensure_ascii=False),
                member_tags=member_tags,
                expiration_date=expiration_date,
                is_banned=False,
                online=False
            )
            db.session.add(new_user)
            success_count += 1
        
        db.session.commit()
        
        return jsonify({
            'status': 'ok',
            'success_count': success_count,
            'skipped_count': skipped_count
        })
    except Exception as e:
        db.session.rollback()
        print(f"Error in bulk_import_users: {e}")
        return jsonify({'status':'error', 'msg': f'导入失败: {str(e)}'})

@core_bp.route('/api/export_users', methods=['POST'])
def api_export_users():
    """Export users to XLSX format"""
    if not session.get('logged_in'): return jsonify({'status':'error', 'msg': 'Not logged in'})
    
    d = request.json
    group_id = d.get('group_id')
    
    if not group_id:
        return jsonify({'status':'error', 'msg':'缺少必要参数'})
    
    group = BotGroup.query.get(group_id)
    if not group:
        return jsonify({'status':'error', 'msg':'群组不存在'})
    
    fields = get_group_fields(group)
    users = GroupUser.query.filter_by(group_id=group_id).order_by(GroupUser.id.desc()).all()
    
    # Create XLSX workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "认证用户"
    
    # Style for header
    header_fill = PatternFill(start_color="667EEA", end_color="667EEA", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    header_alignment = Alignment(horizontal="center", vertical="center")
    
    # Header row
    header = ['TG_ID']
    for field in fields:
        header.append(field['label'])
    header.extend(['成员标签', '状态', '过期时间', '禁言'])
    
    for col_num, column_title in enumerate(header, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.value = column_title
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_alignment
    
    # Data rows
    for row_num, user in enumerate(users, 2):
        try:
            profile = json.loads(user.profile_data) if user.profile_data else {}
        except:
            profile = {}
        
        ws.cell(row=row_num, column=1, value=str(user.tg_id))
        
        for col_num, field in enumerate(fields, 2):
            value = profile.get(field['key'], '')
            ws.cell(row=row_num, column=col_num, value=str(value))
        
        # Custom member tags (comma-separated for spreadsheet editing)
        tags_col = len(fields) + 2
        tags = parse_member_tags(getattr(user, 'member_tags', None))
        ws.cell(row=row_num, column=tags_col, value=','.join(tags))
        
        # Status
        col_num = tags_col + 1
        if user.is_banned:
            status = '封禁'
        elif user.expiration_date:
            status = '正常'
        else:
            status = '永久'
        ws.cell(row=row_num, column=col_num, value=status)
        
        # Expiration date
        exp_date = user.expiration_date.strftime('%Y-%m-%d %H:%M:%S') if user.expiration_date else ''
        ws.cell(row=row_num, column=col_num + 1, value=exp_date)
        
        # Banned
        ws.cell(row=row_num, column=col_num + 2, value='是' if user.is_banned else '否')
    
    # Auto-adjust column widths
    for column in ws.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = min(max_length + 2, 50)
        ws.column_dimensions[column_letter].width = adjusted_width
    
    # Save to BytesIO
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    # Convert to base64 for JSON response
    import base64
    xlsx_base64 = base64.b64encode(output.read()).decode('utf-8')
    
    return jsonify({
        'status': 'ok',
        'xlsx_content': xlsx_base64,
        'filename': f'users_{group_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    })


@core_bp.route('/api/search_users', methods=['POST'])
def api_search_users():
    if not session.get('logged_in'): return jsonify({'status':'error'})
    d = request.json
    keyword = d.get('keyword', '').strip()
    gid = session.get('current_group_id')
    
    if not gid or not keyword:
        return jsonify({'status':'error', 'msg':'Missing parameters'})
    
    group = BotGroup.query.get(gid)
    if not group:
        return jsonify({'status':'error', 'msg':'Group not found'})
    
    fields = get_group_fields(group)
    
    # Search users by keyword in profile_data
    # Requirement: "所有的查询只显示已经今日打卡的认证用户" (ALL queries should only show users who checked in today)
    today = get_beijing_today()
    users = GroupUser.query.filter(
        GroupUser.group_id == gid,
        GroupUser.online == True,
        GroupUser.checkin_time >= today,
        GroupUser.profile_data.contains(keyword)
    ).order_by(GroupUser.id.desc()).limit(200).all()
    
    result_users = []
    for u in users:
        try:
            profile = json.loads(u.profile_data) if u.profile_data else {}
        except (ValueError, TypeError, json.JSONDecodeError):
            profile = {}
        
        result_users.append({
            'id': u.id,
            'tg_id': u.tg_id,
            'profile': profile,
            'is_banned': u.is_banned,
            'expiration_date': u.expiration_date.strftime('%Y-%m-%d %H:%M:%S') if u.expiration_date else None
        })
    
    return jsonify({
        'status': 'ok',
        'users': result_users,
        'fields': fields
    })


@core_bp.route('/api/push_user', methods=['POST'])
def api_push_user():
    if not session.get('logged_in'): return jsonify({'status':'error'})
    try:
        d = request.json
        if not d or 'id' not in d:
            return jsonify({'status':'error', 'msg':'Missing required parameter: id'})
        user = GroupUser.query.get(d['id'])
        if not user: return jsonify({'status':'error','msg':'User not found'})
        
        group = BotGroup.query.get(user.group_id)
        conf = get_group_conf(group)
        cid = conf.get('push_channel_id')
        
        if not cid: return jsonify({'status':'error','msg':'请先在功能配置中填写推送频道ID'})
        
        tpl = conf.get('push_template', '用户: {tg_id}')
        text = tpl.replace('{tg_id}', str(user.tg_id)).replace('{onlineEmoji}', '🟢' if user.online else '🔴').replace('{序号}', str(user.id))
        _member_tags_text = format_member_tags(getattr(user, 'member_tags', None))
        text = text.replace('{成员标签}', _member_tags_text).replace('{标签}', _member_tags_text)
        
        p = json.loads(user.profile_data or '{}')
        for k,v in p.items(): text = text.replace(f'{{{k}}}', str(v)) 
        
        fields = get_group_fields(group)
        for f in fields:
            val = p.get(f['key'], '')
            text = text.replace(f"{{{f['label']}}}", str(val))

        # Select the correct bot: clone bot for clone groups, main bot otherwise
        if group.clone_id is not None:
            clone_info = bot_clone_manager.active_clones.get(group.clone_id)
            ptb_app = clone_info.get('app') if clone_info else None
            if not ptb_app:
                return jsonify({'status': 'error', 'msg': '克隆机器人未运行'})
        else:
            if not global_ptb_app:
                return jsonify({'status': 'error', 'msg': 'Bot not initialized'})
            ptb_app = global_ptb_app

        # Inject report deep-link variables using the correct bot's username (before sanitization)
        text = _inject_report_links(text, user.tg_id, ptb_app.bot.username)

        # Sanitize HTML before sending to Telegram
        text = sanitize_html_for_telegram(text)

        asyncio.run_coroutine_threadsafe(
            ptb_app.bot.send_message(chat_id=cid, text=text, parse_mode='HTML'),
            global_bot_loop
        )
        return jsonify({'status':'ok'})
    except Exception as e:
        return jsonify({'status':'error','msg':str(e)})

# --- Auto Reply API Routes ---
@core_bp.route('/api/save_auto_reply', methods=['POST'])
def api_save_auto_reply():
    """保存自动回复规则"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d: return jsonify({'status':'error','msg':'Missing request body'})
    
    group_id = d.get('group_id')
    if not group_id: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(group_id)
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err
    
    trigger_keyword = d.get('trigger_keyword', '').strip()
    if not trigger_keyword: return jsonify({'status':'error','msg':'触发关键词不能为空'})
    
    # 检查是否包含 /start 关键词 - /start 应使用专门的 /start 消息功能
    # 只阻止精确的 /start 命令，不影响其他包含 start 的关键词
    keywords = [k.strip() for k in trigger_keyword.split(',') if k.strip()]
    if '/start' in keywords:
        return jsonify({'status':'error','msg':'/start 命令请使用「/start 消息」功能配置，不能在自动回复中设置'})
    
    try:
        item_id = d.get('id')
        if item_id:
            # 编辑现有规则
            item = AutoReply.query.get(item_id)
            if not item: return jsonify({'status':'error','msg':'Rule not found'})
            if item.group_id != group_id: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            # 新增规则
            item = AutoReply(group_id=group_id)
            db.session.add(item)
        
        item.trigger_keyword = trigger_keyword
        item.media_type = d.get('media_type', 'text')
        item.media_url = d.get('media_url', '').strip() or None
        # 支持多个多媒体链接
        media_urls_raw = d.get('media_urls', [])
        if isinstance(media_urls_raw, list):
            if item.media_type == 'media':
                # 混合媒体类型：存储为对象数组
                media_urls_clean = []
                for u in media_urls_raw:
                    if isinstance(u, dict):
                        url = (u.get('url') or '').strip()
                        mtype = u.get('type', 'image')
                        if url:
                            media_urls_clean.append({'url': url, 'type': mtype})
                    elif u and str(u).strip():
                        media_urls_clean.append({'url': str(u).strip(), 'type': 'image'})
                item.media_urls = json.dumps(media_urls_clean)
                if media_urls_clean:
                    item.media_url = media_urls_clean[0].get('url') if isinstance(media_urls_clean[0], dict) else media_urls_clean[0]
            else:
                # 单一类型：存储为字符串数组
                media_urls_clean = [u.strip() for u in media_urls_raw if u and isinstance(u, str) and u.strip()]
                item.media_urls = json.dumps(media_urls_clean)
                # 同步第一个 URL 到 media_url 以兼容旧逻辑
                if media_urls_clean:
                    item.media_url = media_urls_clean[0]
        item.content = d.get('content', '').strip() or None
        item.links = d.get('links', '[]')
        item.delete_after = safe_int(d.get('delete_after'), 0)
        item.remark = d.get('remark', '').strip() or None
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/toggle_auto_reply', methods=['POST'])
def api_toggle_auto_reply():
    """切换自动回复规则状态"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        item = AutoReply.query.get(d['id'])
        if not item: return jsonify({'status':'error','msg':'Rule not found'})
        err = _api_check_group_access(BotGroup.query.get(item.group_id))
        if err: return err
        item.is_active = not item.is_active
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/delete_auto_reply', methods=['POST'])
def api_delete_auto_reply():
    """删除自动回复规则"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        item = AutoReply.query.get(d['id'])
        if not item: return jsonify({'status':'error','msg':'Rule not found'})
        err = _api_check_group_access(BotGroup.query.get(item.group_id))
        if err: return err
        db.session.delete(item)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/export_auto_replies/<int:group_id>', methods=['GET'])
def api_export_auto_replies(group_id):
    """导出自动回复规则为XLSX"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    group = BotGroup.query.get(group_id)
    if not group: return jsonify({'status': 'error', 'msg': 'Group not found'})
    err = _api_check_group_access(group)
    if err: return err
    
    try:
        auto_replies = AutoReply.query.filter_by(group_id=group_id).all()
        
        # Create XLSX workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "自动回复"
        
        # Style for header
        header_fill = PatternFill(start_color="667EEA", end_color="667EEA", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        # Header row
        headers = ['触发关键词', '消息类型', '多媒体链接', '消息内容', '链接按钮', '自动删除(秒)', '备注', '状态']
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
        
        # Data rows
        for row_num, ar in enumerate(auto_replies, 2):
            ws.cell(row=row_num, column=1, value=ar.trigger_keyword)
            ws.cell(row=row_num, column=2, value=ar.media_type or 'text')
            # 导出多媒体链接：多个URL用换行分隔
            media_url_list = ar.get_media_url_list()
            ws.cell(row=row_num, column=3, value='\n'.join(media_url_list) if media_url_list else '')
            ws.cell(row=row_num, column=4, value=ar.content or '')
            ws.cell(row=row_num, column=5, value=ar.links or '[]')
            ws.cell(row=row_num, column=6, value=ar.delete_after or 0)
            ws.cell(row=row_num, column=7, value=ar.remark or '')
            ws.cell(row=row_num, column=8, value='启用' if ar.is_active else '暂停')
        
        # Auto-adjust column widths
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        # Save to BytesIO
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        # Convert to base64 for JSON response
        import base64
        xlsx_base64 = base64.b64encode(output.read()).decode('utf-8')
        
        return jsonify({
            'status': 'ok',
            'xlsx_content': xlsx_base64,
            'filename': f'auto_replies_{group_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx',
            'count': len(auto_replies)
        })
    except Exception as e:
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/import_auto_replies', methods=['POST'])
def api_import_auto_replies():
    """导入自动回复规则从XLSX"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    
    try:
        d = request.json
        if not d or 'group_id' not in d:
            return jsonify({'status':'error','msg':'Missing required fields'})
        
        group_id = d['group_id']
        xlsx_content = d.get('xlsx_content')
        
        # Verify group exists
        group = BotGroup.query.get(group_id)
        if not group:
            return jsonify({'status':'error','msg':'Group not found'})
        
        if not xlsx_content:
            return jsonify({'status':'error','msg':'No data provided'})
        
        # Parse XLSX
        import base64
        xlsx_bytes = base64.b64decode(xlsx_content)
        wb = load_workbook(BytesIO(xlsx_bytes))
        ws = wb.active
        
        imported_count = 0
        skipped_count = 0
        
        # Skip header row
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[0]:
                continue
            
            try:
                # Create new auto reply
                item = AutoReply(group_id=group_id)
                item.trigger_keyword = row[0] if row[0] else ''
                item.media_type = row[1] if len(row) > 1 and row[1] else 'text'
                # 导入多媒体链接：支持换行分隔的多个URL
                raw_media = str(row[2]).strip() if len(row) > 2 and row[2] else ''
                if raw_media:
                    url_list = [u.strip() for u in raw_media.split('\n') if u.strip()]
                    item.media_urls = json.dumps(url_list)
                    item.media_url = url_list[0] if url_list else None
                else:
                    item.media_url = None
                    item.media_urls = '[]'
                item.content = row[3] if len(row) > 3 else None
                item.links = row[4] if len(row) > 4 and row[4] else '[]'
                item.delete_after = int(row[5]) if len(row) > 5 and row[5] else 0
                item.remark = row[6] if len(row) > 6 else None
                item.is_active = (row[7] == '启用') if len(row) > 7 and row[7] else True
                
                db.session.add(item)
                imported_count += 1
            except Exception as e:
                logging.error(f"Error importing auto reply: {e}")
                skipped_count += 1
                continue
        
        db.session.commit()
        return jsonify({
            'status':'ok',
            'imported': imported_count,
            'skipped': skipped_count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

# --- Scheduled Message API Routes ---
@core_bp.route('/api/save_scheduled_message', methods=['POST'])
def api_save_scheduled_message():
    """保存定时消息"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d: return jsonify({'status':'error','msg':'Missing request body'})
    
    group_id = d.get('group_id')
    if not group_id: return jsonify({'status':'error','msg':'Missing group_id'})
    
    group = BotGroup.query.get(group_id)
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        item_id = d.get('id')
        if item_id:
            # 编辑现有消息
            item = ScheduledMessage.query.get(item_id)
            if not item: return jsonify({'status':'error','msg':'Message not found'})
            if item.group_id != group_id: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            # 新增消息
            item = ScheduledMessage(group_id=group_id)
            db.session.add(item)
        
        apply_scheduled_media_fields(item, d)
        item.content = d.get('content', '').strip() or None
        if not scheduled_message_has_payload(item.media_type, item.get_media_url_list(), item.content):
            return jsonify({'status':'error','msg':'请填写消息内容或至少添加一个媒体链接'})
        item.links = d.get('links', '[]')
        old_repeat_interval = item.repeat_interval
        item.repeat_interval = safe_int(d.get('repeat_interval'), 0)
        item.delete_previous = d.get('delete_previous', False)

        # 解析时间
        start_time_str = d.get('start_time')
        stop_time_str = d.get('stop_time')
        old_start_time = item.start_time
        new_start_time = datetime.fromisoformat(start_time_str) if start_time_str else None
        item.start_time = new_start_time
        item.stop_time = datetime.fromisoformat(stop_time_str) if stop_time_str else None

        # 当开始时间或重复间隔变化时，重新计算下次发送时间
        if new_start_time != old_start_time or item.repeat_interval != old_repeat_interval:
            now = get_beijing_now()
            if new_start_time:
                if new_start_time > now:
                    # 开始时间在未来，下次发送时间设为开始时间
                    item.next_send_at = new_start_time
                elif item.repeat_interval > 0:
                    # 开始时间已过且有重复间隔，从开始时间推算下一个发送时间点
                    item.next_send_at = compute_aligned_next_send_at(
                        new_start_time, item.repeat_interval, now
                    )
                else:
                    # 开始时间已过且不重复，清空下次发送时间（下次检查时立即发送）
                    item.next_send_at = None
            else:
                item.next_send_at = None
            # 重置上次发送时间，让调度器重新开始
            item.last_sent_at = None
            item.fail_count = 0

        item.remark = d.get('remark', '').strip() or None
        item.auto_pin = bool(d.get('auto_pin', False))
        item.message_thread_id = safe_int(d.get('message_thread_id'), None)
        item.target_type = 'channel' if d.get('target_type') == 'channel' else 'group'
        item.target_channel_id = (d.get('target_channel_id') or '').strip() or None
        if item.target_type != 'channel':
            item.target_channel_id = None
        elif not item.target_channel_id:
            return jsonify({'status':'error','msg':'频道ID不能为空'})
        if item.target_type == 'channel':
            item.message_thread_id = None

        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/send_scheduled_message_now/<int:message_id>', methods=['POST'])
def api_send_scheduled_message_now(message_id):
    """保存并立即发送定时消息"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d: return jsonify({'status':'error','msg':'Missing request body'})

    group_id = d.get('group_id')
    if not group_id: return jsonify({'status':'error','msg':'Missing group_id'})

    try:
        item = ScheduledMessage.query.get(message_id)
        if not item: return jsonify({'status':'error','msg':'Message not found'})
        if item.group_id != group_id: return jsonify({'status':'error','msg':'Permission denied'})
        err = _api_check_group_access(item.group)
        if err: return err

        # 更新消息内容（与保存逻辑相同）
        apply_scheduled_media_fields(item, d)
        item.content = d.get('content', '').strip() or None
        if not scheduled_message_has_payload(item.media_type, item.get_media_url_list(), item.content):
            return jsonify({'status':'error','msg':'请填写消息内容或至少添加一个媒体链接'})
        item.links = d.get('links', '[]')
        item.repeat_interval = safe_int(d.get('repeat_interval'), 0)
        item.delete_previous = d.get('delete_previous', False)

        start_time_str = d.get('start_time')
        stop_time_str = d.get('stop_time')
        item.start_time = datetime.fromisoformat(start_time_str) if start_time_str else None
        item.stop_time = datetime.fromisoformat(stop_time_str) if stop_time_str else None

        item.remark = d.get('remark', '').strip() or None
        item.auto_pin = bool(d.get('auto_pin', False))
        item.message_thread_id = safe_int(d.get('message_thread_id'), None)
        item.target_type = 'channel' if d.get('target_type') == 'channel' else 'group'
        item.target_channel_id = (d.get('target_channel_id') or '').strip() or None
        if item.target_type != 'channel':
            item.target_channel_id = None
        elif not item.target_channel_id:
            return jsonify({'status':'error','msg':'频道ID不能为空'})
        if item.target_type == 'channel':
            item.message_thread_id = None

        db.session.commit()

        # 获取群组 chat_id
        group = item.group
        if not group or not group.chat_id:
            return jsonify({'status':'error','msg':'Group not found or missing chat_id'})

        if not global_bot_loop:
            return jsonify({'status':'error','msg':'Bot not initialized'})

        # Select the correct bot: clone bot for clone groups, main bot otherwise
        group_clone_id = group.clone_id
        if group_clone_id is not None:
            clone_info = bot_clone_manager.active_clones.get(group_clone_id)
            if not clone_info:
                return jsonify({'status':'error','msg':'Clone bot not running'})
            ptb_app = clone_info['app']
        else:
            if not global_ptb_app:
                return jsonify({'status':'error','msg':'Bot not initialized'})
            ptb_app = global_ptb_app

        # 快照发送所需数据，避免异步函数中的 ORM 懒加载问题
        chat_id = item.target_channel_id if item.target_channel_id and (item.target_type == 'channel' or not item.target_type) else group.chat_id
        url_with_types = item.get_media_url_with_types()
        msg_snapshot = {
            'media_type': item.media_type,
            'media_url': item.media_url,
            'media_urls': item.get_media_url_list(),
            'media_url_types': [t for _, t in url_with_types] if url_with_types else None,
            'content': item.content,
            'links': item.links,
            'delete_previous': item.delete_previous,
            'last_message_id': item.last_message_id,
            'last_message_ids': item.last_message_ids,
            'message_thread_id': item.message_thread_id if not (item.target_type == 'channel' or (not item.target_type and item.target_channel_id)) else None,
            'auto_pin': item.auto_pin,
        }

        collected_ids = []

        async def _send_now():
            message_thread_id = msg_snapshot.get('message_thread_id')
            # 如需删除上一条消息（支持媒体组多条消息整体删除）
            if msg_snapshot['delete_previous'] and (msg_snapshot['last_message_id'] or msg_snapshot.get('last_message_ids')):
                ids_to_delete = []
                if msg_snapshot.get('last_message_ids'):
                    try:
                        ids_to_delete = json.loads(msg_snapshot['last_message_ids'])
                    except Exception:
                        pass
                if not ids_to_delete and msg_snapshot['last_message_id']:
                    ids_to_delete = [msg_snapshot['last_message_id']]
                for mid in ids_to_delete:
                    try:
                        await ptb_app.bot.delete_message(chat_id=chat_id, message_id=mid)
                    except Exception:
                        pass

            # 构建内联键盘
            buttons = []
            try:
                links = json.loads(msg_snapshot['links'] or '[]')
                buttons = build_inline_keyboard_from_links(links)
            except Exception:
                pass

            reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
            content = sanitize_html_for_telegram(msg_snapshot['content'] or '')

            sent_message = None
            media_urls = msg_snapshot.get('media_urls', [])
            media_type = msg_snapshot['media_type']

            if media_type in ('image', 'video', 'media') and media_urls:
                sent_message = await send_multi_media(
                    ptb_app.bot, chat_id, media_type, media_urls,
                    content=content, reply_markup=reply_markup,
                    message_thread_id=message_thread_id,
                    media_url_types=msg_snapshot.get('media_url_types'),
                    collected_ids=collected_ids
                )
            elif content:
                sent_message = await ptb_app.bot.send_message(
                    chat_id=chat_id,
                    text=content,
                    parse_mode='HTML',
                    reply_markup=reply_markup,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    **({'message_thread_id': message_thread_id} if message_thread_id else {})
                )
                if sent_message:
                    collected_ids.append(sent_message.message_id)
            # 自动置顶
            if sent_message and msg_snapshot.get('auto_pin'):
                try:
                    await ptb_app.bot.pin_chat_message(
                        chat_id=chat_id,
                        message_id=sent_message.message_id,
                        disable_notification=True
                    )
                except Exception as pin_err:
                    print(f"⚠️ 置顶消息失败 (send_now, chat_id={chat_id}): {pin_err}", flush=True)
            return sent_message

        future = asyncio.run_coroutine_threadsafe(_send_now(), global_bot_loop)
        try:
            sent_message = future.result(timeout=30)
        except TimeoutError:
            return jsonify({'status':'error','msg':'消息发送超时，请稍后重试'})
        except Exception as send_err:
            print(f"Error sending scheduled message now: {send_err}", flush=True)
            return jsonify({'status':'error','msg':f'消息发送失败：{send_err}'})

        # 更新发送时间和下次发送时间，防止定时任务重复发送
        if sent_message:
            now = get_beijing_now()
            item.last_sent_at = now
            item.last_message_id = sent_message.message_id
            item.last_message_ids = json.dumps(collected_ids) if collected_ids else None
            item.fail_count = 0
            # 手动发送后重新计算下次发送时间，以本次发送时刻为基准
            if item.repeat_interval > 0:
                item.next_send_at = now + timedelta(minutes=item.repeat_interval)
            else:
                item.next_send_at = None
            db.session.commit()

        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        print(f"Error in send_scheduled_message_now: {e}", flush=True)
        return jsonify({'status':'error','msg':'操作失败，请稍后重试'})

@core_bp.route('/api/send_preview_to_admin', methods=['POST'])
def api_send_preview_to_admin():
    """将消息内容以实际格式发送给当前登录管理员的私聊，用于预览效果"""
    if not session.get('logged_in'):
        return jsonify({'status': 'error', 'msg': 'Auth required'})
    d = request.json
    if not d:
        return jsonify({'status': 'error', 'msg': 'Missing request body'})

    admin_tg_id = session.get('login_user_id')
    if not admin_tg_id:
        return jsonify({'status': 'error', 'msg': '无法获取管理员 Telegram ID，请重新登录'})

    group_id = d.get('group_id')
    clone_id = session.get('clone_id')

    # If a group_id is provided, resolve clone_id from the group
    if group_id:
        group = BotGroup.query.get(group_id)
        if group:
            clone_id = group.clone_id

    # Select the correct bot
    if clone_id is not None:
        clone_info = bot_clone_manager.active_clones.get(clone_id)
        if not clone_info:
            return jsonify({'status': 'error', 'msg': '克隆机器人未运行，无法发送预览'})
        ptb_app = clone_info['app']
    else:
        if not global_ptb_app or not global_bot_loop:
            return jsonify({'status': 'error', 'msg': '机器人未启动，无法发送预览'})
        ptb_app = global_ptb_app

    if not global_bot_loop:
        return jsonify({'status': 'error', 'msg': '机器人未启动，无法发送预览'})

    msg_snapshot = {
        'media_type': d.get('media_type', 'text'),
        'media_url': (d.get('media_url') or '').strip() or None,
        'content': (d.get('content') or '').strip() or None,
        'links': d.get('links', '[]'),
    }
    # 处理媒体URL和类型
    media_type = msg_snapshot['media_type']
    media_urls_raw = d.get('media_urls') or []
    media_url_types = None
    if media_type == 'media':
        # 混合媒体：URL可能是对象数组
        clean_urls = []
        clean_types = []
        for u in media_urls_raw:
            if isinstance(u, dict):
                url = (u.get('url') or '').strip()
                mtype = u.get('type', 'image')
                if url:
                    clean_urls.append(url)
                    clean_types.append(mtype)
            elif u and str(u).strip():
                clean_urls.append(str(u).strip())
                clean_types.append('image')
        msg_snapshot['media_urls'] = clean_urls
        media_url_types = clean_types
    else:
        msg_snapshot['media_urls'] = [u.strip() for u in media_urls_raw if u and isinstance(u, str) and u.strip()]
    # 兼容：如果 media_urls 为空但 media_url 有值
    if not msg_snapshot['media_urls'] and msg_snapshot['media_url']:
        msg_snapshot['media_urls'] = [msg_snapshot['media_url']]
    chat_id = admin_tg_id

    async def _send_preview():
        buttons = []
        try:
            links = json.loads(msg_snapshot['links'] or '[]')
            buttons = build_inline_keyboard_from_links(links)
        except Exception:
            pass
        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        content = sanitize_html_for_telegram(msg_snapshot['content'] or '')

        sent_msg = None
        media_urls = msg_snapshot.get('media_urls', [])
        media_type = msg_snapshot['media_type']

        if media_type in ('image', 'video', 'media') and media_urls:
            sent_msg = await send_multi_media(
                ptb_app.bot, chat_id, media_type, media_urls,
                content=content, reply_markup=reply_markup,
                media_url_types=media_url_types
            )
        elif content:
            sent_msg = await ptb_app.bot.send_message(
                chat_id=chat_id,
                text=content,
                parse_mode='HTML',
                reply_markup=reply_markup,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        else:
            raise ValueError('消息内容为空，无法发送预览')

        if sent_msg is not None:
            async def _delete_preview_later(msg_id, delay):
                await asyncio.sleep(delay)
                try:
                    await ptb_app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception:
                    pass
            _task = asyncio.create_task(_delete_preview_later(sent_msg.message_id, 60))
            _background_tasks.add(_task)
            _task.add_done_callback(_background_tasks.discard)

    future = asyncio.run_coroutine_threadsafe(_send_preview(), global_bot_loop)
    try:
        future.result(timeout=30)
    except TimeoutError:
        return jsonify({'status': 'error', 'msg': '发送超时，请稍后重试'})
    except Exception as send_err:
        err_msg = str(send_err)
        print(f"Preview send error: {send_err}", flush=True)
        if 'bot was blocked' in err_msg or 'chat not found' in err_msg or 'PEER_ID_INVALID' in err_msg:
            return jsonify({'status': 'error', 'msg': '发送失败：请先在 Telegram 中向机器人发送一条消息以开启私聊'})
        return jsonify({'status': 'error', 'msg': '发送预览失败，请检查机器人状态后重试'})

    return jsonify({'status': 'ok', 'msg': '✅ 预览消息已发送到您的私聊'})


@core_bp.route('/api/toggle_scheduled_message', methods=['POST'])
def api_toggle_scheduled_message():
    """切换定时消息状态"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        item = ScheduledMessage.query.get(d['id'])
        if not item: return jsonify({'status':'error','msg':'Message not found'})
        err = _api_check_group_access(item.group)
        if err: return err
        item.is_active = not item.is_active
        # 重新启用时重新计算下次发送时间，不立即触发发送（不算手动发送）
        if item.is_active:
            prepare_scheduled_message_enable(item, get_beijing_now())
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/delete_scheduled_message', methods=['POST'])
def api_delete_scheduled_message():
    """删除定时消息"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        item = ScheduledMessage.query.get(d['id'])
        if not item: return jsonify({'status':'error','msg':'Message not found'})
        err = _api_check_group_access(item.group)
        if err: return err
        db.session.delete(item)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/batch_scheduled_messages', methods=['POST'])
def api_batch_scheduled_messages():
    """批量操作定时消息（启用/暂停/删除）"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d: return jsonify({'status':'error','msg':'Missing request body'})
    ids = d.get('ids')
    action = d.get('action')
    if not ids or not isinstance(ids, list): return jsonify({'status':'error','msg':'Missing ids'})
    if action not in ('enable', 'pause', 'delete'): return jsonify({'status':'error','msg':'Invalid action'})

    try:
        items = ScheduledMessage.query.filter(ScheduledMessage.id.in_(ids)).all()
        session_clone_id = session.get('clone_id')
        processed = 0
        for item in items:
            # Global admins (no clone_id in session) may operate on any group.
            # Clone admins may only operate on groups that belong to their clone.
            if session_clone_id and item.group and item.group.clone_id != session_clone_id:
                continue
            if action == 'delete':
                db.session.delete(item)
            elif action == 'enable':
                item.is_active = True
                # 重新启用时重新计算下次发送时间，不立即触发发送（不算手动发送）
                prepare_scheduled_message_enable(item, get_beijing_now())
            elif action == 'pause':
                item.is_active = False
            processed += 1
        db.session.commit()
        return jsonify({'status':'ok','count':processed})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/export_scheduled_messages/<int:group_id>', methods=['GET'])
def api_export_scheduled_messages(group_id):
    """导出定时消息为XLSX"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    group = BotGroup.query.get(group_id)
    if not group: return jsonify({'status': 'error', 'msg': 'Group not found'})
    err = _api_check_group_access(group)
    if err: return err
    
    def format_repeat_interval(minutes):
        if not minutes or minutes == 0:
            return '不重复'
        if minutes % (24 * 60) == 0:
            return f'每{minutes // (24 * 60)}天'
        elif minutes % 60 == 0:
            return f'每{minutes // 60}小时'
        else:
            return f'每{minutes}分钟'

    try:
        scheduled_messages = ScheduledMessage.query.filter_by(group_id=group_id).all()
        
        # Create XLSX workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "定时消息"
        
        # Style for header
        header_fill = PatternFill(start_color="F093FB", end_color="F093FB", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        # Header row
        headers = ['规则编号', '发送开始时间', '发送结束时间', '重复规则', '消息内容类型', '图片或视频链接', '消息内容', '附加按钮', '按钮分页', '删除上一条', '备注', '是否自动置顶', '是否启用']
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
        
        # Data rows
        for row_num, sm in enumerate(scheduled_messages, 2):
            ws.cell(row=row_num, column=1, value=sm.id)
            ws.cell(row=row_num, column=2, value=sm.start_time.strftime('%Y-%m-%d %H:%M:%S') if sm.start_time else '')
            ws.cell(row=row_num, column=3, value=sm.stop_time.strftime('%Y-%m-%d %H:%M:%S') if sm.stop_time else '')
            ws.cell(row=row_num, column=4, value=format_repeat_interval(sm.repeat_interval))
            ws.cell(row=row_num, column=5, value=sm.media_type or 'text')
            # 导出多媒体链接：多个URL用换行分隔
            media_url_list = sm.get_media_url_list()
            ws.cell(row=row_num, column=6, value='\n'.join(media_url_list) if media_url_list else '')
            ws.cell(row=row_num, column=7, value=sm.content or '')
            ws.cell(row=row_num, column=8, value=sm.links or '[]')
            ws.cell(row=row_num, column=9, value='不分页')
            ws.cell(row=row_num, column=10, value='是' if sm.delete_previous else '否')
            ws.cell(row=row_num, column=11, value=sm.remark or '')
            ws.cell(row=row_num, column=12, value='是' if getattr(sm, 'auto_pin', False) else '否')
            ws.cell(row=row_num, column=13, value='是' if sm.is_active else '否')
        
        # Auto-adjust column widths
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        # Save to BytesIO
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        # Convert to base64 for JSON response
        import base64
        xlsx_base64 = base64.b64encode(output.read()).decode('utf-8')
        
        return jsonify({
            'status': 'ok',
            'xlsx_content': xlsx_base64,
            'filename': f'scheduled_messages_{group_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx',
            'count': len(scheduled_messages)
        })
    except Exception as e:
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/import_scheduled_messages', methods=['POST'])
def api_import_scheduled_messages():
    """导入定时消息从XLSX"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})

    def parse_repeat_interval(text):
        """将重复规则文字转换为分钟数"""
        if not text or str(text).strip() in ('不重复', '0', ''):
            return 0
        text = str(text).strip()
        m = re.search(r'\d+', text)
        if not m:
            try:
                return int(text)
            except (ValueError, TypeError):
                return 0
        n = int(m.group())
        if '天' in text:
            return n * 24 * 60
        elif '小时' in text:
            return n * 60
        elif '分钟' in text:
            return n
        try:
            return int(text)
        except (ValueError, TypeError):
            return 0
    
    try:
        d = request.json
        if not d or 'group_id' not in d:
            return jsonify({'status':'error','msg':'Missing required fields'})
        
        group_id = d['group_id']
        xlsx_content = d.get('xlsx_content')
        
        # Verify group exists
        group = BotGroup.query.get(group_id)
        if not group:
            return jsonify({'status':'error','msg':'Group not found'})
        
        if not xlsx_content:
            return jsonify({'status':'error','msg':'No data provided'})
        
        # Parse XLSX
        import base64
        xlsx_bytes = base64.b64decode(xlsx_content)
        wb = load_workbook(BytesIO(xlsx_bytes))
        ws = wb.active

        # Detect format by reading header row
        # New format: 规则编号, 发送开始时间, 发送结束时间, 重复规则, 消息内容类型, 图片或视频链接, 消息内容, 附加按钮, 按钮分页, 删除上一条, 备注, 是否自动置顶, 是否启用
        # Old format: 消息类型, 多媒体链接, 消息内容, 链接按钮, 间隔(分钟), 删除上一条, 开始时间, 停止时间, 备注, 状态
        header_row = [str(c.value or '').strip() for c in ws[1]]
        is_new_format = len(header_row) >= 1 and header_row[0] in ('规则编号', 'ID', 'id')
        
        imported_count = 0
        skipped_count = 0
        
        # Skip header row
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or all(v is None for v in row):
                continue
            
            try:
                item = ScheduledMessage(group_id=group_id)

                if is_new_format:
                    # New format columns (0-indexed):
                    # 0:规则编号, 1:发送开始时间, 2:发送结束时间, 3:重复规则,
                    # 4:消息内容类型, 5:图片或视频链接, 6:消息内容, 7:附加按钮,
                    # 8:按钮分页(skip), 9:删除上一条, 10:备注, 11:是否自动置顶, 12:是否启用
                    def _parse_dt(val):
                        if not val:
                            return None
                        if isinstance(val, str):
                            try:
                                return datetime.strptime(val.strip(), '%Y-%m-%d %H:%M:%S')
                            except ValueError:
                                return None
                        return val

                    item.start_time = _parse_dt(row[1] if len(row) > 1 else None)
                    item.stop_time = _parse_dt(row[2] if len(row) > 2 else None)
                    item.repeat_interval = parse_repeat_interval(row[3] if len(row) > 3 else None)
                    item.media_type = str(row[4]).strip() if len(row) > 4 and row[4] else 'text'
                    # 导入多媒体链接：支持换行分隔的多个URL
                    raw_media = str(row[5]).strip() if len(row) > 5 and row[5] else ''
                    if raw_media:
                        url_list = [u.strip() for u in raw_media.split('\n') if u.strip()]
                        item.media_urls = json.dumps(url_list)
                        item.media_url = url_list[0] if url_list else None
                    else:
                        item.media_url = None
                        item.media_urls = '[]'
                    item.content = row[6] if len(row) > 6 else None
                    item.links = str(row[7]).strip() if len(row) > 7 and row[7] else '[]'
                    # column 8: 按钮分页 — skip
                    item.delete_previous = (str(row[9]).strip() == '是') if len(row) > 9 and row[9] else False
                    item.remark = str(row[10]).strip() if len(row) > 10 and row[10] else None
                    item.auto_pin = (str(row[11]).strip() == '是') if len(row) > 11 and row[11] else False
                    item.is_active = (str(row[12]).strip() == '是') if len(row) > 12 and row[12] else True
                else:
                    # Old format columns (0-indexed):
                    # 0:消息类型, 1:多媒体链接, 2:消息内容, 3:链接按钮, 4:间隔(分钟),
                    # 5:删除上一条, 6:开始时间, 7:停止时间, 8:备注, 9:状态
                    item.media_type = str(row[0]).strip() if row[0] else 'text'
                    # 导入多媒体链接：支持换行分隔的多个URL
                    raw_media_old = str(row[1]).strip() if len(row) > 1 and row[1] else ''
                    if raw_media_old:
                        url_list_old = [u.strip() for u in raw_media_old.split('\n') if u.strip()]
                        item.media_urls = json.dumps(url_list_old)
                        item.media_url = url_list_old[0] if url_list_old else None
                    else:
                        item.media_url = None
                        item.media_urls = '[]'
                    item.content = row[2] if len(row) > 2 else None
                    item.links = str(row[3]).strip() if len(row) > 3 and row[3] else '[]'
                    item.repeat_interval = 0
                    if len(row) > 4 and row[4]:
                        try:
                            item.repeat_interval = int(row[4])
                        except (ValueError, TypeError):
                            pass
                    item.delete_previous = (str(row[5]).strip() == '是') if len(row) > 5 and row[5] else False
                    if len(row) > 6 and row[6]:
                        try:
                            item.start_time = datetime.strptime(str(row[6]).strip(), '%Y-%m-%d %H:%M:%S') if isinstance(row[6], str) else row[6]
                        except Exception:
                            item.start_time = None
                    if len(row) > 7 and row[7]:
                        try:
                            item.stop_time = datetime.strptime(str(row[7]).strip(), '%Y-%m-%d %H:%M:%S') if isinstance(row[7], str) else row[7]
                        except Exception:
                            item.stop_time = None
                    item.remark = row[8] if len(row) > 8 else None
                    item.is_active = (str(row[9]).strip() == '启用') if len(row) > 9 and row[9] else True
                
                # Compute initial next_send_at aligned to start_time cadence
                if item.start_time and item.is_active:
                    now_import = get_beijing_now()
                    if item.start_time > now_import:
                        item.next_send_at = item.start_time
                    elif item.repeat_interval > 0:
                        item.next_send_at = compute_aligned_next_send_at(
                            item.start_time, item.repeat_interval, now_import
                        )

                db.session.add(item)
                imported_count += 1
            except Exception as e:
                logging.error(f"Error importing scheduled message: {e}")
                skipped_count += 1
                continue
        
        db.session.commit()
        return jsonify({
            'status':'ok',
            'imported': imported_count,
            'skipped': skipped_count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

# --- Start Message API Routes ---
@core_bp.route('/api/save_start_message', methods=['POST'])
def api_save_start_message():
    """保存自定义 /start 消息"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d: return jsonify({'status':'error','msg':'Missing request body'})
    
    group_id = d.get('group_id')
    if not group_id: return jsonify({'status':'error','msg':'Missing group_id'})
    
    message_type = d.get('message_type', 'user')
    if message_type not in ['user', 'admin']: return jsonify({'status':'error','msg':'Invalid message_type'})

    group = BotGroup.query.get(group_id)
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err
    
    try:
        item_id = d.get('id')
        if item_id:
            # 编辑现有消息
            item = StartMessage.query.get(item_id)
            if not item: return jsonify({'status':'error','msg':'Message not found'})
            if item.group_id != group_id: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            # 新增消息
            item = StartMessage(group_id=group_id)
            db.session.add(item)
        
        item.message_type = message_type
        item.media_type = d.get('media_type', 'text')
        item.media_url = d.get('media_url', '').strip() or None
        item.content = d.get('content', '').strip() or None
        item.links = d.get('links', '[]')
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/toggle_start_message', methods=['POST'])
def api_toggle_start_message():
    """切换自定义 /start 消息状态"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        item = StartMessage.query.get(d['id'])
        if not item: return jsonify({'status':'error','msg':'Message not found'})
        err = _api_check_group_access(BotGroup.query.get(item.group_id))
        if err: return err
        item.is_active = not item.is_active
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/delete_start_message', methods=['POST'])
def api_delete_start_message():
    """删除自定义 /start 消息"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        item = StartMessage.query.get(d['id'])
        if not item: return jsonify({'status':'error','msg':'Message not found'})
        err = _api_check_group_access(BotGroup.query.get(item.group_id))
        if err: return err
        db.session.delete(item)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/save_entry_exit_settings', methods=['POST'])
def api_save_entry_exit_settings():
    """保存进退群设置"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        settings = GroupEntryExitSettings.query.filter_by(group_id=d['group_id']).first()
        if not settings:
            settings = GroupEntryExitSettings(group_id=d['group_id'])
            db.session.add(settings)
        
        settings.entry_verification_enabled = d.get('entry_verification_enabled', False)
        verification_type = d.get('verification_type', 'question')
        if verification_type not in ('question', 'captcha', 'emoji', 'multiple_choice'):
            verification_type = 'question'
        settings.verification_type = verification_type
        settings.verification_question = d.get('verification_question')
        settings.verification_answer = d.get('verification_answer')
        verification_options = d.get('verification_options')
        if verification_options is not None:
            if isinstance(verification_options, list):
                verification_options = json.dumps(verification_options, ensure_ascii=False)
            elif isinstance(verification_options, str):
                try:
                    json.loads(verification_options)
                except (ValueError, TypeError):
                    verification_options = None
            else:
                verification_options = None
        settings.verification_options = verification_options
        settings.verification_timeout = d.get('verification_timeout', 60)
        settings.welcome_enabled = d.get('welcome_enabled', False)
        settings.welcome_message = d.get('welcome_message')
        settings.welcome_media_type = d.get('welcome_media_type', 'text')
        settings.welcome_media_url = d.get('welcome_media_url')
        settings.exit_ban_enabled = d.get('exit_ban_enabled', False)
        settings.exit_ban_duration = d.get('exit_ban_duration', 0)
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/save_spam_protection', methods=['POST'])
def api_save_spam_protection():
    """保存垃圾防护设置"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        settings = SpamProtection.query.filter_by(group_id=d['group_id']).first()
        if not settings:
            settings = SpamProtection(group_id=d['group_id'])
            db.session.add(settings)
        
        settings.enabled = d.get('enabled', False)
        settings.max_messages_per_minute = d.get('max_messages_per_minute', 10)
        settings.block_links = d.get('block_links', False)
        settings.block_forwards = d.get('block_forwards', False)
        settings.block_stickers = d.get('block_stickers', False)
        settings.punishment_type = d.get('punishment_type', 'mute')
        settings.punishment_duration = d.get('punishment_duration', 60)
        settings.whitelist_users = json.dumps(d.get('whitelist_users', []))
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/save_timed_group_control', methods=['POST'])
def api_save_timed_group_control():
    """保存定时开关群设置"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        settings = TimedGroupControl.query.filter_by(group_id=d['group_id']).first()
        if not settings:
            settings = TimedGroupControl(group_id=d['group_id'])
            db.session.add(settings)
        
        settings.enabled = d.get('enabled', False)
        # Parse time strings to time objects
        if d.get('open_time'):
            settings.open_time = datetime.strptime(d['open_time'], '%H:%M').time()
        if d.get('close_time'):
            settings.close_time = datetime.strptime(d['close_time'], '%H:%M').time()
        settings.timezone = d.get('timezone', 'Asia/Shanghai')
        settings.close_message = d.get('close_message')
        settings.open_message = d.get('open_message')
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/save_other_settings', methods=['POST'])
def api_save_other_settings():
    """保存其他设置"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        settings = OtherSettings.query.filter_by(group_id=d['group_id']).first()
        if not settings:
            settings = OtherSettings(group_id=d['group_id'])
            db.session.add(settings)
        
        settings.auto_delete_join_msg = d.get('auto_delete_join_msg', False)
        settings.auto_delete_leave_msg = d.get('auto_delete_leave_msg', False)
        settings.auto_delete_promote_msg = d.get('auto_delete_promote_msg', False)
        settings.auto_delete_pin_msg = d.get('auto_delete_pin_msg', False)
        settings.cancel_channel_pin = d.get('cancel_channel_pin', False)
        settings.auto_delete_channel_discussion_msg = d.get('auto_delete_channel_discussion_msg', False)
        settings.channel_auto_buttons = d.get('channel_auto_buttons', False)
        settings.channel_discussion_keyword_filter = (d.get('channel_discussion_keyword_filter') or '').strip() or None
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/save_invitation_activity', methods=['POST'])
def api_save_invitation_activity():
    """保存邀请活动设置"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        settings = InvitationActivity.query.filter_by(group_id=d['group_id']).first()
        if not settings:
            settings = InvitationActivity(group_id=d['group_id'])
            db.session.add(settings)
        
        settings.enabled = d.get('enabled', False)
        settings.reward_points = d.get('reward_points', 10)
        settings.minimum_invites = d.get('minimum_invites', 1)
        if d.get('activity_start'):
            try:
                # Handle datetime-local input format (YYYY-MM-DDTHH:MM)
                activity_start = d['activity_start']
                if 'Z' in activity_start:
                    activity_start = activity_start.replace('Z', '+00:00')
                settings.activity_start = datetime.fromisoformat(activity_start)
            except (ValueError, TypeError):
                pass
        if d.get('activity_end'):
            try:
                activity_end = d['activity_end']
                if 'Z' in activity_end:
                    activity_end = activity_end.replace('Z', '+00:00')
                settings.activity_end = datetime.fromisoformat(activity_end)
            except (ValueError, TypeError):
                pass
        settings.description = d.get('description')
        # Handle announce_in_group
        settings.announce_in_group = d.get('announce_in_group', False)
        settings.link_keyword = d.get('link_keyword') or None
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except (ProgrammingError, OperationalError) as e:
        db.session.rollback()
        error_msg = 'Database schema error. Please run: python migrate_database.py'
        logging.error(f"{error_msg}: {e}")
        return jsonify({'status':'error','msg':error_msg})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/save_forced_channel_subscription', methods=['POST'])
def api_save_forced_channel_subscription():
    """保存强制订阅频道设置"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        settings = ForcedChannelSubscription.query.filter_by(group_id=d['group_id']).first()
        if not settings:
            settings = ForcedChannelSubscription(group_id=d['group_id'])
            db.session.add(settings)
        
        settings.enabled = d.get('enabled', False)
        settings.channel_id = d.get('channel_id')
        settings.channel_username = d.get('channel_username')
        settings.check_interval = d.get('check_interval', 3600)
        settings.unsubscribe_action = d.get('unsubscribe_action', 'kick')
        settings.verification_message = d.get('verification_message')
        settings.reward_points = safe_int(d.get('reward_points'), 0)
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/save_points_rule', methods=['POST'])
def api_save_points_rule():
    """保存积分规则"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        if d.get('id'):
            rule = PointsRule.query.get(d['id'])
            if not rule: return jsonify({'status':'error','msg':'Rule not found'})
            if rule.group_id != d['group_id']: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            rule = PointsRule(group_id=d['group_id'])
            db.session.add(rule)
        
        rule.rule_name = d.get('rule_name', '')
        rule.rule_type = d.get('rule_type', 'message')
        rule.points_amount = d.get('points_amount', 1)
        rule.is_active = d.get('is_active', True)
        # Treat None / empty / 0 as "no daily cap" (stored as NULL).
        # Positive values set a per-user daily earning limit for this rule.
        _max_daily = d.get('max_daily_points')
        rule.max_daily_points = int(_max_daily) if _max_daily and int(_max_daily) > 0 else None
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/delete_points_rule', methods=['POST'])
def api_delete_points_rule():
    """删除积分规则"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        rule = PointsRule.query.get(d['id'])
        if not rule: return jsonify({'status':'error','msg':'Rule not found'})
        err = _api_check_group_access(BotGroup.query.get(rule.group_id))
        if err: return err
        db.session.delete(rule)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/save_points_auto_reply', methods=['POST'])
def api_save_points_auto_reply():
    """保存积分自动回复"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        if d.get('id'):
            reply = PointsAutoReply.query.get(d['id'])
            if not reply: return jsonify({'status':'error','msg':'Reply not found'})
            if reply.group_id != d['group_id']: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            reply = PointsAutoReply(group_id=d['group_id'])
            db.session.add(reply)
        
        reply.trigger_keyword = d.get('trigger_keyword', '')
        reply.points_cost = d.get('points_cost', 0)
        reply.content = d.get('content')
        reply.media_type = d.get('media_type', 'text')
        reply.media_url = d.get('media_url')
        # 支持多个多媒体链接
        media_urls_raw = d.get('media_urls', [])
        if isinstance(media_urls_raw, list):
            media_urls_clean = [u.strip() for u in media_urls_raw if u and u.strip()]
            reply.media_urls = json.dumps(media_urls_clean)
            if media_urls_clean:
                reply.media_url = media_urls_clean[0]
        reply.is_active = d.get('is_active', True)
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/delete_points_auto_reply', methods=['POST'])
def api_delete_points_auto_reply():
    """删除积分自动回复"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        reply = PointsAutoReply.query.get(d['id'])
        if not reply: return jsonify({'status':'error','msg':'Reply not found'})
        err = _api_check_group_access(BotGroup.query.get(reply.group_id))
        if err: return err
        db.session.delete(reply)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/save_points_auction', methods=['POST'])
def api_save_points_auction():
    """保存积分竞拍"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        if d.get('id'):
            auction = PointsAuction.query.get(d['id'])
            if not auction: return jsonify({'status':'error','msg':'Auction not found'})
            if auction.group_id != d['group_id']: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            auction = PointsAuction(group_id=d['group_id'])
            db.session.add(auction)
        
        auction.item_name = d.get('item_name', '')
        auction.item_description = d.get('item_description')
        auction.starting_price = d.get('starting_price', 100)
        if d.get('auction_start'):
            try:
                auction_start = d['auction_start']
                if 'Z' in auction_start:
                    auction_start = auction_start.replace('Z', '+00:00')
                auction.auction_start = datetime.fromisoformat(auction_start)
            except (ValueError, TypeError):
                pass
        if d.get('auction_end'):
            try:
                auction_end = d['auction_end']
                if 'Z' in auction_end:
                    auction_end = auction_end.replace('Z', '+00:00')
                auction.auction_end = datetime.fromisoformat(auction_end)
            except (ValueError, TypeError):
                pass
        auction.status = d.get('status', 'pending')
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/delete_points_auction', methods=['POST'])
def api_delete_points_auction():
    """删除积分竞拍"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        auction = PointsAuction.query.get(d['id'])
        if not auction: return jsonify({'status':'error','msg':'Auction not found'})
        err = _api_check_group_access(BotGroup.query.get(auction.group_id))
        if err: return err
        db.session.delete(auction)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/save_red_packet_settings', methods=['POST'])
def api_save_red_packet_settings():
    """保存红包设置"""
    if not session.get('logged_in'): return jsonify({'status': 'error', 'msg': 'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status': 'error', 'msg': 'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    err = _api_check_group_access(group)
    if err: return err

    try:
        conf = get_group_conf(group)
        conf['red_packet_enabled'] = bool(d.get('red_packet_enabled', True))
        conf['red_packet_max_count'] = max(1, int(d.get('red_packet_max_count', DEFAULT_SYSTEM['red_packet_max_count'])))
        conf['red_packet_min_total'] = max(1, int(d.get('red_packet_min_total', DEFAULT_SYSTEM['red_packet_min_total'])))
        conf['red_packet_max_total'] = max(0, int(d.get('red_packet_max_total', DEFAULT_SYSTEM['red_packet_max_total'])))
        conf['red_packet_expire_hours'] = max(1, int(d.get('red_packet_expire_hours', DEFAULT_SYSTEM['red_packet_expire_hours'])))
        group.config = json.dumps(conf, ensure_ascii=False)
        db.session.commit()
        return jsonify({'status': 'ok'})
    except Exception as e:
        db.session.rollback()
        _logger.error("save_red_packet_settings error: %s", e)
        return jsonify({'status': 'error', 'msg': '保存失败，请重试'})


@core_bp.route('/api/create_admin_red_packet', methods=['POST'])
def api_create_admin_red_packet():
    """后台管理员创建红包并发送到群组"""
    if not session.get('logged_in'):
        return jsonify({'status': 'error', 'msg': 'Auth required'})
    d = request.json
    if not d or 'group_id' not in d:
        return jsonify({'status': 'error', 'msg': 'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group:
        return jsonify({'status': 'error', 'msg': 'Group not found'})
    err = _api_check_group_access(group)
    if err:
        return err

    if not global_bot_loop:
        return jsonify({'status': 'error', 'msg': '机器人未初始化'})

    # Resolve the correct PTB app (main bot vs clone bot)
    group_clone_id = group.clone_id
    if group_clone_id is not None:
        clone_info = bot_clone_manager.active_clones.get(group_clone_id)
        if not clone_info:
            return jsonify({'status': 'error', 'msg': '克隆机器人未运行'})
        ptb_app = clone_info['app']
    else:
        if not global_ptb_app:
            return jsonify({'status': 'error', 'msg': '机器人未初始化'})
        ptb_app = global_ptb_app

    # Validate params
    try:
        total_points = int(d.get('total_points', 0))
        packet_count = int(d.get('packet_count', 0))
        expire_hours = max(1, int(d.get('expire_hours', 24)))
        packet_type = d.get('packet_type', 'random')
        message = d.get('message', '').strip() or '恭喜发财 🧧'
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'msg': '参数格式错误'})

    conf = get_group_conf(group)
    max_count = int(conf.get('red_packet_max_count', 50))
    min_total = int(conf.get('red_packet_min_total', 1))
    max_total = int(conf.get('red_packet_max_total', 0))

    if total_points < 1:
        return jsonify({'status': 'error', 'msg': '总积分必须 ≥ 1'})
    if packet_count < 1 or packet_count > max_count:
        return jsonify({'status': 'error', 'msg': f'红包数量需在 1–{max_count} 之间'})
    if total_points < packet_count:
        return jsonify({'status': 'error', 'msg': '总积分不能少于红包数量'})
    if total_points < min_total:
        return jsonify({'status': 'error', 'msg': f'总积分不能少于 {min_total}'})
    if max_total > 0 and total_points > max_total:
        return jsonify({'status': 'error', 'msg': f'总积分不能超过 {max_total}'})
    if packet_type not in ('random', 'equal'):
        packet_type = 'random'

    # Create the RedPacket record (admin-funded, creator_id=0)
    try:
        packet = RedPacket(
            group_id=group.id,
            creator_id=0,  # 0 = admin backend
            packet_type=packet_type,
            total_points=total_points,
            packet_count=packet_count,
            remaining_count=packet_count,
            remaining_points=total_points,
            message=message,
            expire_time=get_beijing_now() + timedelta(hours=expire_hours),
            status='active'
        )
        db.session.add(packet)
        db.session.commit()
        packet_id = packet.id
    except Exception as e:
        db.session.rollback()
        _logger.error("create_admin_red_packet DB error: %s", e)
        return jsonify({'status': 'error', 'msg': '创建红包失败，请重试'})

    # Build and send the Telegram message
    chat_id = group.chat_id
    type_label = '拼手气' if packet_type == 'random' else '普通（平均）'

    async def _send_red_packet():
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🧧 领取红包", callback_data=f"redpacket_claim_{packet_id}")
        ]])
        return await ptb_app.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🧧 <b>红包来啦！</b>\n\n"
                f"💬 {message}\n"
                f"💰 共 {total_points} 积分\n"
                f"🎁 {packet_count} 个{type_label}红包\n"
                f"⏰ {expire_hours} 小时内有效\n\n"
                f"👆 点击按钮领取"
            ),
            reply_markup=keyboard,
            parse_mode='HTML'
        )

    future = asyncio.run_coroutine_threadsafe(_send_red_packet(), global_bot_loop)
    try:
        sent = future.result(timeout=20)
    except TimeoutError:
        # Red packet was created but message failed to send — mark as expired
        try:
            p = RedPacket.query.get(packet_id)
            if p:
                p.status = 'expired'
                db.session.commit()
        except Exception:
            db.session.rollback()
        return jsonify({'status': 'error', 'msg': '消息发送超时，红包已取消'})
    except Exception as send_err:
        _logger.error("create_admin_red_packet send error: %s", send_err)
        try:
            p = RedPacket.query.get(packet_id)
            if p:
                p.status = 'expired'
                db.session.commit()
        except Exception:
            db.session.rollback()
        return jsonify({'status': 'error', 'msg': '消息发送失败，红包已取消，请检查机器人权限'})

    # Save message_id for potential future reference
    try:
        p = RedPacket.query.get(packet_id)
        if p:
            p.message_id = sent.message_id
            db.session.commit()
    except Exception:
        db.session.rollback()

    return jsonify({'status': 'ok', 'msg': '红包已发送到群组！'})


# --- Exchange Catalog Helpers ---

def _build_exchange_catalog_sync(group_id):
    """同步构建兑换商品目录消息内容（在数据库上下文中调用）。
    返回 (text, buttons_list)，buttons_list 是 InlineKeyboardButton 行列表。
    """
    items = PointsExchangeItem.query.filter_by(
        group_id=group_id, is_active=True
    ).order_by(PointsExchangeItem.id).all()

    if not items:
        return (
            "🏪 <b>积分兑换商城</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "🕐 暂无可兑换商品，敬请期待！"
        ), []

    lines = [
        "🏪 <b>积分兑换商城</b>",
        "━━━━━━━━━━━━━━━━",
        "",
    ]
    buttons = []
    for idx, it in enumerate(items, 1):
        stock_str = f"<b>{it.stock}</b> 件" if it.stock is not None else "<b>不限量</b>"
        block = [f"<b>{idx}. {it.item_name}</b>"]
        block.append(f"   💎 积分: <b>{it.points_cost}</b>  ·  📦 库存: {stock_str}")
        if it.item_description:
            block.append(f"   📝 {it.item_description}")
        lines.extend(block)
        lines.append("")
        buttons.append([InlineKeyboardButton(
            f"🛒 {idx}. {it.item_name}（{it.points_cost} 积分）",
            callback_data=f"exchange_redeem_{it.id}_{group_id}"
        )])

    lines.append("━━━━━━━━━━━━━━━━")
    lines.append("👇 点击下方按钮一键兑换，积分不足将提示余额。")
    return "\n".join(lines), buttons


async def _update_exchange_catalog_async(group_id, ptb_app):
    """在管理员指定的频道中发布或更新兑换商品目录消息。"""
    if not global_flask_app:
        return

    def _load_data():
        with global_flask_app.app_context():
            group = BotGroup.query.get(group_id)
            if not group:
                return None
            conf = get_group_conf(group)
            channel_id = conf.get('exchange_channel_id', '').strip()
            msg_id = conf.get('exchange_catalog_msg_id')
            text, buttons = _build_exchange_catalog_sync(group_id)
            return {'conf': conf, 'channel_id': channel_id, 'msg_id': msg_id,
                    'text': text, 'buttons': buttons}

    data = await asyncio.get_running_loop().run_in_executor(None, _load_data)
    if not data:
        return
    channel_id = data['channel_id']
    if not channel_id:
        return  # 未配置展示频道，跳过

    text = data['text']
    msg_id = data['msg_id']
    keyboard = InlineKeyboardMarkup(data['buttons']) if data['buttons'] else None
    new_msg_id = None

    try:
        if msg_id:
            try:
                await ptb_app.bot.edit_message_text(
                    chat_id=channel_id,
                    message_id=int(msg_id),
                    text=text,
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
                new_msg_id = int(msg_id)
            except Exception as edit_err:
                # 消息已删除或无法编辑，重新发布
                print(f"⚠️ 编辑兑换目录消息失败，重新发布 (group_id={group_id}): {edit_err}", flush=True)
                sent = await ptb_app.bot.send_message(
                    chat_id=channel_id, text=text,
                    parse_mode='HTML', reply_markup=keyboard
                )
                new_msg_id = sent.message_id
        else:
            sent = await ptb_app.bot.send_message(
                chat_id=channel_id, text=text,
                parse_mode='HTML', reply_markup=keyboard
            )
            new_msg_id = sent.message_id
    except Exception as e:
        print(f"❌ 更新兑换商品目录失败 (group_id={group_id}): {e}", flush=True)
        return

    # 若消息ID变化则持久化
    if new_msg_id and new_msg_id != msg_id:
        def _save_msg_id():
            with global_flask_app.app_context():
                group = BotGroup.query.get(group_id)
                if not group:
                    return
                conf = get_group_conf(group)
                conf['exchange_catalog_msg_id'] = new_msg_id
                group.config = json.dumps(conf, ensure_ascii=False)
                try:
                    db.session.commit()
                except Exception as ex:
                    db.session.rollback()
                    print(f"❌ 保存 exchange_catalog_msg_id 失败: {ex}", flush=True)
        await asyncio.get_running_loop().run_in_executor(None, _save_msg_id)


def schedule_exchange_catalog_update(group_id):
    """从 Flask 路由（同步上下文）中异步触发兑换目录更新。"""
    if not global_bot_loop or not global_flask_app:
        return

    def _pick_app():
        with global_flask_app.app_context():
            group = BotGroup.query.get(group_id)
            if not group:
                return None
            if group.clone_id is not None:
                clone_info = bot_clone_manager.active_clones.get(group.clone_id)
                return clone_info['app'] if clone_info else None
            return global_ptb_app

    ptb_app = _pick_app()
    if not ptb_app:
        print(f"⚠️ schedule_exchange_catalog_update: no bot app found for group_id={group_id}, skipping.", flush=True)
        return
    asyncio.run_coroutine_threadsafe(
        _update_exchange_catalog_async(group_id, ptb_app),
        global_bot_loop
    )


def _format_tg_user_display(tg_user) -> str:
    """返回 Telegram 用户的展示字符串：@username (ID: xxx) 或 昵称 (ID: xxx)。"""
    if tg_user.username:
        return f"@{tg_user.username} (ID: {tg_user.id})"
    name = (tg_user.first_name or '') + (' ' + tg_user.last_name if tg_user.last_name else '')
    return f"{name.strip() or '用户'} (ID: {tg_user.id})"


def _format_item_announcement_text(item):
    """格式化单个商品的上架公告文本。"""
    stock_str = f"<b>{item.stock}</b> 件" if item.stock is not None else "<b>不限量</b>"
    lines = [
        "🆕 <b>新品上架！</b>",
        "━━━━━━━━━━━━━━━━",
        "",
        f"🎁 <b>{item.item_name}</b>",
        "",
        f"💎 所需积分: <b>{item.points_cost}</b>  ·  📦 库存: {stock_str}",
    ]
    if item.item_description:
        lines.append("")
        lines.append(f"📝 {item.item_description}")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━")
    lines.append("👇 点击下方按钮立即兑换！")
    return "\n".join(lines)


async def _send_item_announcement_async(group_id, item_id, ptb_app):
    """向兑换展示频道发送单条商品上架公告，并将消息 ID 存入 announcement_msg_id。"""
    if not global_flask_app:
        return

    def _load():
        with global_flask_app.app_context():
            group = BotGroup.query.get(group_id)
            if not group:
                return None
            conf = get_group_conf(group)
            channel_id = conf.get('exchange_channel_id', '').strip()
            item = PointsExchangeItem.query.get(item_id)
            if not item or not item.is_active:
                return None
            return {'channel_id': channel_id, 'item_name': item.item_name,
                    'points_cost': item.points_cost, 'text': _format_item_announcement_text(item)}

    data = await asyncio.get_running_loop().run_in_executor(None, _load)
    if not data or not data['channel_id']:
        return

    button = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"🛍️ 兑换「{data['item_name']}」({data['points_cost']}积分)",
            callback_data=f"exchange_redeem_{item_id}_{group_id}"
        )
    ]])
    try:
        sent = await ptb_app.bot.send_message(
            chat_id=data['channel_id'],
            text=data['text'],
            parse_mode='HTML',
            reply_markup=button
        )

        def _save_msg_id():
            with global_flask_app.app_context():
                it = PointsExchangeItem.query.get(item_id)
                if it:
                    it.announcement_msg_id = sent.message_id
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
        await asyncio.get_running_loop().run_in_executor(None, _save_msg_id)
        print(f"✅ 新品公告已发送 channel={data['channel_id']} msg_id={sent.message_id}", flush=True)
    except Exception as e:
        print(f"❌ 发送新品公告失败 (group_id={group_id}, item_id={item_id}): {e}", flush=True)


async def _edit_item_announcement_async(group_id, item_id, ptb_app):
    """编辑频道中已存在的单条商品公告消息，使其与最新商品信息保持同步。"""
    if not global_flask_app:
        return

    def _load():
        with global_flask_app.app_context():
            group = BotGroup.query.get(group_id)
            if not group:
                return None
            conf = get_group_conf(group)
            channel_id = conf.get('exchange_channel_id', '').strip()
            item = PointsExchangeItem.query.get(item_id)
            if not item or not channel_id or not item.announcement_msg_id:
                return None
            return {
                'channel_id': channel_id,
                'msg_id': item.announcement_msg_id,
                'item_name': item.item_name,
                'points_cost': item.points_cost,
                'text': _format_item_announcement_text(item),
            }

    data = await asyncio.get_running_loop().run_in_executor(None, _load)
    if not data:
        return

    button = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"🛍️ 兑换「{data['item_name']}」({data['points_cost']}积分)",
            callback_data=f"exchange_redeem_{item_id}_{group_id}"
        )
    ]])
    try:
        await ptb_app.bot.edit_message_text(
            chat_id=data['channel_id'],
            message_id=int(data['msg_id']),
            text=data['text'],
            parse_mode='HTML',
            reply_markup=button
        )
        print(f"✅ 商品公告已更新 channel={data['channel_id']} msg_id={data['msg_id']}", flush=True)
    except Exception as e:
        print(f"⚠️ 更新商品公告失败 (group_id={group_id}, item_id={item_id}): {e}", flush=True)


async def _delete_item_announcement_async(channel_id, msg_id, ptb_app):
    """从展示频道删除单条商品公告消息（商品售罄时调用）。"""
    if not channel_id or not msg_id:
        return
    try:
        await ptb_app.bot.delete_message(chat_id=channel_id, message_id=int(msg_id))
        print(f"🗑️ 已删除商品公告 channel={channel_id} msg_id={msg_id}", flush=True)
    except Exception as e:
        print(f"⚠️ 删除商品公告失败 channel={channel_id} msg_id={msg_id}: {e}", flush=True)


def schedule_item_announcement_edit(group_id, item_id):
    """从 Flask 路由（同步上下文）异步触发单条商品公告的编辑更新。"""
    if not global_bot_loop or not global_flask_app:
        return

    def _pick_app():
        with global_flask_app.app_context():
            group = BotGroup.query.get(group_id)
            if not group:
                return None
            if group.clone_id is not None:
                clone_info = bot_clone_manager.active_clones.get(group.clone_id)
                return clone_info['app'] if clone_info else None
            return global_ptb_app

    ptb_app = _pick_app()
    if not ptb_app:
        return
    asyncio.run_coroutine_threadsafe(
        _edit_item_announcement_async(group_id, item_id, ptb_app),
        global_bot_loop
    )


def schedule_item_announcement(group_id, item_id):
    """从 Flask 路由（同步上下文）异步触发单条商品上架公告。"""
    if not global_bot_loop or not global_flask_app:
        return

    def _pick_app():
        with global_flask_app.app_context():
            group = BotGroup.query.get(group_id)
            if not group:
                return None
            if group.clone_id is not None:
                clone_info = bot_clone_manager.active_clones.get(group.clone_id)
                return clone_info['app'] if clone_info else None
            return global_ptb_app

    ptb_app = _pick_app()
    if not ptb_app:
        return
    asyncio.run_coroutine_threadsafe(
        _send_item_announcement_async(group_id, item_id, ptb_app),
        global_bot_loop
    )

@core_bp.route('/api/save_exchange_item', methods=['POST'])
def api_save_exchange_item():
    """保存积分兑换商品"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        is_new_item = not d.get('id')
        if not is_new_item:
            item = PointsExchangeItem.query.get(d['id'])
            if not item: return jsonify({'status':'error','msg':'Item not found'})
            if item.group_id != d['group_id']: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            item = PointsExchangeItem(group_id=d['group_id'])
            db.session.add(item)

        item.item_name = d.get('item_name', '').strip()
        item.item_description = d.get('item_description', '').strip() or None
        item.points_cost = int(d.get('points_cost', 100))
        stock = d.get('stock')
        item.stock = int(stock) if stock not in (None, '', 0) else None
        item.redemption_info = d.get('redemption_info', '').strip() or None
        item.is_active = bool(d.get('is_active', True))

        db.session.commit()
        schedule_exchange_catalog_update(d['group_id'])
        # 新上架商品自动发送单独公告；更新已有商品时同步编辑已发送的公告消息
        if is_new_item and item.is_active:
            schedule_item_announcement(d['group_id'], item.id)
        elif not is_new_item and item.announcement_msg_id:
            schedule_item_announcement_edit(d['group_id'], item.id)
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/delete_exchange_item', methods=['POST'])
def api_delete_exchange_item():
    """删除积分兑换商品"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})

    try:
        item = PointsExchangeItem.query.get(d['id'])
        if not item: return jsonify({'status':'error','msg':'Item not found'})
        err = _api_check_group_access(BotGroup.query.get(item.group_id))
        if err: return err
        group_id = item.group_id
        db.session.delete(item)
        db.session.commit()
        schedule_exchange_catalog_update(group_id)
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/save_exchange_channel', methods=['POST'])
def api_save_exchange_channel():
    """保存积分兑换展示频道配置"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    try:
        group = BotGroup.query.get(d['group_id'])
        if not group: return jsonify({'status':'error','msg':'Group not found'})
        err = _api_check_group_access(group)
        if err: return err
        conf = get_group_conf(group)
        new_channel = d.get('exchange_channel_id', '').strip()
        # 频道切换时重置旧消息ID，以便在新频道重新发布
        if new_channel != conf.get('exchange_channel_id', ''):
            conf['exchange_catalog_msg_id'] = None
        conf['exchange_channel_id'] = new_channel
        if 'exchange_command_word' in d:
            conf['exchange_command_word'] = (d['exchange_command_word'] or '兑换').strip()
        group.config = json.dumps(conf, ensure_ascii=False)
        db.session.commit()
        if new_channel:
            schedule_exchange_catalog_update(d['group_id'])
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/save_points_shop_enabled', methods=['POST'])
def api_save_points_shop_enabled():
    """保存积分商城总开关（兑换 + 竞拍）"""
    if not session.get('logged_in'): return jsonify({'status': 'error', 'msg': 'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status': 'error', 'msg': 'Missing group_id'})

    try:
        group = BotGroup.query.get(d['group_id'])
        if not group: return jsonify({'status': 'error', 'msg': 'Group not found'})
        err = _api_check_group_access(group)
        if err: return err
        conf = get_group_conf(group)
        conf['points_shop_enabled'] = bool(d.get('points_shop_enabled', True))
        group.config = json.dumps(conf, ensure_ascii=False)
        db.session.commit()
        return jsonify({'status': 'ok', 'points_shop_enabled': conf['points_shop_enabled']})
    except Exception as e:
        db.session.rollback()
        logging.error("save_points_shop_enabled error: %s", e)
        return jsonify({'status': 'error', 'msg': '保存失败，请重试'})


@core_bp.route('/api/save_auction_channel', methods=['POST'])
def api_save_auction_channel():
    """保存积分竞拍频道推送配置"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    try:
        group = BotGroup.query.get(d['group_id'])
        if not group: return jsonify({'status':'error','msg':'Group not found'})
        err = _api_check_group_access(group)
        if err: return err
        conf = get_group_conf(group)
        conf['auction_channel_id'] = d.get('auction_channel_id', '').strip()
        if 'auction_command_word' in d:
            conf['auction_command_word'] = (d['auction_command_word'] or '竞拍').strip()
        group.config = json.dumps(conf, ensure_ascii=False)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/save_group_lottery', methods=['POST'])
def api_save_group_lottery():
    """保存群抽奖"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    # Validate required fields before DB access
    lottery_name = (d.get('lottery_name') or '').strip()
    if not lottery_name:
        return jsonify({'status':'error','msg':'抽奖名称不能为空'})
    lottery_type = d.get('lottery_type', 'message_count')
    if lottery_type not in ('message_count', 'message_rank', 'random'):
        return jsonify({'status':'error','msg':'无效的抽奖类型'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        if d.get('id'):
            lottery = GroupLottery.query.get(d['id'])
            if not lottery: return jsonify({'status':'error','msg':'Lottery not found'})
            if lottery.group_id != d['group_id']: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            lottery = GroupLottery(group_id=d['group_id'])
            db.session.add(lottery)
        
        lottery.lottery_name = lottery_name
        lottery.lottery_type = lottery_type
        lottery.prize_description = d.get('prize_description') or None
        lottery.min_messages = safe_int(d.get('min_messages'), 10) or 10
        lottery.top_n_winners = safe_int(d.get('top_n_winners'), 3) or 3
        if d.get('start_time'):
            try:
                start_time = d['start_time']
                if 'Z' in start_time:
                    start_time = start_time.replace('Z', '+00:00')
                lottery.start_time = datetime.fromisoformat(start_time)
            except (ValueError, TypeError):
                pass
        else:
            lottery.start_time = None
        if d.get('end_time'):
            try:
                end_time = d['end_time']
                if 'Z' in end_time:
                    end_time = end_time.replace('Z', '+00:00')
                lottery.end_time = datetime.fromisoformat(end_time)
            except (ValueError, TypeError):
                pass
        else:
            lottery.end_time = None
        lottery.status = d.get('status', 'pending')
        lottery.announce_channel_id = (d.get('announce_channel_id') or '').strip() or None
        if not lottery.winner_ids:
            lottery.winner_ids = '[]'
        
        db.session.commit()
        return jsonify({'status':'ok', 'id': lottery.id})
    except Exception as e:
        db.session.rollback()
        logging.error(f"❌ [api_save_group_lottery] error: {e}")
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/delete_group_lottery', methods=['POST'])
def api_delete_group_lottery():
    """删除群抽奖"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        lottery = GroupLottery.query.get(d['id'])
        if not lottery: return jsonify({'status':'error','msg':'Lottery not found'})
        err = _api_check_group_access(BotGroup.query.get(lottery.group_id))
        if err: return err
        db.session.delete(lottery)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/save_member_level', methods=['POST'])
def api_save_member_level():
    """保存成员等级"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        if d.get('id'):
            level = MemberLevel.query.get(d['id'])
            if not level: return jsonify({'status':'error','msg':'Level not found'})
            if level.group_id != d['group_id']: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            level = MemberLevel(group_id=d['group_id'])
            db.session.add(level)
        
        level.level_name = d.get('level_name', '')
        level.required_points = d.get('required_points', 0)
        level.permissions = d.get('permissions', '{}')
        level.badge_emoji = d.get('badge_emoji')
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/delete_member_level', methods=['POST'])
def api_delete_member_level():
    """删除成员等级"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        level = MemberLevel.query.get(d['id'])
        if not level: return jsonify({'status':'error','msg':'Level not found'})
        err = _api_check_group_access(BotGroup.query.get(level.group_id))
        if err: return err
        db.session.delete(level)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/save_group_bottom_button', methods=['POST'])
def api_save_group_bottom_button():
    """保存群底部按钮"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        if d.get('id'):
            button = GroupBottomButton.query.get(d['id'])
            if not button: return jsonify({'status':'error','msg':'Button not found'})
            if button.group_id != d['group_id']: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            button = GroupBottomButton(group_id=d['group_id'])
            db.session.add(button)
        
        # Active UI fields
        button.button_text = d.get('button_text', '')
        button.input_field_placeholder = d.get('input_field_placeholder', '').strip() or None
        button.button_order = d.get('button_order', 0)
        button.row_position = d.get('row_position', 0)
        button.is_active = d.get('is_active', True)
        
        # Keep these fields for backward compatibility but don't expose in UI
        button.button_url = d.get('button_url')
        button.button_callback = d.get('button_callback')
        button.trigger_keyword = d.get('trigger_keyword', '').strip() or None
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/delete_group_bottom_button', methods=['POST'])
def api_delete_group_bottom_button():
    """删除群底部按钮"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        button = GroupBottomButton.query.get(d['id'])
        if not button: return jsonify({'status':'error','msg':'Button not found'})
        err = _api_check_group_access(BotGroup.query.get(button.group_id))
        if err: return err
        db.session.delete(button)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/move_group_bottom_button', methods=['POST'])
def api_move_group_bottom_button():
    """移动群底部按钮顺序"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d or 'direction' not in d: return jsonify({'status':'error','msg':'Missing parameters'})
    
    try:
        button = GroupBottomButton.query.get(d['id'])
        if not button: return jsonify({'status':'error','msg':'Button not found'})
        err = _api_check_group_access(BotGroup.query.get(button.group_id))
        if err: return err
        
        direction = d['direction']
        current_order = button.button_order
        
        # Get all buttons for the same group, ordered by button_order
        all_buttons = GroupBottomButton.query.filter_by(group_id=button.group_id).order_by(GroupBottomButton.button_order).all()
        
        # Find current position
        current_index = next((i for i, b in enumerate(all_buttons) if b.id == button.id), None)
        if current_index is None:
            return jsonify({'status':'error','msg':'Button position not found'})
        
        # Determine swap target
        if direction == 'up' and current_index > 0:
            swap_button = all_buttons[current_index - 1]
        elif direction == 'down' and current_index < len(all_buttons) - 1:
            swap_button = all_buttons[current_index + 1]
        else:
            return jsonify({'status':'error','msg':'Cannot move in that direction'})
        
        # Swap orders
        button.button_order, swap_button.button_order = swap_button.button_order, button.button_order
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/push_group_bottom_buttons', methods=['POST'])
def api_push_group_bottom_buttons():
    """推送群底部按钮到群组 - 直接显示菜单键盘给所有群成员"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})
    
    try:
        group = BotGroup.query.get(d['group_id'])
        if not group: return jsonify({'status':'error','msg':'Group not found'})
        
        # Get active buttons ordered by row_position, then button_order
        buttons = GroupBottomButton.query.filter_by(
            group_id=group.id,
            is_active=True
        ).order_by(GroupBottomButton.row_position, GroupBottomButton.button_order).all()
        
        if not buttons:
            return jsonify({'status':'error','msg':'没有可推送的按钮'})
        
        # Check if bot loop is ready
        if not global_bot_loop:
            return jsonify({'status':'error','msg':'Bot未就绪，请稍后再试'})

        # Select the correct bot: clone bot for clone groups, main bot otherwise
        if group.clone_id is not None:
            clone_info = bot_clone_manager.active_clones.get(group.clone_id)
            if not clone_info:
                return jsonify({'status':'error','msg':f'克隆机器人 {group.clone_id} 未运行'})
            ptb_app = clone_info['app']
        else:
            if not global_ptb_app:
                return jsonify({'status':'error','msg':'Bot未就绪，请稍后再试'})
            ptb_app = global_ptb_app
        
        print(f"🔄 推送菜单键盘到群组 {group.chat_id}，共 {len(buttons)} 个按钮", flush=True)
        
        # Build the reply keyboard markup from buttons
        keyboard = []
        current_row = []
        current_row_num = buttons[0].row_position
        
        # Get the input field placeholder from the first button that has one
        input_placeholder = None
        for button in buttons:
            if button.input_field_placeholder:
                input_placeholder = button.input_field_placeholder
                break
        
        for button in buttons:
            # Start a new row if row_position changes
            if button.row_position != current_row_num:
                if current_row:
                    keyboard.append(current_row)
                current_row = []
                current_row_num = button.row_position
            
            # Reply keyboard buttons don't support URLs, just text
            current_row.append(KeyboardButton(button.button_text))
        
        # Add the last row
        if current_row:
            keyboard.append(current_row)
        
        reply_markup = ReplyKeyboardMarkup(
            keyboard, 
            resize_keyboard=True,
            one_time_keyboard=False,
            input_field_placeholder=input_placeholder
        ) if keyboard else None
        
        # Send message with menu keyboard to the group
        async def _send_menu_command():
            try:
                # Send a message with the menu keyboard attached
                msg = await ptb_app.bot.send_message(
                    chat_id=group.chat_id,
                    text="📋 群组菜单已更新！\n\n👇 请使用下方按钮菜单：",
                    reply_markup=reply_markup
                )
                print(f"✅ 菜单键盘推送成功，消息ID: {msg.message_id}", flush=True)
                return True
            except Exception as e:
                import traceback
                print(f"❌ 推送菜单键盘时发生错误: {e}", flush=True)
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)), flush=True)
                return False
        
        # Run async function in bot's event loop
        try:
            future = asyncio.run_coroutine_threadsafe(_send_menu_command(), global_bot_loop)
            success = future.result(timeout=10)
            
            if success:
                return jsonify({'status':'ok','msg':'菜单已成功推送到群组，群成员现在可以看到底部按钮菜单'})
            else:
                return jsonify({'status':'error','msg':'推送失败，请检查bot权限和群组ID是否正确'})
        except asyncio.TimeoutError:
            print("❌ 推送菜单超时", flush=True)
            return jsonify({'status':'error','msg':'推送超时，请稍后再试'})
        except Exception as e:
            import traceback
            print(f"❌ 异步调用失败: {e}", flush=True)
            print(''.join(traceback.format_exception(type(e), e, e.__traceback__)), flush=True)
            return jsonify({'status':'error','msg':f'推送失败: {str(e)}'})
            
    except Exception as e:
        import traceback
        print(f"❌ API调用失败: {e}", flush=True)
        print(''.join(traceback.format_exception(type(e), e, e.__traceback__)), flush=True)
        return jsonify({'status':'error','msg':str(e)})

def _apply_sync_options_to_rows(rows, enabled, sync_media, sync_forwards, filter_keywords, include_sender_prefix=False,
                                sender_prefix_style=None, sync_own_bot_posts=None):
    """Apply shared sync toggles to every target row for a source."""
    for row in rows:
        if sync_own_bot_posts is not None:
            row.sync_own_bot_posts = sync_own_bot_posts
        row.enabled = enabled
        row.sync_media = sync_media
        row.sync_forwards = sync_forwards
        row.include_sender_prefix = include_sender_prefix
        if sender_prefix_style is not None:
            row.sender_prefix_style = sender_prefix_style
        row.filter_keywords = filter_keywords


def _fetch_and_set_target_title(group, row, chat_id=None):
    """Best-effort getChat → target_title. Returns (ok, title_or_error)."""
    from app.services.sync_service import fetch_chat_info
    token = _resolve_bot_token_for_group(group)
    info = fetch_chat_info(token, chat_id or row.target_group_id)
    if info.get('ok'):
        # Prefer canonical id from Telegram when available
        if info.get('id'):
            row.target_group_id = str(info['id'])
        row.target_title = info.get('title') or row.target_title
        return True, row.target_title
    return False, info.get('error') or 'getChat 失败'


@core_bp.route('/api/save_sync_group_messages', methods=['POST'])
def api_save_sync_group_messages():
    """Save shared sync options for all targets of a source group.

    Optional ``target_group_id`` upserts that one target (legacy single-target
    clients / tests). Multi-target UIs should use add/delete target APIs and
    call this endpoint only for enabled/media/forwards/keywords/prefix.
    """
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    enabled = bool(d.get('enabled', False))
    sync_media = bool(d.get('sync_media', True))
    sync_forwards = bool(d.get('sync_forwards', True))
    include_sender_prefix = bool(d.get('include_sender_prefix', False))
    from app.services.sync_service import normalize_prefix_style
    sender_prefix_style = (
        normalize_prefix_style(d.get('sender_prefix_style')) if 'sender_prefix_style' in d else None
    )
    filter_keywords = d.get('filter_keywords', '[]')
    if filter_keywords is None:
        filter_keywords = '[]'
    sync_own_bot_posts = bool(d['sync_own_bot_posts']) if 'sync_own_bot_posts' in d else None
    target_group_id = (d.get('target_group_id') or '').strip()

    try:
        rows = SyncGroupMessages.query.filter_by(source_group_id=group.id).all()

        if target_group_id:
            existing = SyncGroupMessages.query.filter_by(
                source_group_id=group.id, target_group_id=target_group_id
            ).first()
            if not existing:
                # Legacy single-target UI: if exactly one row, retarget it
                if len(rows) == 1 and not d.get('add_target'):
                    existing = rows[0]
                    existing.target_group_id = target_group_id
                else:
                    existing = SyncGroupMessages(
                        source_group_id=group.id,
                        target_group_id=target_group_id,
                    )
                    db.session.add(existing)
                    rows = list(rows) + [existing]
            _fetch_and_set_target_title(group, existing, target_group_id)

        if not rows and not target_group_id:
            # Allow saving "enabled" with zero targets (plugin mirror only)
            from app.models import GroupPluginSettings
            GroupPluginSettings.set_enabled(db.session, group.id, 'sync', enabled)
            return jsonify({'status': 'ok', 'targets': 0, 'msg': '已保存开关；请添加至少一个目标'})

        _apply_sync_options_to_rows(
            rows, enabled, sync_media, sync_forwards, filter_keywords, include_sender_prefix, sender_prefix_style,
            sync_own_bot_posts,
        )
        db.session.commit()
        try:
            from app.services import own_post_sync
            own_post_sync.invalidate()
        except Exception:
            pass

        from app.models import GroupPluginSettings
        GroupPluginSettings.set_enabled(db.session, group.id, 'sync', enabled)

        return jsonify({
            'status': 'ok',
            'targets': SyncGroupMessages.query.filter_by(source_group_id=group.id).count(),
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/add_sync_target', methods=['POST'])
def api_add_sync_target():
    """Add a sync target chat_id for a source group; fetches title via getChat."""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json or {}
    if 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    target_group_id = (d.get('target_group_id') or '').strip()
    if not target_group_id:
        return jsonify({'status':'error','msg':'目标群组/频道 ID 不能为空'})

    try:
        if SyncGroupMessages.query.filter_by(
            source_group_id=group.id, target_group_id=target_group_id
        ).first():
            return jsonify({'status':'error','msg':'该目标已存在'})

        # Copy shared options from an existing row if any
        sibling = SyncGroupMessages.query.filter_by(source_group_id=group.id).first()
        enabled = bool(d['enabled']) if 'enabled' in d else (bool(sibling.enabled) if sibling else False)
        sync_media = bool(d['sync_media']) if 'sync_media' in d else (bool(sibling.sync_media) if sibling else True)
        sync_forwards = bool(d['sync_forwards']) if 'sync_forwards' in d else (bool(sibling.sync_forwards) if sibling else True)
        include_sender_prefix = (
            bool(d['include_sender_prefix']) if 'include_sender_prefix' in d
            else (bool(sibling.include_sender_prefix) if sibling else False)
        )
        from app.services.sync_service import normalize_prefix_style
        sender_prefix_style = normalize_prefix_style(
            d['sender_prefix_style'] if 'sender_prefix_style' in d
            else (getattr(sibling, 'sender_prefix_style', None) if sibling else None)
        )
        filter_keywords = d.get('filter_keywords')
        if filter_keywords is None:
            filter_keywords = sibling.filter_keywords if sibling else '[]'
        sync_own_bot_posts = (
            bool(d['sync_own_bot_posts']) if 'sync_own_bot_posts' in d
            else (getattr(sibling, 'sync_own_bot_posts', True) is not False if sibling else True)
        )

        row = SyncGroupMessages(
            source_group_id=group.id,
            target_group_id=target_group_id,
            sync_own_bot_posts=sync_own_bot_posts,
            enabled=enabled,
            sync_media=sync_media,
            sync_forwards=sync_forwards,
            include_sender_prefix=include_sender_prefix,
            sender_prefix_style=sender_prefix_style,
            filter_keywords=filter_keywords or '[]',
        )
        ok, title_or_err = _fetch_and_set_target_title(group, row, target_group_id)
        # Still save even if getChat fails (bot may not be in target yet)
        if not ok:
            row.target_title = None
        db.session.add(row)
        db.session.commit()

        from app.models import GroupPluginSettings
        if enabled:
            GroupPluginSettings.set_enabled(db.session, group.id, 'sync', True)

        return jsonify({
            'status': 'ok',
            'id': row.id,
            'target_group_id': row.target_group_id,
            'target_title': row.target_title,
            'title_ok': ok,
            'title_error': None if ok else title_or_err,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/delete_sync_target', methods=['POST'])
def api_delete_sync_target():
    """Remove one sync target row by id (must belong to group_id)."""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json or {}
    gid = d.get('group_id')
    tid = d.get('id')
    if not gid or not tid:
        return jsonify({'status':'error','msg':'Missing group_id or id'})

    group = BotGroup.query.get(gid)
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        row = SyncGroupMessages.query.filter_by(id=int(tid), source_group_id=int(gid)).first()
        if not row:
            return jsonify({'status':'error','msg':'目标不存在'})
        db.session.delete(row)
        db.session.commit()

        remaining = SyncGroupMessages.query.filter_by(source_group_id=group.id).count()
        if remaining == 0:
            from app.models import GroupPluginSettings
            GroupPluginSettings.set_enabled(db.session, group.id, 'sync', False)

        return jsonify({'status': 'ok', 'targets': remaining})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/refresh_sync_target_title', methods=['POST'])
def api_refresh_sync_target_title():
    """Re-fetch target title via Telegram getChat."""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json or {}
    gid = d.get('group_id')
    tid = d.get('id')
    if not gid or not tid:
        return jsonify({'status':'error','msg':'Missing group_id or id'})

    group = BotGroup.query.get(gid)
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        row = SyncGroupMessages.query.filter_by(id=int(tid), source_group_id=int(gid)).first()
        if not row:
            return jsonify({'status':'error','msg':'目标不存在'})
        ok, title_or_err = _fetch_and_set_target_title(group, row)
        if not ok:
            return jsonify({'status':'error','msg': title_or_err})
        db.session.commit()
        return jsonify({
            'status': 'ok',
            'id': row.id,
            'target_group_id': row.target_group_id,
            'target_title': row.target_title,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/save_bot_clone', methods=['POST'])
@require_main_instance
def api_save_bot_clone():
    """保存机器人克隆 - 如果克隆正在运行且被编辑，则重启它"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d: return jsonify({'status':'error','msg':'Missing request body'})
    
    clone_name = d.get('clone_name', '').strip()
    bot_token = d.get('bot_token', '').strip()
    
    if not clone_name or not bot_token:
        return jsonify({'status':'error','msg':'克隆名称和Bot Token不能为空'})
    
    try:
        clone_id = d.get('id')
        was_running = False
        
        if clone_id:
            # Edit existing clone
            clone = BotClone.query.get(clone_id)
            if not clone: return jsonify({'status':'error','msg':'Clone not found'})
            
            # Check if clone is currently running
            was_running = bot_clone_manager.is_clone_running(clone_id)
        else:
            # Create new clone
            clone = BotClone()
            db.session.add(clone)
        
        clone.clone_name = clone_name
        clone.bot_token = encrypt_token(bot_token)
        clone.is_active = d.get('is_active', True)
        clone.description = d.get('description', '').strip() or None
        clone.webhook_url = d.get('webhook_url', '').strip() or None
        
        # Handle owner_user_id
        owner_user_id = d.get('owner_user_id', '').strip()
        if owner_user_id:
            try:
                clone.owner_user_id = int(owner_user_id)
            except (ValueError, TypeError):
                clone.owner_user_id = None
        else:
            clone.owner_user_id = None
        
        # Handle admin_user_ids - convert comma-separated string to JSON array
        admin_user_ids_str = d.get('admin_user_ids', '').strip()
        if admin_user_ids_str:
            try:
                # Split by comma and convert to integers (supports negative IDs)
                admin_ids = []
                for uid in admin_user_ids_str.split(','):
                    uid = uid.strip()
                    if uid:  # Skip empty strings
                        try:
                            admin_ids.append(int(uid))
                        except ValueError:
                            pass  # Skip invalid values
                clone.admin_user_ids = json.dumps(admin_ids)
            except (ValueError, TypeError):
                clone.admin_user_ids = '[]'
        else:
            clone.admin_user_ids = '[]'
        
        # Parse expiration date
        expiration_date_str = d.get('expiration_date')
        if expiration_date_str:
            try:
                # HTML datetime-local format: YYYY-MM-DDTHH:MM (no timezone)
                # Parse as Beijing time
                clone.expiration_date = datetime.strptime(expiration_date_str, '%Y-%m-%dT%H:%M')
            except (ValueError, TypeError):
                clone.expiration_date = None
        else:
            clone.expiration_date = None
        
        db.session.commit()
        
        # If clone was running and was edited, restart or stop it depending on new is_active state
        if was_running and clone_id and global_bot_loop and global_flask_app:
            try:
                now = get_beijing_now()
                if clone.is_active and (not clone.expiration_date or clone.expiration_date >= now):
                    clone_data = {
                        'id': clone.id,
                        'token': clone.get_bot_token(),
                        'webhook_url': clone.webhook_url
                    }
                    
                    # Restart the clone bot in the bot loop
                    future = asyncio.run_coroutine_threadsafe(
                        bot_clone_manager.restart_clone_bot(
                            clone_id=clone_data['id'],
                            bot_token=clone_data['token'],
                            webhook_url=get_clone_webhook_url(clone_data['id'], clone_data['webhook_url']),
                            flask_app=global_flask_app,
                            handlers_setup_func=setup_clone_handlers
                        ),
                        global_bot_loop
                    )
                    future.result(timeout=CLONE_RESTART_TIMEOUT)  # Wait for restart
                    logging.info(f"✅ Clone bot {clone_id} restarted after edit")
                else:
                    # is_active set to False (or expired) — stop the running clone
                    future = asyncio.run_coroutine_threadsafe(
                        bot_clone_manager.stop_clone_bot(clone_id),
                        global_bot_loop
                    )
                    future.result(timeout=CLONE_STOP_TIMEOUT)
                    logging.info(f"✅ Clone bot {clone_id} stopped after being disabled via edit")
            except Exception as e:
                logging.warning(f"⚠️ Failed to restart/stop clone bot {clone_id} after edit: {e}")
                # Don't fail the API call, just log the error
        
        # If this is an existing clone that was NOT running but is now enabled, start it
        elif clone_id and not was_running and clone.is_active and global_bot_loop and global_flask_app:
            try:
                now = get_beijing_now()
                if not clone.expiration_date or clone.expiration_date >= now:
                    clone_data = {
                        'id': clone.id,
                        'token': clone.get_bot_token(),
                        'webhook_url': clone.webhook_url
                    }
                    future = asyncio.run_coroutine_threadsafe(
                        bot_clone_manager.start_clone_bot(
                            clone_id=clone_data['id'],
                            bot_token=clone_data['token'],
                            webhook_url=get_clone_webhook_url(clone_data['id'], clone_data['webhook_url']),
                            flask_app=global_flask_app,
                            handlers_setup_func=setup_clone_handlers,
                            post_init_func=_register_bot_commands,
                        ),
                        global_bot_loop
                    )
                    success = future.result(timeout=CLONE_START_TIMEOUT)
                    if success:
                        logging.info(f"✅ Clone bot {clone_id} started after being enabled via edit")
                    else:
                        logging.warning(f"⚠️ Clone bot {clone_id} failed to start after being enabled via edit")
            except Exception as e:
                logging.warning(f"⚠️ Failed to start clone bot {clone_id} after edit: {e}")
                # Don't fail the API call, just log the error
        
        # If this is a brand-new active clone, start it immediately without waiting for a server restart
        elif not clone_id and clone.is_active and global_bot_loop and global_flask_app:
            try:
                now = get_beijing_now()
                if not clone.expiration_date or clone.expiration_date >= now:
                    clone_data = {
                        'id': clone.id,
                        'token': clone.get_bot_token(),
                        'webhook_url': clone.webhook_url
                    }
                    future = asyncio.run_coroutine_threadsafe(
                        bot_clone_manager.start_clone_bot(
                            clone_id=clone_data['id'],
                            bot_token=clone_data['token'],
                            webhook_url=get_clone_webhook_url(clone_data['id'], clone_data['webhook_url']),
                            flask_app=global_flask_app,
                            handlers_setup_func=setup_clone_handlers,
                            post_init_func=_register_bot_commands,
                        ),
                        global_bot_loop
                    )
                    success = future.result(timeout=CLONE_START_TIMEOUT)
                    if success:
                        logging.info(f"✅ New clone bot {clone.id} started immediately after creation")
                    else:
                        logging.warning(f"⚠️ New clone bot {clone.id} failed to start after creation")
            except Exception as e:
                logging.warning(f"⚠️ Failed to start new clone bot {clone.id} after creation: {e}")
                # Don't fail the API call, just log the error
        
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error saving clone bot: {e}")
        traceback.print_exc()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/delete_bot_clone', methods=['POST'])
@require_main_instance
def api_delete_bot_clone():
    """删除机器人克隆 - 如果正在运行则先停止"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        clone = BotClone.query.get(d['id'])
        if not clone: return jsonify({'status':'error','msg':'Clone not found'})
        
        clone_id = clone.id
        
        # Stop clone if running
        if bot_clone_manager.is_clone_running(clone_id) and global_bot_loop:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    bot_clone_manager.stop_clone_bot(clone_id),
                    global_bot_loop
                )
                future.result(timeout=CLONE_STOP_TIMEOUT)
                logging.info(f"✅ Stopped clone bot {clone_id} before deletion")
            except Exception as e:
                logging.warning(f"⚠️ Failed to stop clone bot {clone_id} before deletion: {e}")
                # Continue with deletion anyway
        
        db.session.delete(clone)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting clone bot: {e}")
        traceback.print_exc()
        return jsonify({'status':'error','msg':str(e)})

@core_bp.route('/api/toggle_bot_clone', methods=['POST'])
@require_main_instance
def api_toggle_bot_clone():
    """切换机器人克隆状态 - 动态启动/停止克隆机器人"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        clone = BotClone.query.get(d['id'])
        if not clone: return jsonify({'status':'error','msg':'Clone not found'})
        
        # Check if expired before enabling
        now = get_beijing_now()
        new_status = not clone.is_active
        if new_status and clone.expiration_date and clone.expiration_date < now:
            return jsonify({'status':'error','msg':'克隆机器人已过期，无法启动'})
        
        clone.is_active = new_status
        db.session.commit()
        
        # Dynamically start or stop the clone bot
        if global_bot_loop and global_flask_app:
            clone_data = {
                'id': clone.id,
                'name': clone.clone_name,
                'token': clone.get_bot_token(),
                'webhook_url': clone.webhook_url
            }
            
            # Schedule the async operation in the bot loop
            if new_status:
                # Start the clone bot
                future = asyncio.run_coroutine_threadsafe(
                    bot_clone_manager.start_clone_bot(
                        clone_id=clone_data['id'],
                        bot_token=clone_data['token'],
                        webhook_url=get_clone_webhook_url(clone_data['id'], clone_data['webhook_url']),
                        flask_app=global_flask_app,
                        handlers_setup_func=setup_clone_handlers,
                        post_init_func=_register_bot_commands,
                    ),
                    global_bot_loop
                )
                # Wait for completion (with timeout)
                success = future.result(timeout=CLONE_START_TIMEOUT)
                if not success:
                    # Revert the DB status since the bot failed to start
                    clone.is_active = False
                    db.session.commit()
                    return jsonify({'status':'error','msg':'克隆机器人启动失败'})
            else:
                # Stop the clone bot
                future = asyncio.run_coroutine_threadsafe(
                    bot_clone_manager.stop_clone_bot(clone_data['id']),
                    global_bot_loop
                )
                future.result(timeout=CLONE_STOP_TIMEOUT)
        
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error toggling clone bot: {e}")
        traceback.print_exc()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/enter_clone_backend', methods=['POST'])
@require_main_instance
def api_enter_clone_backend():
    """切换当前会话到指定克隆机器人的后台"""
    if not session.get('logged_in'): return jsonify({'status': 'error', 'msg': '需要登录'})
    if session.get('clone_id'): return jsonify({'status': 'error', 'msg': '已在克隆后台，无法再次切换'})
    d = request.json
    if not d or 'clone_id' not in d: return jsonify({'status': 'error', 'msg': '缺少克隆ID'})

    try:
        clone = BotClone.query.get(d['clone_id'])
        if not clone: return jsonify({'status': 'error', 'msg': '克隆不存在'})
        session['clone_id'] = clone.id
        return jsonify({'status': 'ok', 'redirect_url': '/core/select_group'})
    except Exception as e:
        logging.error(f"Error entering clone backend: {e}")
        return jsonify({'status': 'error', 'msg': '服务器错误，请稍后再试'})


@core_bp.route('/exit_clone_backend')
@require_main_instance
def exit_clone_backend():
    """退出克隆后台，返回主机器人后台"""
    if not session.get('logged_in'): return redirect('/core')
    session.pop('clone_id', None)
    session.pop('current_group_id', None)
    return redirect('/core/bot_clones')


@core_bp.route('/api/resync_clone_groups', methods=['POST'])
def api_resync_clone_groups():
    """
    手动同步克隆机器人的群组/频道。
    当机器人加入群组时事件未被捕获时，可调用此接口重新注册。
    请求体: {"chat_id": "-100xxxxxxxxxx"}
    """
    if not session.get('logged_in'):
        return jsonify({'status': 'error', 'msg': '需要登录'})

    clone_id = session.get('clone_id')
    if not clone_id:
        return jsonify({'status': 'error', 'msg': '仅限克隆机器人后台使用'})

    d = request.json or {}
    chat_id_raw = str(d.get('chat_id', '')).strip()
    if not chat_id_raw:
        return jsonify({'status': 'error', 'msg': '请提供群组或频道的 chat_id'})

    # Retrieve the clone's bot token
    try:
        clone = BotClone.query.get(clone_id)
        if not clone:
            return jsonify({'status': 'error', 'msg': '克隆机器人不存在'})
        bot_token = clone.get_bot_token()
    except Exception as e:
        logging.error(f"api_resync_clone_groups: DB error fetching clone {clone_id}: {e}")
        return jsonify({'status': 'error', 'msg': '数据库错误'})

    import requests as _requests

    # Call getMe first to validate the token and obtain the bot's username.
    # This lets us include the exact bot @username in error messages so the
    # user can verify they added the correct bot account to the group.
    bot_username = None
    try:
        me_resp = _requests.get(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            timeout=10,
        )
        me_data = me_resp.json()
        if me_data.get('ok'):
            bot_username = me_data['result'].get('username')
    except Exception as e:
        logging.warning(f"api_resync_clone_groups: getMe failed for clone {clone_id}: {redact_secrets(str(e))}")

    bot_hint = f"（请确认已将 @{bot_username} 添加为群组/频道管理员）" if bot_username else ""

    # Call Telegram getChat to verify the bot has access to the chat
    try:
        resp = _requests.get(
            f"https://api.telegram.org/bot{bot_token}/getChat",
            params={"chat_id": chat_id_raw},
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        logging.error(f"api_resync_clone_groups: Telegram API error: {redact_secrets(str(e))}")
        return jsonify({'status': 'error', 'msg': '调用 Telegram API 失败，请检查网络'})

    if not data.get('ok'):
        tg_desc = data.get('description', 'Unknown error')
        return jsonify({'status': 'error', 'msg': f'Telegram 返回错误: {tg_desc}。{bot_hint}'})

    chat = data['result']
    chat_type = chat.get('type', '')
    if chat_type not in ('group', 'supergroup', 'channel'):
        return jsonify({'status': 'error', 'msg': '仅支持群组和频道'})

    chat_id_str = str(chat['id'])
    chat_title = chat.get('title', chat_id_str)
    label = '频道' if chat_type == 'channel' else '群组'

    try:
        existing = BotGroup.query.filter_by(chat_id=chat_id_str, clone_id=clone_id).first()
        if existing:
            existing.is_active = True
            existing.title = chat_title
            existing.type = chat_type
            db.session.commit()
            return jsonify({'status': 'ok', 'msg': f'{label} "{chat_title}" 已更新并激活'})
        else:
            g = BotGroup(
                chat_id=chat_id_str,
                title=chat_title,
                type=chat_type,
                is_active=True,
                clone_id=clone_id,
            )
            g.fields_config = json.dumps(DEFAULT_FIELDS, ensure_ascii=False)
            db.session.add(g)
            db.session.commit()
            return jsonify({'status': 'ok', 'msg': f'{label} "{chat_title}" 注册成功'})
    except Exception as e:
        db.session.rollback()
        logging.error(f"api_resync_clone_groups: DB error saving group: {e}")
        return jsonify({'status': 'error', 'msg': '数据库写入失败'})


@core_bp.route('/api/save_inactive_user_settings', methods=['POST'])
def api_save_inactive_user_settings():
    """保存不活跃用户设置"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d: return jsonify({'status':'error','msg':'Missing request body'})
    
    try:
        gid = safe_int(d.get('group_id'))
        group = BotGroup.query.get(gid)
        if not group: return jsonify({'status':'error','msg':'Group not found'})
        err = _api_check_group_access(group)
        if err: return err
        
        settings = InactiveUserSettings.query.filter_by(group_id=gid).first()
        if not settings:
            settings = InactiveUserSettings(group_id=gid)
            db.session.add(settings)
        
        settings.enabled = d.get('enabled', False)
        settings.inactivity_days = safe_int(d.get('inactivity_days', 30))
        settings.action_type = d.get('action_type', 'kick')
        settings.check_interval = safe_int(d.get('check_interval', 86400))
        settings.warning_enabled = d.get('warning_enabled', False)
        settings.warning_days = safe_int(d.get('warning_days', 7))
        settings.warning_message = d.get('warning_message', '')
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/save_keyword_filter', methods=['POST'])
def api_save_keyword_filter():
    """保存关键词过滤"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d: return jsonify({'status':'error','msg':'Missing request body'})
    
    try:
        gid = safe_int(d.get('group_id'))
        group = BotGroup.query.get(gid)
        if not group: return jsonify({'status':'error','msg':'Group not found'})
        err = _api_check_group_access(group)
        if err: return err
        
        filter_id = safe_int(d.get('id', 0))
        if filter_id:
            kf = KeywordFilter.query.get(filter_id)
            if not kf: return jsonify({'status':'error','msg':'Filter not found'})
            if kf.group_id != gid: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            kf = KeywordFilter(group_id=gid)
            db.session.add(kf)
        
        kf.keyword = d.get('keyword', '')
        kf.filter_type = d.get('filter_type', 'blacklist')
        kf.match_type = d.get('match_type', 'contains')
        kf.action = d.get('action', 'delete')
        kf.is_active = d.get('is_active', True)
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/delete_keyword_filter', methods=['POST'])
def api_delete_keyword_filter():
    """删除关键词过滤"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        kf = KeywordFilter.query.get(d['id'])
        if not kf: return jsonify({'status':'error','msg':'Filter not found'})
        err = _api_check_group_access(BotGroup.query.get(kf.group_id))
        if err: return err
        db.session.delete(kf)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/save_group_vote', methods=['POST'])
def api_save_group_vote():
    """保存群投票"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d: return jsonify({'status':'error','msg':'Missing request body'})
    
    try:
        gid = safe_int(d.get('group_id'))
        group = BotGroup.query.get(gid)
        if not group: return jsonify({'status':'error','msg':'Group not found'})
        err = _api_check_group_access(group)
        if err: return err
        
        vote_id = safe_int(d.get('id', 0))
        if vote_id:
            vote = GroupVote.query.get(vote_id)
            if not vote: return jsonify({'status':'error','msg':'Vote not found'})
            if vote.group_id != gid: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            vote = GroupVote(group_id=gid)
            db.session.add(vote)
        
        vote.title = d.get('title', '')
        vote.description = d.get('description', '')
        vote.options = json.dumps(d.get('options', []), ensure_ascii=False)
        vote.vote_type = d.get('vote_type', 'single')
        vote.max_choices = safe_int(d.get('max_choices', 1))
        vote.is_anonymous = d.get('is_anonymous', False)
        vote.allow_revote = d.get('allow_revote', True)
        
        # Parse dates
        if d.get('start_time'):
            try:
                vote.start_time = datetime.fromisoformat(d['start_time'].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        if d.get('end_time'):
            try:
                vote.end_time = datetime.fromisoformat(d['end_time'].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/delete_group_vote', methods=['POST'])
def api_delete_group_vote():
    """删除群投票"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        vote = GroupVote.query.get(d['id'])
        if not vote: return jsonify({'status':'error','msg':'Vote not found'})
        err = _api_check_group_access(BotGroup.query.get(vote.group_id))
        if err: return err
        db.session.delete(vote)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/publish_group_vote', methods=['POST'])
def api_publish_group_vote():
    """发布群投票到 Telegram 群组"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})

    vote = GroupVote.query.get(d['id'])
    if not vote: return jsonify({'status':'error','msg':'Vote not found'})
    group = BotGroup.query.get(vote.group_id)
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    if not global_bot_loop:
        return jsonify({'status':'error','msg':'Bot not initialized'})

    # Resolve the correct PTB app for this group's bot
    group_clone_id = group.clone_id
    if group_clone_id is not None:
        clone_info = bot_clone_manager.active_clones.get(group_clone_id)
        if not clone_info:
            return jsonify({'status':'error','msg':'Clone bot not running'})
        ptb_app = clone_info['app']
    else:
        if not global_ptb_app:
            return jsonify({'status':'error','msg':'Bot not initialized'})
        ptb_app = global_ptb_app

    try:
        options = json.loads(vote.options or '[]')
    except Exception:
        options = []
    if len(options) < 2:
        return jsonify({'status':'error','msg':'投票至少需要 2 个选项'})

    # Snapshot data needed inside the async coroutine (avoid lazy-loaded ORM access)
    vote_snapshot = {
        'id': vote.id,
        'title': vote.title,
        'options': options,
        'allow_revote': vote.allow_revote,
        'end_time': vote.end_time,
    }
    chat_id = group.chat_id
    vote_id_db = vote.id

    async def _send_vote():
        buttons = []
        for idx, option in enumerate(vote_snapshot['options']):
            buttons.append([InlineKeyboardButton(
                f"{option}",
                callback_data=f"vote_{vote_snapshot['id']}_{idx}"
            )])
        buttons.append([InlineKeyboardButton("📊 查看结果", callback_data=f"vote_result_{vote_snapshot['id']}")])
        keyboard = InlineKeyboardMarkup(buttons)

        end_info = ""
        if vote_snapshot['end_time']:
            end_info = f"\n⏰ 截止：{vote_snapshot['end_time'].strftime('%Y-%m-%d %H:%M:%S')}"
        revote_info = "✅ 允许改投" if vote_snapshot['allow_revote'] else "🔒 不允许改投"

        return await ptb_app.bot.send_message(
            chat_id=chat_id,
            text=f"📊 <b>投票：{vote_snapshot['title']}</b>\n\n👆 点击下方按钮投票{end_info}\n{revote_info}",
            reply_markup=keyboard,
            parse_mode='HTML'
        )

    future = asyncio.run_coroutine_threadsafe(_send_vote(), global_bot_loop)
    try:
        sent_msg = future.result(timeout=20)
    except TimeoutError:
        return jsonify({'status':'error','msg':'发送超时，请稍后重试'})
    except Exception as send_err:
        logging.error(f"❌ [api_publish_group_vote] send error: {send_err}")
        return jsonify({'status':'error','msg':'消息发送失败，请检查机器人权限或稍后重试'})

    # Update vote status to active and store the message_id
    try:
        vote = GroupVote.query.get(vote_id_db)
        if vote:
            vote.status = 'active'
            vote.message_id = sent_msg.message_id
            db.session.commit()
    except Exception as db_err:
        logging.error(f"❌ [api_publish_group_vote] DB update error: {db_err}")

    return jsonify({'status':'ok','msg':'投票已发布到群组'})


@core_bp.route('/api/publish_group_lottery', methods=['POST'])
def api_publish_group_lottery():
    """发布群抽奖公告到 Telegram 群组"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})

    lottery = GroupLottery.query.get(d['id'])
    if not lottery: return jsonify({'status':'error','msg':'Lottery not found'})
    group = BotGroup.query.get(lottery.group_id)
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    if not global_bot_loop:
        return jsonify({'status':'error','msg':'Bot not initialized'})

    group_clone_id = group.clone_id
    if group_clone_id is not None:
        clone_info = bot_clone_manager.active_clones.get(group_clone_id)
        if not clone_info:
            return jsonify({'status':'error','msg':'Clone bot not running'})
        ptb_app = clone_info['app']
    else:
        if not global_ptb_app:
            return jsonify({'status':'error','msg':'Bot not initialized'})
        ptb_app = global_ptb_app

    _type_label = {'message_count': '💬 发言数量抽奖', 'message_rank': '🏆 发言排行抽奖', 'random': '🎲 随机抽奖'}.get(lottery.lottery_type, lottery.lottery_type)
    lines = [f"🎉 <b>抽奖活动：{lottery.lottery_name}</b>"]
    lines.append(f"类型：{_type_label}")
    if lottery.prize_description:
        lines.append(f"奖品：{lottery.prize_description}")
    if lottery.lottery_type == 'message_count':
        lines.append(f"参与条件：活动期间发言 ≥ {lottery.min_messages} 条（发言越多，中奖概率越高）")
    elif lottery.lottery_type == 'message_rank':
        lines.append(f"获奖人数：发言最多的前 {lottery.top_n_winners} 名")
    else:
        lines.append(f"获奖人数：随机抽取 {lottery.top_n_winners} 名幸运者")
    if lottery.start_time:
        lines.append(f"开始时间：{lottery.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    if lottery.end_time:
        lines.append(f"结束时间：{lottery.end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("\n积极发言，参与抽奖！")
    announcement = "\n".join(lines)
    target_chat_ids = [group.chat_id]
    if lottery.announce_channel_id and lottery.announce_channel_id not in target_chat_ids:
        target_chat_ids.append(lottery.announce_channel_id)
    lottery_id_db = lottery.id

    async def _send_lottery_announcement(chat_id):
        return await ptb_app.bot.send_message(
            chat_id=chat_id,
            text=announcement,
            parse_mode='HTML'
        )

    for target_chat_id in target_chat_ids:
        future = asyncio.run_coroutine_threadsafe(_send_lottery_announcement(target_chat_id), global_bot_loop)
        try:
            future.result(timeout=20)
        except TimeoutError:
            return jsonify({'status':'error','msg':'发送超时，请稍后重试'})
        except Exception as send_err:
            logging.error(f"❌ [api_publish_group_lottery] send error to {target_chat_id}: {send_err}")
            return jsonify({'status':'error','msg':'消息发送失败，请检查机器人权限或稍后重试'})

    # Mark lottery as active if still pending
    try:
        lottery = GroupLottery.query.get(lottery_id_db)
        if lottery and lottery.status == 'pending':
            lottery.status = 'active'
            db.session.commit()
    except Exception as db_err:
        logging.error(f"❌ [api_publish_group_lottery] DB update error: {db_err}")

    return jsonify({'status':'ok','msg':'抽奖公告已发送到群组'})


@core_bp.route('/api/save_channel_forward_rule', methods=['POST'])
def api_save_channel_forward_rule():
    """保存频道转发规则"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        if d.get('id'):
            item = ChannelForwardRule.query.get(d['id'])
            if not item: return jsonify({'status':'error','msg':'Rule not found'})
            if item.group_id != d['group_id']: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            item = ChannelForwardRule(group_id=d['group_id'])
            db.session.add(item)

        item.rule_name = (d.get('rule_name') or '').strip() or '未命名规则'
        item.source_keywords = (d.get('source_keywords') or '').strip() or None
        item.target_chat_id = (d.get('target_chat_id') or '').strip()
        item.target_thread_id = safe_int(d.get('target_thread_id'), None)
        item.forward_mode = d.get('forward_mode', 'copy') if d.get('forward_mode') in ('copy', 'forward') else 'copy'
        item.is_active = bool(d.get('is_active', True))

        if not item.target_chat_id:
            return jsonify({'status':'error','msg':'目标聊天ID不能为空'})

        db.session.commit()
        return jsonify({'status':'ok', 'id': item.id})
    except Exception as e:
        db.session.rollback()
        logging.exception('保存频道转发规则失败: %s', e)
        return jsonify({'status':'error','msg':'保存频道转发规则失败'})


@core_bp.route('/api/delete_channel_forward_rule', methods=['POST'])
def api_delete_channel_forward_rule():
    """删除频道转发规则"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})

    try:
        item = ChannelForwardRule.query.get(d['id'])
        if not item: return jsonify({'status':'error','msg':'Rule not found'})
        err = _api_check_group_access(BotGroup.query.get(item.group_id))
        if err: return err
        db.session.delete(item)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        logging.exception('删除频道转发规则失败: %s', e)
        return jsonify({'status':'error','msg':'删除频道转发规则失败'})


@core_bp.route('/api/save_channel_template', methods=['POST'])
def api_save_channel_template():
    """保存频道消息模板"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        if d.get('id'):
            item = ChannelMessageTemplate.query.get(d['id'])
            if not item: return jsonify({'status':'error','msg':'Template not found'})
            if item.group_id != d['group_id']: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            item = ChannelMessageTemplate(group_id=d['group_id'])
            db.session.add(item)

        item.template_name = (d.get('template_name') or '').strip() or '未命名模板'
        item.trigger_keywords = (d.get('trigger_keywords') or '').strip() or None
        item.media_type = d.get('media_type', 'text') if d.get('media_type') in ('text', 'image', 'video') else 'text'
        item.media_url = (d.get('media_url') or '').strip() or None
        item.content = (d.get('content') or '').strip() or None
        item.links = d.get('links', '[]') or '[]'
        item.send_as_reply = bool(d.get('send_as_reply', True))
        item.is_active = bool(d.get('is_active', True))

        db.session.commit()
        return jsonify({'status':'ok', 'id': item.id})
    except Exception as e:
        db.session.rollback()
        logging.exception('保存频道模板失败: %s', e)
        return jsonify({'status':'error','msg':'保存频道模板失败'})


@core_bp.route('/api/delete_channel_template', methods=['POST'])
def api_delete_channel_template():
    """删除频道消息模板"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})

    try:
        item = ChannelMessageTemplate.query.get(d['id'])
        if not item: return jsonify({'status':'error','msg':'Template not found'})
        err = _api_check_group_access(BotGroup.query.get(item.group_id))
        if err: return err
        db.session.delete(item)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        logging.exception('删除频道模板失败: %s', e)
        return jsonify({'status':'error','msg':'删除频道模板失败'})


@core_bp.route('/api/save_channel_coupon', methods=['POST'])
def api_save_channel_coupon():
    """保存频道优惠券"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        if d.get('id'):
            item = ChannelCoupon.query.get(d['id'])
            if not item: return jsonify({'status':'error','msg':'Coupon not found'})
            if item.group_id != d['group_id']: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            item = ChannelCoupon(group_id=d['group_id'])
            db.session.add(item)

        item.coupon_name = (d.get('coupon_name') or '').strip() or '未命名优惠券'
        item.coupon_code = (d.get('coupon_code') or '').strip() or None
        item.button_text = (d.get('button_text') or '').strip() or None
        item.button_url = (d.get('button_url') or '').strip() or None
        item.trigger_keywords = (d.get('trigger_keywords') or '').strip() or None
        item.sort_order = safe_int(d.get('sort_order'), 0)
        item.is_active = bool(d.get('is_active', True))

        db.session.commit()
        return jsonify({'status':'ok', 'id': item.id})
    except Exception as e:
        db.session.rollback()
        logging.exception('保存频道优惠券失败: %s', e)
        return jsonify({'status':'error','msg':'保存频道优惠券失败'})


@core_bp.route('/api/delete_channel_coupon', methods=['POST'])
def api_delete_channel_coupon():
    """删除频道优惠券"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})

    try:
        item = ChannelCoupon.query.get(d['id'])
        if not item: return jsonify({'status':'error','msg':'Coupon not found'})
        err = _api_check_group_access(BotGroup.query.get(item.group_id))
        if err: return err
        db.session.delete(item)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        logging.exception('删除频道优惠券失败: %s', e)
        return jsonify({'status':'error','msg':'删除频道优惠券失败'})



@core_bp.route('/api/save_channel_post_buttons', methods=['POST'])
def api_save_channel_post_buttons():
    """Save channel-post auto button config (enable + URL button links)."""
    if not session.get('logged_in'):
        return jsonify({'status': 'error', 'message': '未登录'}), 401
    try:
        d = request.get_json(force=True) or {}
        gid = d.get('group_id')
        if not gid:
            return jsonify({'status': 'error', 'message': '缺少 group_id'}), 400
        group = get_group_or_403(int(gid))
        from app.services.channel_post_buttons import normalize_button_links
        links = normalize_button_links(d.get('links') or [])
        enabled = bool(d.get('enabled', False))
        item = ChannelPostButtonConfig.query.filter_by(group_id=group.id).first()
        if not item:
            item = ChannelPostButtonConfig(group_id=group.id)
            db.session.add(item)
        item.enabled = enabled
        item.links = json.dumps(links, ensure_ascii=False)
        item.updated_at = get_beijing_now()
        db.session.commit()
        return jsonify({'status': 'ok', 'id': item.id, 'enabled': item.enabled, 'links': links})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@core_bp.route('/api/clear_channel_stats', methods=['POST'])
def api_clear_channel_stats():
    """清空频道消息统计"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'group_id' not in d: return jsonify({'status':'error','msg':'Missing group_id'})

    group = BotGroup.query.get(d['group_id'])
    if not group: return jsonify({'status':'error','msg':'Group not found'})
    err = _api_check_group_access(group)
    if err: return err

    try:
        ChannelMessageStats.query.filter_by(group_id=d['group_id']).delete(synchronize_session=False)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        logging.exception('清空频道统计失败: %s', e)
        return jsonify({'status':'error','msg':'清空频道统计失败'})


@core_bp.route('/api/save_quiz_game', methods=['POST'])
def api_save_quiz_game():
    """保存问答游戏"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d: return jsonify({'status':'error','msg':'Missing request body'})
    
    try:
        gid = safe_int(d.get('group_id'))
        group = BotGroup.query.get(gid)
        if not group: return jsonify({'status':'error','msg':'Group not found'})
        err = _api_check_group_access(group)
        if err: return err
        
        quiz_id = safe_int(d.get('id', 0))
        if quiz_id:
            quiz = QuizGame.query.get(quiz_id)
            if not quiz: return jsonify({'status':'error','msg':'Quiz not found'})
            if quiz.group_id != gid: return jsonify({'status':'error','msg':'Permission denied'})
        else:
            quiz = QuizGame(group_id=gid)
            db.session.add(quiz)
        
        quiz.question = d.get('question', '')
        quiz.answers = json.dumps(d.get('answers', []), ensure_ascii=False)
        quiz.correct_answer_index = safe_int(d.get('correct_answer_index', 0))
        quiz.explanation = d.get('explanation', '')
        quiz.points_reward = safe_int(d.get('points_reward', 10))
        quiz.time_limit = safe_int(d.get('time_limit', 60))
        quiz.difficulty = d.get('difficulty', 'medium')
        quiz.category = d.get('category', '')
        quiz.is_active = d.get('is_active', True)
        
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/api/delete_quiz_game', methods=['POST'])
def api_delete_quiz_game():
    """删除问答游戏"""
    if not session.get('logged_in'): return jsonify({'status':'error','msg':'Auth required'})
    d = request.json
    if not d or 'id' not in d: return jsonify({'status':'error','msg':'Missing id'})
    
    try:
        quiz = QuizGame.query.get(d['id'])
        if not quiz: return jsonify({'status':'error','msg':'Quiz not found'})
        err = _api_check_group_access(BotGroup.query.get(quiz.group_id))
        if err: return err
        db.session.delete(quiz)
        db.session.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status':'error','msg':str(e)})


@core_bp.route('/magic_login')
@limiter.limit("20 per minute")
def magic_login():
    token = request.args.get('token')
    secret_key = os.getenv('SECRET_KEY')
    if not secret_key:
        print("❌ CRITICAL: SECRET_KEY environment variable is not set!")
        return "Configuration error: SECRET_KEY not set", 500
    try:
        data = jwt.decode(token, secret_key, algorithms=['HS256'])
        user_id = data.get('uid')
        chat_id = data.get('chat_id')

        def _get_user_name(uid):
            member = GroupMember.query.filter_by(user_id=uid).first()
            if member:
                return member.username or (
                    ((member.first_name or '') + (' ' + member.last_name if member.last_name else '')).strip()
                ) or str(uid)
            return str(uid)
        
        # Check if user is ADMIN_ID (global admin)
        admin_id = safe_int(os.getenv('ADMIN_ID', 0))
        if admin_id and user_id == admin_id:
            session['logged_in'] = True
            session['login_user_id'] = user_id
            session['login_user_name'] = _get_user_name(user_id)
            return redirect('/core/select_group')
        
        # Check if user is admin in the specific group
        if chat_id and global_ptb_app:
            try:
                # Run async check in the bot loop
                future = asyncio.run_coroutine_threadsafe(
                    is_user_admin_in_group(global_ptb_app.bot, chat_id, user_id),
                    global_bot_loop
                )
                is_admin = future.result(timeout=5)
                if is_admin:
                    session['logged_in'] = True
                    session['login_user_id'] = user_id
                    session['login_user_name'] = _get_user_name(user_id)
                    return redirect('/core/select_group')
            except Exception as e:
                print(f"Error checking group admin: {e}")
        
        return "无权限访问后台，仅限机器人管理员", 403
    except Exception as e:
        print(f"Login error: {e}")
        return "Invalid Token", 403

@core_bp.route('/auth_verify/<session_token>')
@limiter.limit("10 per minute")
def auth_verify_page(session_token):
    """Display verification page with code"""
    try:
        auth_session = AuthSession.query.filter_by(session_token=session_token).first()
        
        if not auth_session:
            return "无效的验证链接", 404
        
        # Check if session has expired
        if get_beijing_now() > auth_session.expires_at:
            return "验证链接已过期", 403
        
        # Check if already verified - redirect directly if already logged in via cookie
        if auth_session.is_verified:
            session['logged_in'] = True
            session.permanent = True  # Make session persistent
            session['clone_id'] = auth_session.clone_id
            session['login_user_id'] = auth_session.user_id
            session['login_user_name'] = auth_session.user_name
            return redirect('/core/select_group')
        
        return render_template('auth_verify.html', 
                             code=auth_session.verification_code,
                             session_token=session_token)
    except Exception as e:
        print(f"Error in auth_verify_page: {e}")
        return "服务器错误", 500

@core_bp.route('/api/check_auth_status', methods=['POST'])
@limiter.limit("30 per minute")
def api_check_auth_status():
    """Check if authentication session has been verified"""
    if not session.get('logged_in'):
        data = request.json
        session_token = data.get('session_token')
        
        if not session_token:
            return jsonify({'status': 'error', 'msg': 'Missing session_token'})
        
        auth_session = AuthSession.query.filter_by(session_token=session_token).first()
        
        if not auth_session:
            return jsonify({'status': 'error', 'msg': 'Invalid session'})
        
        # Check if expired
        if get_beijing_now() > auth_session.expires_at:
            return jsonify({'status': 'expired'})
        
        # Check if verified
        if auth_session.is_verified:
            # Set session as logged in with persistent cookie
            session['logged_in'] = True
            session.permanent = True  # Make session persistent (uses PERMANENT_SESSION_LIFETIME)
            session['clone_id'] = auth_session.clone_id
            session['login_user_id'] = auth_session.user_id
            session['login_user_name'] = auth_session.user_name
            return jsonify({
                'status': 'verified',
                'redirect_url': '/core/select_group'
            })
        
        return jsonify({'status': 'pending'})
    else:
        return jsonify({'status': 'verified', 'redirect_url': '/core/select_group'})


@core_bp.route('/api/get_member_detail')
def api_get_member_detail():
    """获取成员详细信息（支持认证和未认证用户）"""
    if not session.get('logged_in'):
        return jsonify({'status': 'error', 'msg': 'Auth required'})
    
    group_id = safe_int(request.args.get('group_id'), 0)
    user_id = safe_int(request.args.get('user_id'), 0)
    
    if not group_id or not user_id:
        return jsonify({'status': 'error', 'msg': 'Missing parameters'})
    
    # 获取积分信息
    user_points = UserPoints.query.filter_by(group_id=group_id, user_id=user_id).first()
    
    # 获取认证用户信息（如果存在）
    verified_user = GroupUser.query.filter_by(group_id=group_id, tg_id=user_id).first()
    
    # 获取积分日志
    logs = PointsLog.query.filter_by(
        group_id=group_id, user_id=user_id
    ).order_by(PointsLog.created_at.desc()).limit(10).all()
    
    result = {
        'user_id': user_id,
        'points': user_points.points_balance if user_points else 0,
        'is_verified': verified_user is not None,
        'recent_logs': [{
            'points_change': log.points_change,
            'reason': log.reason,
            'created_at': log.created_at.isoformat() if log.created_at else None
        } for log in logs]
    }
    
    if verified_user:
        try:
            profile_data = json.loads(verified_user.profile_data) if verified_user.profile_data else {}
        except:
            profile_data = {}
        
        result.update({
            'name': profile_data.get('name') or profile_data.get('first_name'),
            'username': profile_data.get('username'),
            'expiration_date': verified_user.expiration_date.isoformat() if verified_user.expiration_date else None,
            'is_banned': verified_user.is_banned,
            'checkin_time': verified_user.checkin_time.isoformat() if verified_user.checkin_time else None
        })
    
    return jsonify({'status': 'ok', 'user': result})


@core_bp.route('/api/adjust_points', methods=['POST'])
def api_adjust_points():
    """后台调整用户积分（增加或减少）"""
    if not session.get('logged_in'):
        return jsonify({'status': 'error', 'msg': '需要登录'})
    
    data = request.get_json()
    group_id = safe_int(data.get('group_id'), 0)
    user_id = safe_int(data.get('user_id'), 0)
    points_change = safe_int(data.get('points_change'), 0)
    reason = data.get('reason', '后台调整').strip()
    
    if not group_id or not user_id:
        return jsonify({'status': 'error', 'msg': '缺少必要参数'})
    
    if points_change == 0:
        return jsonify({'status': 'error', 'msg': '积分变动不能为0'})
    
    # 验证群组是否存在
    group = BotGroup.query.get(group_id)
    if not group:
        return jsonify({'status': 'error', 'msg': '群组不存在'})
    err = _api_check_group_access(group)
    if err: return err
    
    # 获取或创建用户积分记录
    user_points = UserPoints.query.filter_by(
        group_id=group_id,
        user_id=user_id
    ).first()
    
    if not user_points:
        user_points = UserPoints(
            group_id=group_id,
            user_id=user_id,
            points_balance=0
        )
        db.session.add(user_points)
    
    # 检查积分是否会变为负数
    new_balance = user_points.points_balance + points_change
    if new_balance < 0:
        return jsonify({
            'status': 'error', 
            'msg': f'积分不足，当前积分: {user_points.points_balance}，无法减少 {abs(points_change)}'
        })
    
    # 更新积分
    user_points.points_balance = new_balance
    
    # 记录积分日志
    points_log = PointsLog(
        group_id=group_id,
        user_id=user_id,
        points_change=points_change,
        reason=reason,
        balance_after=new_balance
    )
    db.session.add(points_log)
    
    try:
        db.session.commit()
        return jsonify({
            'status': 'ok', 
            'msg': f'积分调整成功',
            'new_balance': new_balance
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'msg': f'操作失败: {str(e)}'})


@core_bp.route('/logout')
def logout():
    session.clear()
    return "已退出，请关闭窗口。"

# =======================
# 🤖 机器人逻辑 (核心)
# =======================

def _get_bot_for_clone(clone_id, default_bot):
    """Return the correct Telegram Bot instance for a group.

    All periodic background jobs run on the main bot's scheduler.  For groups
    that belong to a clone bot (clone_id is not None) we must use the clone's
    own Bot object, because only that bot is a member of those groups.

    Args:
        clone_id: BotGroup.clone_id (None = main bot's group)
        default_bot: context.bot from the scheduler job (main bot)

    Returns:
        The Bot to use for Telegram API calls, or None if the clone is
        registered but not currently running (caller should skip the group).
    """
    if clone_id is None:
        return default_bot
    clone_info = bot_clone_manager.active_clones.get(clone_id)
    if clone_info:
        return clone_info['app'].bot
    # Clone is configured but not currently running — skip to avoid sending
    # messages from the wrong bot or raising untimely Telegram errors.
    return None


async def check_expired_users(context):
    """
    Periodic job to check for expired users and mute them in groups
    """
    if not global_flask_app:
        return
    
    def _sync_check():
        with global_flask_app.app_context():
            try:
                now = get_beijing_now()
                # Find expired users with a limit to avoid memory issues
                # Process in batches for large datasets
                expired_users = GroupUser.query.options(
                    joinedload(GroupUser.group)
                ).filter(
                    GroupUser.expiration_date.isnot(None),
                    GroupUser.expiration_date < now,
                    GroupUser.is_banned == False
                ).limit(EXPIRED_USERS_BATCH_SIZE).all()
                
                if expired_users:
                    print(f"🔍 Found {len(expired_users)} expired users to ban (batch limit: {EXPIRED_USERS_BATCH_SIZE})", flush=True)
                
                # Collect users to ban across ALL groups (main + clone)
                users_to_ban = []
                for user in expired_users:
                    if not user.group or not user.group.is_active:
                        continue
                    # Get configuration in sync context
                    conf = get_group_conf(user.group)
                    ban_msg = conf.get('msg_expired_ban', '⛔️ <b>您的认证已过期，已被暂时禁言。请联系管理员续费。</b>')
                    users_to_ban.append((user, user.group, ban_msg))
                
                if not users_to_ban:
                    return None
                
                # Extract scalar values BEFORE leaving the app context to avoid detached instance errors
                # This is crucial because accessing attributes outside the session context can fail
                # Note: We extract data WITHOUT marking as banned yet - we'll update DB after API success
                users_data = []
                for user, group, ban_msg in users_to_ban:
                    users_data.append({
                        'user_tg_id': user.tg_id,
                        'group_chat_id': group.chat_id,
                        'group_id': group.id,
                        'group_user_id': user.id,  # Store user.id to update database later
                        'group_title': group.title,
                        'ban_msg': ban_msg,
                        'clone_id': group.clone_id,  # which bot to use for Telegram API calls
                    })
                print(f"📦 [定时任务] Extracted data for {len(users_data)} users to ban", flush=True)
                
                # Return the extracted data for async processing
                return users_data
                            
            except Exception as e:
                print(f"Error in check_expired_users sync part: {e}")
                db.session.rollback()
                return None
    
    # Run sync DB operations in executor
    users_to_ban = await asyncio.get_running_loop().run_in_executor(None, _sync_check)
    
    if not users_to_ban:
        return
    
    # Log the users_to_ban list to confirm we have users to process
    print(f"🚀 [定时任务] Starting async ban operations for {len(users_to_ban)} users", flush=True)
    for idx, user_data in enumerate(users_to_ban):
        print(f"   User {idx+1}: tg_id={user_data['user_tg_id']}, group={user_data['group_title']} (chat_id={user_data['group_chat_id']})", flush=True)
    
    # Helper function to mark user as banned in database after successful API call
    def _mark_user_banned_in_db(group_user_id):
        """Mark user as banned in database - called after successful API mute"""
        with global_flask_app.app_context():
            try:
                group_user = GroupUser.query.get(group_user_id)
                if group_user:
                    group_user.is_banned = True
                    db.session.commit()
                    return True
            except Exception as e:
                print(f"❌ [定时任务] Failed to mark user {group_user_id} as banned in DB: {e}", flush=True)
                db.session.rollback()
                return False
        return False
    
    # Now perform all async Telegram operations with rate limiting
    async def ban_user_async(user_data):
        """Mute an expired user and send notification"""
        user_tg_id = user_data['user_tg_id']
        group_chat_id = user_data['group_chat_id']
        group_id = user_data['group_id']
        group_user_id = user_data['group_user_id']
        group_title = user_data['group_title']
        ban_msg = user_data['ban_msg']
        
        # Use the bot that owns the group (main or clone)
        bot = _get_bot_for_clone(user_data.get('clone_id'), context.bot)
        if bot is None:
            print(f"⏭️ [定时任务] 跳过群组 {group_title}：对应克隆机器人未运行", flush=True)
            return
        
        print(f"🔧 [定时任务] ban_user_async CALLED for user {user_tg_id} in group {group_title}", flush=True)
        try:
            # Convert chat_id to integer for Telegram API
            chat_id_int = convert_chat_id_to_int(group_chat_id, group_id, group_title)
            if chat_id_int is None:
                print(f"❌ [定时任务] Failed to convert chat_id for group {group_id} ({group_title}), chat_id={group_chat_id}", flush=True)
                return
            
            # Mute the user in the group with comprehensive restrictions
            try:
                await bot.restrict_chat_member(
                    chat_id=chat_id_int,
                    user_id=user_tg_id,
                    permissions=get_muted_permissions()
                )
                print(f"✅ [定时任务] Successfully called restrict_chat_member API - Muted user {user_tg_id} in group {group_title} (chat_id={chat_id_int})", flush=True)
                
                # ✅ NEW: Only mark as banned in DB after successful API call
                success = await asyncio.get_running_loop().run_in_executor(None, _mark_user_banned_in_db, group_user_id)
                if success:
                    print(f"✅ [定时任务] Marked user {user_tg_id} as banned in database after successful API call", flush=True)
                else:
                    print(f"⚠️  [定时任务] API succeeded but failed to update database for user {user_tg_id}", flush=True)
                    
            except Exception as restrict_error:
                # 详细记录 restrict_chat_member API 调用失败的错误
                print(f"❌ [定时任务] restrict_chat_member API failed for user {user_tg_id} in group {group_title} (chat_id={chat_id_int})", flush=True)
                print(f"   Error type: {type(restrict_error).__name__}", flush=True)
                print(f"   Error details: {str(restrict_error)}", flush=True)
                print(f"   Traceback: {traceback.format_exc()}", flush=True)
                print(f"   ⚠️  Database NOT updated - user will be retried in next check", flush=True)
                # Don't re-raise to avoid duplicate logging in outer exception handler
                return
            
            # Try to send notification to user privately
            try:
                # Sanitize HTML before sending to Telegram
                sanitized_msg = sanitize_html_for_telegram(ban_msg)
                await bot.send_message(
                    chat_id=user_tg_id,
                    text=sanitized_msg,
                    parse_mode='HTML'
                )
                print(f"✅ [定时任务] Sent ban notification to user {user_tg_id}", flush=True)
            except Exception as e:
                # If private message fails, we don't send to group to avoid spam
                print(f"⚠️  [定时任务] Failed to send ban notification to user {user_tg_id}: {e}", flush=True)
                
        except Exception as e:
            # 捕获所有其他异常，确保详细记录
            print(f"❌ [定时任务] Unexpected error in ban_user_async for user {user_tg_id} in group {group_chat_id} (type: {type(group_chat_id).__name__})", flush=True)
            print(f"   Error: {type(e).__name__}: {str(e)}", flush=True)
            print(f"   Full traceback: {traceback.format_exc()}", flush=True)
    
    # Use semaphore to limit concurrent operations and avoid Telegram API rate limits
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BANS)
    
    async def ban_with_limit(user_data):
        user_tg_id = user_data['user_tg_id']
        print(f"🔄 [定时任务] ban_with_limit wrapper CALLED for user {user_tg_id}", flush=True)
        async with semaphore:
            await ban_user_async(user_data)
    
    # Run ban operations with rate limiting
    print(f"📋 [定时任务] About to call asyncio.gather for {len(users_to_ban)} ban operations", flush=True)
    results = await asyncio.gather(*[ban_with_limit(user_data) for user_data in users_to_ban], return_exceptions=True)
    print(f"✅ [定时任务] asyncio.gather completed, results count: {len(results)}", flush=True)
    
    # Log any exceptions that occurred
    for idx, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"❌ [定时任务] Exception in ban operation {idx}: {type(result).__name__}: {str(result)}", flush=True)

_CLONE_SKIP_WARN_INTERVAL = 600  # seconds
_clone_skip_last_warned = {}


def _warn_clone_not_running_for_scheduled(clone_id, msg_id):
    """Log (at most every 10 min per clone) that a clone's scheduled messages are skipped."""
    now_ts = time.time()
    last = _clone_skip_last_warned.get(clone_id)
    if last is not None and now_ts - last < _CLONE_SKIP_WARN_INTERVAL:
        return
    _clone_skip_last_warned[clone_id] = now_ts
    msg = (f"⚠️ 克隆机器人 {clone_id} 未运行，其群组的定时消息被跳过（例如消息 {msg_id}）；"
           f"启动该克隆机器人后会自动恢复发送")
    print(msg, flush=True)
    logging.warning(msg)


async def check_scheduled_messages(context):
    """
    定时检查需要发送的消息
    """
    if not global_flask_app:
        return
    
    def _sync_check():
        with global_flask_app.app_context():
            try:
                now = get_beijing_now()
                # 查找所有需要发送的定时消息
                messages_to_send = []
                expired_msg_ids = []
                
                # Ensure we get fresh data from the database (not cached)
                db.session.expire_all()
                
                scheduled_messages = ScheduledMessage.query.options(
                    joinedload(ScheduledMessage.group)
                ).filter(
                    ScheduledMessage.is_active == True
                ).all()
                
                for msg in scheduled_messages:
                    # 检查群组是否活跃
                    if not msg.group or not msg.group.is_active:
                        continue
                    
                    # 检查模块是否启用
                    conf = get_group_conf(msg.group)
                    if not conf.get('scheduled_msg_open', True):
                        continue
                    
                    # 检查开始时间
                    if msg.start_time and now < msg.start_time:
                        continue
                    
                    # 检查停止时间：已过期则收集起来批量禁用
                    if msg.stop_time and now > msg.stop_time:
                        expired_msg_ids.append(msg.id)
                        continue
                    
                    # 检查是否需要发送：next_send_at 在未来一律等待（包括开始时间已过、
                    # 从未发送过的循环消息——等到对齐的下一个时间点再发，不再立即发送）
                    should_send = scheduled_should_send(
                        msg.last_sent_at, msg.next_send_at, msg.repeat_interval or 0, now
                    )
                    
                    if should_send:
                        # 获取每个URL的类型信息
                        url_with_types = msg.get_media_url_with_types()
                        media_url_types = [t for _, t in url_with_types] if url_with_types else None
                        messages_to_send.append({
                            'id': msg.id,
                            'chat_id': msg.group.chat_id,
                            'target_type': msg.target_type or 'group',
                            'target_channel_id': msg.target_channel_id,
                            'clone_id': msg.group.clone_id,  # which bot to use
                            'media_type': msg.media_type,
                            'media_url': msg.media_url,
                            'media_urls': msg.get_media_url_list(),
                            'media_url_types': media_url_types,
                            'content': msg.content,
                            'links': msg.links,
                            'delete_previous': msg.delete_previous,
                            'last_message_id': msg.last_message_id,
                            'last_message_ids': msg.last_message_ids,
                            'message_thread_id': msg.message_thread_id,
                            'auto_pin': msg.auto_pin,
                            'repeat_interval': msg.repeat_interval,
                            'start_time': msg.start_time,  # used to align cadence
                            'last_sent_at': msg.last_sent_at,  # used for atomic claim
                            'next_send_at': msg.next_send_at,  # used to compute next schedule
                        })
                
                # 批量禁用已过期的定时消息，避免列表中继续显示启用状态
                if expired_msg_ids:
                    ScheduledMessage.query.filter(
                        ScheduledMessage.id.in_(expired_msg_ids)
                    ).update({'is_active': False}, synchronize_session=False)
                    db.session.commit()
                
                return messages_to_send
                
            except Exception as e:
                print(f"Error in check_scheduled_messages sync part: {e}")
                return []
    
    # 在 executor 中运行同步 DB 操作
    messages_to_send = await asyncio.get_running_loop().run_in_executor(None, _sync_check)
    
    if not messages_to_send:
        return
    
    # Atomically claim a message before sending to prevent duplicate sends when
    # multiple scheduler instances (e.g. after a restart with a stale job) run
    # concurrently.  The UPDATE succeeds only when last_sent_at still matches
    # the value we read during _sync_check, ensuring at-most-once delivery.
    def _try_claim(msg_id, expected_last_sent_at, claim_time):
        with global_flask_app.app_context():
            try:
                q = db.session.query(ScheduledMessage).filter(
                    ScheduledMessage.id == msg_id,
                    ScheduledMessage.is_active,
                )
                if expected_last_sent_at is None:
                    q = q.filter(ScheduledMessage.last_sent_at.is_(None))
                else:
                    q = q.filter(ScheduledMessage.last_sent_at == expected_last_sent_at)
                rows = q.update({'last_sent_at': claim_time}, synchronize_session=False)
                db.session.commit()
                return rows > 0
            except Exception as claim_err:
                db.session.rollback()
                print(f"Error claiming message {msg_id}: {claim_err}")
                return False

    send_time = get_beijing_now()

    # 发送消息
    for msg_data in messages_to_send:
        try:
            chat_id = msg_data['target_channel_id'] if msg_data.get('target_channel_id') and (msg_data.get('target_type') == 'channel' or not msg_data.get('target_type')) else msg_data['chat_id']
            message_thread_id = None if (msg_data.get('target_type') == 'channel' or (not msg_data.get('target_type') and msg_data.get('target_channel_id'))) else msg_data.get('message_thread_id')

            # Resolve which bot should send to this group
            bot = _get_bot_for_clone(msg_data.get('clone_id'), context.bot)
            if bot is None:
                _warn_clone_not_running_for_scheduled(msg_data.get('clone_id'), msg_data['id'])
                continue

            # Atomically claim the message before sending.
            # If another scheduler instance already claimed it (last_sent_at changed),
            # skip to prevent duplicate delivery.
            claimed = await asyncio.get_running_loop().run_in_executor(
                None, _try_claim, msg_data['id'], msg_data['last_sent_at'], send_time
            )
            if not claimed:
                print(f"⏭️ 跳过消息 {msg_data['id']}：已被其他调度器实例处理或已停用", flush=True)
                continue
            
            # 如果需要删除上一条消息（支持媒体组多条消息整体删除）
            if msg_data['delete_previous'] and (msg_data['last_message_id'] or msg_data.get('last_message_ids')):
                ids_to_delete = []
                if msg_data.get('last_message_ids'):
                    try:
                        ids_to_delete = json.loads(msg_data['last_message_ids'])
                    except Exception:
                        pass
                if not ids_to_delete and msg_data['last_message_id']:
                    ids_to_delete = [msg_data['last_message_id']]
                for mid in ids_to_delete:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=mid)
                    except Exception as e:
                        print(f"Failed to delete previous message {mid}: {e}")
            
            # 构建按钮
            buttons = []
            try:
                links = json.loads(msg_data['links'] or '[]')
                buttons = build_inline_keyboard_from_links(links)
            except:
                pass
            
            reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
            
            # 发送消息
            sent_message = None
            all_sent_ids = []
            content = sanitize_html_for_telegram(msg_data['content'] or '')
            media_urls = msg_data.get('media_urls', [])
            media_type = msg_data['media_type']

            if media_type in ('image', 'video', 'media') and media_urls:
                sent_message = await send_multi_media(
                    bot, chat_id, media_type, media_urls,
                    content=content, reply_markup=reply_markup,
                    message_thread_id=message_thread_id,
                    media_url_types=msg_data.get('media_url_types'),
                    collected_ids=all_sent_ids
                )
            elif content:
                sent_message = await bot.send_message(
                    chat_id=chat_id,
                    text=content,
                    parse_mode='HTML',
                    reply_markup=reply_markup,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    **({'message_thread_id': message_thread_id} if message_thread_id else {})
                )
                if sent_message:
                    all_sent_ids.append(sent_message.message_id)
            
            if not sent_message:
                # 没有发出任何消息（无内容/无媒体等）：按失败处理，计入失败次数
                raise RuntimeError('发送结果为空（没有可发送的文字或媒体）')

            # 更新消息ID；last_sent_at was already set by the atomic claim above.
            # For one-shot messages (repeat_interval=0), also deactivate so the UI
            # reflects the correct state and the scheduler skips them on future runs.
            if sent_message:
                # Compute next_send_at: use previous next_send_at as baseline so
                # that a manual-send cadence (based on the manual send time) is
                # preserved.  Fall back to start_time alignment only when
                # next_send_at is unavailable.
                new_next_send_at = None
                if msg_data['repeat_interval'] > 0:
                    prev_next = msg_data.get('next_send_at')
                    if prev_next is not None:
                        # 只在 <= 本次发送时间时才向后推，列表显示的下一个时间点不会被跳过
                        new_next_send_at = advance_next_send_at(
                            prev_next, msg_data['repeat_interval'], send_time
                        )
                    else:
                        new_next_send_at = compute_aligned_next_send_at(
                            msg_data.get('start_time'),
                            msg_data['repeat_interval'],
                            send_time,
                        )

                def _update_sent(msg_id, sent_msg_id, sent_ids, is_one_shot, next_send_at_val):
                    with global_flask_app.app_context():
                        try:
                            scheduled_msg = ScheduledMessage.query.get(msg_id)
                            if scheduled_msg and scheduled_msg.is_active:
                                scheduled_msg.last_message_id = sent_msg_id
                                scheduled_msg.last_message_ids = json.dumps(sent_ids) if sent_ids else None
                                scheduled_msg.next_send_at = next_send_at_val
                                scheduled_msg.fail_count = 0
                                if is_one_shot:
                                    scheduled_msg.is_active = False
                                db.session.commit()
                        except Exception as e:
                            db.session.rollback()
                            print(f"⚠️ _update_sent 保存失败 (msg_id={msg_id}): {e}", flush=True)
                
                await asyncio.get_running_loop().run_in_executor(
                    None, _update_sent, msg_data['id'], sent_message.message_id, all_sent_ids,
                    msg_data['repeat_interval'] == 0, new_next_send_at
                )

                # 自动置顶
                if msg_data.get('auto_pin'):
                    try:
                        await bot.pin_chat_message(
                            chat_id=chat_id,
                            message_id=sent_message.message_id,
                            disable_notification=True
                        )
                    except Exception as pin_err:
                        print(f"⚠️ 置顶消息失败 (chat_id={chat_id}): {pin_err}", flush=True)

                print(f"✅ 定时消息已发送到群组 {chat_id}", flush=True)
                
        except Exception as e:
            print(f"Error sending scheduled message {msg_data.get('id')} (chat {msg_data.get('chat_id')}): {e}", flush=True)
            # The atomic claim already set last_sent_at on the DB row.
            # One-shot (repeat_interval=0): reset last_sent_at so the next tick
            # retries, but stop after SCHEDULED_MSG_MAX_FAILURES consecutive
            # failures (auto-disable) instead of retrying every minute forever.
            # Repeating: keep the claim and advance next_send_at to the next slot.
            def _update_failed(msg_id):
                with global_flask_app.app_context():
                    try:
                        scheduled_msg = ScheduledMessage.query.get(msg_id)
                        if not scheduled_msg or not scheduled_msg.is_active:
                            return
                        scheduled_msg.fail_count = (scheduled_msg.fail_count or 0) + 1
                        if not scheduled_msg.repeat_interval:
                            if scheduled_msg.fail_count >= SCHEDULED_MSG_MAX_FAILURES:
                                scheduled_msg.is_active = False
                                print(
                                    f"⛔ 定时消息 {msg_id} 连续发送失败 {scheduled_msg.fail_count} 次，已自动停用"
                                    f"（请检查机器人是否仍在群/频道内并有发言权限，修改后重新启用）",
                                    flush=True,
                                )
                            else:
                                scheduled_msg.last_sent_at = None  # allow retry
                                print(
                                    f"⚠️ 定时消息 {msg_id} 发送失败（第 {scheduled_msg.fail_count}/{SCHEDULED_MSG_MAX_FAILURES} 次），下次检查时重试",
                                    flush=True,
                                )
                        else:
                            now_bj = get_beijing_now()
                            if scheduled_msg.next_send_at is not None:
                                new_next = advance_next_send_at(
                                    scheduled_msg.next_send_at, scheduled_msg.repeat_interval, now_bj
                                )
                            else:
                                new_next = compute_aligned_next_send_at(
                                    scheduled_msg.start_time, scheduled_msg.repeat_interval, now_bj
                                )
                            scheduled_msg.next_send_at = new_next
                        db.session.commit()
                    except Exception as upd_err:
                        db.session.rollback()
                        print(f"⚠️ _update_failed 保存失败 (msg_id={msg_id}): {upd_err}", flush=True)
            await asyncio.get_running_loop().run_in_executor(None, _update_failed, msg_data['id'])

# 🆕 New Feature Handlers

async def handle_new_chat_member(update: Update, context):
    """Handle new members joining the group - Entry verification, welcome messages, etc."""
    if not global_flask_app or not update.message or not update.message.new_chat_members:
        return
    
    try:
        chat = update.effective_chat
        if chat.type not in ['group', 'supergroup']:
            return
        
        logging.info(f"👥 [入群事件] 检测到新成员加入群组 (ID: {chat.id})")
        
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                # Auto-register: bot is in this group (receiving events), register it
                bot_clone_id = context.application.bot_data.get('clone_id')
                group = BotGroup(chat_id=str(chat.id), title=chat.title, type=chat.type, is_active=True, clone_id=bot_clone_id)
                group.fields_config = json.dumps(DEFAULT_FIELDS, ensure_ascii=False)
                db.session.add(group)
                db.session.commit()
                logging.info(f"➕ [入群事件] 自动注册群组: {chat.title} (chat_id: {chat.id})")
            elif not group.is_active:
                # Re-activate: bot is still in the group (receiving events)
                group.is_active = True
                group.title = chat.title
                db.session.commit()
                logging.info(f"🔄 [入群事件] 重新激活群组: {chat.title} (chat_id: {chat.id})")
            
            logging.info(f"👥 [入群事件] 在群组 {group.id} 中处理新成员")
            
            settings = GroupEntryExitSettings.query.filter_by(group_id=group.id).first()
            if not settings:
                logging.info(f"👥 [入群事件] 群组 {group.id} 无入群设置，跳过验证")
                return
            
            for new_member in update.message.new_chat_members:
                if new_member.is_bot:
                    logging.info(f"👥 [入群事件] 跳过机器人: {new_member.first_name}")
                    continue
                
                logging.info(f"👥 [入群事件] 处理新成员: {new_member.first_name} (ID: {new_member.id})")
                
                # 记录新成员加入（包含joined_at时间）
                update_or_create_group_member(
                    group_id=group.id,
                    user_id=new_member.id,
                    username=new_member.username,
                    first_name=new_member.first_name,
                    last_name=new_member.last_name,
                    status='member',
                    is_bot=new_member.is_bot
                )
                
                # Entry verification (only when both question and answer are configured)
                if settings.entry_verification_enabled and settings.verification_question and settings.verification_answer:
                    try:
                        # Mute the new member until they answer correctly
                        try:
                            await context.bot.restrict_chat_member(
                                chat_id=chat.id,
                                user_id=new_member.id,
                                permissions=get_muted_permissions()
                            )
                        except Exception as mute_err:
                            print(f"Error muting new member during verification: {mute_err}")

                        # Send verification question
                        timeout = settings.verification_timeout or 60
                        question_text = (
                            f"👋 欢迎 <a href='tg://user?id={new_member.id}'>{new_member.first_name}</a>!\n\n"
                            f"🔐 请回答验证问题:\n{settings.verification_question}\n\n"
                            f"⏱ 请在 {timeout} 秒内回答，否则将被移出群组。"
                        )
                        await context.bot.send_message(
                            chat_id=chat.id,
                            text=question_text,
                            parse_mode='HTML'
                        )

                        # Store pending verification state in bot_data
                        pending_key = f"pending_verification:{chat.id}:{new_member.id}"
                        context.application.bot_data[pending_key] = {
                            'answer': (settings.verification_answer or '').strip().lower(),
                            'group_id': group.id,
                            'timeout': timeout,
                            'started_at': time.time(),
                        }

                        # Schedule timeout kick
                        async def _kick_on_timeout(ctx):
                            pending_key_inner = ctx.job.data
                            state = ctx.application.bot_data.pop(pending_key_inner, None)
                            if state is None:
                                return  # Already verified
                            try:
                                await ctx.bot.ban_chat_member(chat.id, new_member.id)
                                await ctx.bot.unban_chat_member(chat.id, new_member.id)
                                await ctx.bot.send_message(
                                    chat_id=chat.id,
                                    text=f"⏰ <a href='tg://user?id={new_member.id}'>{new_member.first_name}</a> 未在规定时间内完成验证，已被移出群组。",
                                    parse_mode='HTML'
                                )
                            except Exception as kick_err:
                                print(f"Error kicking unverified member: {kick_err}")

                        context.application.job_queue.run_once(
                            _kick_on_timeout,
                            when=timeout,
                            data=pending_key,
                            name=pending_key
                        )
                    except Exception as e:
                        print(f"Error sending verification question: {e}")
                
                # Welcome message
                if settings.welcome_enabled and settings.welcome_message:
                    try:
                        welcome_text = settings.welcome_message.replace('{username}', new_member.first_name)
                        welcome_text = sanitize_html_for_telegram(welcome_text)
                        
                        if settings.welcome_media_type == 'image' and settings.welcome_media_url:
                            await context.bot.send_photo(
                                chat_id=chat.id,
                                photo=settings.welcome_media_url,
                                caption=welcome_text,
                                parse_mode='HTML'
                            )
                        elif settings.welcome_media_type == 'video' and settings.welcome_media_url:
                            await context.bot.send_video(
                                chat_id=chat.id,
                                video=settings.welcome_media_url,
                                caption=welcome_text,
                                parse_mode='HTML'
                            )
                        elif welcome_text:
                            await context.bot.send_message(
                                chat_id=chat.id,
                                text=welcome_text,
                                parse_mode='HTML'
                            )
                    except Exception as e:
                        print(f"Error sending welcome message: {e}")
                
                # Track invitation for points system
                inviter_id = None
                pending_ref = None
                if update.message.from_user and update.message.from_user.id != new_member.id:
                    inviter_id = update.message.from_user.id
                    logging.info(f"🎁 [邀请活动] 检测到邀请者: {inviter_id}")
                else:
                    # Check for pending referral stored when the user followed a /mylink deep link
                    pending_ref = PendingReferral.query.filter_by(
                        group_id=group.id,
                        user_id=new_member.id
                    ).first()
                    if pending_ref:
                        inviter_id = pending_ref.inviter_id
                        logging.info(f"🎁 [邀请活动] 从专属链接检测到邀请者: {inviter_id}")
                    else:
                        logging.info(f"👥 [入群事件] 未检测到邀请者或自行加入")

                if inviter_id and is_plugin_enabled(group.id, 'invitation'):
                    invitation_activity = InvitationActivity.query.filter_by(group_id=group.id, enabled=True).first()
                    
                    if invitation_activity:
                        logging.info(f"🎁 [邀请活动] 邀请活动已启用 (奖励: {invitation_activity.reward_points} 积分)")
                        
                        # Check if this invitation was already recorded
                        existing_record = InvitationRecord.query.filter_by(
                            group_id=group.id,
                            invited_id=new_member.id
                        ).first()
                        
                        if not existing_record:
                            logging.info(f"🎁 [邀请活动] 创建新的邀请记录: {inviter_id} -> {new_member.id}")
                            
                            # Record the invitation
                            invitation_record = InvitationRecord(
                                group_id=group.id,
                                inviter_id=inviter_id,
                                invited_id=new_member.id,
                                invited_name=new_member.first_name,
                                points_awarded=invitation_activity.reward_points
                            )
                            db.session.add(invitation_record)
                            
                            # Award points to inviter
                            user_points = UserPoints.query.filter_by(
                                group_id=group.id,
                                user_id=inviter_id
                            ).first()
                            
                            if not user_points:
                                user_points = UserPoints(
                                    group_id=group.id,
                                    user_id=inviter_id,
                                    points_balance=0
                                )
                                db.session.add(user_points)
                            
                            old_balance = user_points.points_balance
                            user_points.points_balance += invitation_activity.reward_points
                            
                            # Log the points transaction
                            points_log = PointsLog(
                                group_id=group.id,
                                user_id=inviter_id,
                                points_change=invitation_activity.reward_points,
                                reason=f"邀请新成员: {new_member.first_name}",
                                balance_after=user_points.points_balance
                            )
                            db.session.add(points_log)
                            
                            # Consume the pending referral in the same transaction
                            if pending_ref:
                                db.session.delete(pending_ref)
                            
                            logging.info(f"✅ [邀请活动] 邀请记录成功: {inviter_id} 邀请了 {new_member.id} ({new_member.first_name}), 积分: {old_balance} -> {user_points.points_balance}")
                            
                            # Commit all changes before sending announcement
                            try:
                                db.session.commit()
                                logging.info(f"✅ [邀请活动] 数据库提交成功")
                            except Exception as commit_error:
                                logging.error(f"❌ [邀请活动] 数据库提交失败: {commit_error}")
                                db.session.rollback()
                                raise
                            
                            # Announce in group if enabled
                            if getattr(invitation_activity, 'announce_in_group', False):
                                try:
                                    inviter_mention = f"<a href='tg://user?id={inviter_id}'>邀请者</a>"
                                    invitee_mention = f"<a href='tg://user?id={new_member.id}'>{new_member.first_name}</a>"
                                    announcement = f"🎉 <b>邀请成功！</b>\n\n{inviter_mention} 邀请了 {invitee_mention} 加入群组\n获得积分奖励: +{invitation_activity.reward_points}"
                                    await context.bot.send_message(
                                        chat_id=chat.id,
                                        text=announcement,
                                        parse_mode='HTML'
                                    )
                                    logging.info(f"📢 [邀请活动] 成功在群组 {chat.id} 发送邀请公告")
                                except Exception as announce_error:
                                    logging.error(f"❌ [邀请活动] 发送邀请公告失败: {announce_error}")
                        else:
                            logging.info(f"🎁 [邀请活动] 邀请记录已存在，跳过 (被邀请者: {new_member.id})")
                            # Consume stale pending referral and commit member record
                            if pending_ref:
                                db.session.delete(pending_ref)
                            db.session.commit()
                    else:
                        logging.info(f"🎁 [邀请活动] 群组 {group.id} 未启用邀请活动")
                        # Commit member record if no invitation activity
                        db.session.commit()
                else:
                    logging.info(f"👥 [入群事件] 未检测到邀请者或自行加入")
                    # Commit member record if no inviter detected
                    db.session.commit()
                        
    except Exception as e:
        logging.error(f"❌ [入群事件] handle_new_chat_member 执行失败: {e}")
        logging.error(traceback.format_exc())

async def handle_left_chat_member(update: Update, context):
    """Handle members leaving the group - Exit ban functionality and record deletion"""
    if not global_flask_app or not update.message or not update.message.left_chat_member:
        return
    
    try:
        chat = update.effective_chat
        if chat.type not in ['group', 'supergroup']:
            return
        
        left_member = update.message.left_chat_member
        if left_member.is_bot:
            return
        
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group or not group.is_active:
                return
            
            # 删除群成员记录
            member = GroupMember.query.filter_by(
                group_id=group.id,
                user_id=left_member.id
            ).first()
            if member:
                db.session.delete(member)
                print(f"删除群成员记录: user_id={left_member.id}, group_id={group.id}")
            
            # 删除用户积分记录
            user_points = UserPoints.query.filter_by(
                group_id=group.id,
                user_id=left_member.id
            ).first()
            if user_points:
                db.session.delete(user_points)
                print(f"删除用户积分记录: user_id={left_member.id}, group_id={group.id}")
            
            db.session.commit()
            
            settings = GroupEntryExitSettings.query.filter_by(group_id=group.id).first()
            if not settings or not settings.exit_ban_enabled:
                return
            
            # Ban user who left
            try:
                if settings.exit_ban_duration == 0:
                    # Permanent ban
                    await context.bot.ban_chat_member(chat.id, left_member.id)
                else:
                    # Temporary ban
                    until_date = datetime.now() + timedelta(minutes=settings.exit_ban_duration)
                    await context.bot.ban_chat_member(
                        chat.id,
                        left_member.id,
                        until_date=until_date
                    )
                print(f"Banned user {left_member.id} for leaving group {chat.id}")
            except Exception as e:
                print(f"Error banning user who left: {e}")
    
    except Exception as e:
        print(f"Error in handle_left_chat_member: {e}")

from app.plugins import is_plugin_enabled, is_plugin_enabled_for_chat  # plugin gates


async def _ensure_plugin_enabled(update: Update, context, plugin_name: str) -> bool:
    """Return True if *plugin_name* should run for this chat (default-on).

    When the plugin is explicitly disabled for the group, return False so the
    caller can skip feature logic. Missing group / no Flask app → True
    (preserve historical behavior until settings exist).
    """
    if not global_flask_app:
        return True
    chat = update.effective_chat
    if not chat:
        return True

    def _check():
        with global_flask_app.app_context():
            from app.plugins import is_plugin_enabled_for_chat as _chat_plugin_on
            return _chat_plugin_on(
                chat.id,
                context.application.bot_data.get('clone_id'),
                plugin_name,
            )

    return await asyncio.get_running_loop().run_in_executor(None, _check)


async def check_spam_protection(update: Update, context):
    """Monitor messages for spam protection rules"""
    if not global_flask_app or not update.effective_message:
        return False
    
    try:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        
        if not chat or chat.type not in ['group', 'supergroup']:
            return False
        
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group or not group.is_active:
                return False
            
            # Plugin gate: only enforce when spam plugin is enabled (default-on)
            if not is_plugin_enabled(group.id, 'spam'):
                return False

            protection = SpamProtection.query.filter_by(group_id=group.id, enabled=True).first()
            if not protection:
                return False
            
            # Check whitelist
            try:
                whitelist = json.loads(protection.whitelist_users or '[]')
                if user.id in whitelist:
                    return False
            except json.JSONDecodeError as e:
                print(f"Error parsing whitelist JSON: {e}")
                # Continue with spam check if whitelist is invalid
            
            # Check for blocked content
            should_punish = False

            # --- Rate limiting (in-memory counter via bot_data) ---
            if getattr(protection, 'rate_limit_enabled', False):
                _max_msgs = getattr(protection, 'rate_limit_max_messages', 5) or 5
                _window = getattr(protection, 'rate_limit_window_seconds', 10) or 10
                rate_key = f"rate:{chat.id}:{user.id}"
                now_ts = time.time()
                bucket = context.application.bot_data.setdefault(rate_key, [])
                # Evict timestamps outside the window
                bucket[:] = [t for t in bucket if now_ts - t < _window]
                bucket.append(now_ts)
                if len(bucket) > _max_msgs:
                    should_punish = True
                elif len(bucket) == 1:
                    # Bucket had been empty before this message; nothing to clean up yet.
                    pass
            
            # Block links
            if protection.block_links and msg.text:
                if 'http://' in msg.text or 'https://' in msg.text or 'www.' in msg.text:
                    should_punish = True
            
            # Block forwards (use hasattr for safety)
            if protection.block_forwards and hasattr(msg, 'forward_origin') and msg.forward_origin:
                should_punish = True
            
            # Block stickers
            if protection.block_stickers and msg.sticker:
                should_punish = True
            
            if should_punish:
                # Delete the message
                try:
                    await msg.delete()
                except Exception as e:
                    print(f"Failed to delete spam message: {e}")
                
                # Apply punishment
                if protection.punishment_type == 'mute':
                    until_date = datetime.now() + timedelta(minutes=protection.punishment_duration)
                    await context.bot.restrict_chat_member(
                        chat_id=chat.id,
                        user_id=user.id,
                        permissions=get_muted_permissions(),
                        until_date=until_date
                    )
                elif protection.punishment_type == 'kick':
                    await context.bot.ban_chat_member(chat.id, user.id)
                    await context.bot.unban_chat_member(chat.id, user.id)
                elif protection.punishment_type == 'ban':
                    until_date = datetime.now() + timedelta(minutes=protection.punishment_duration)
                    await context.bot.ban_chat_member(chat.id, user.id, until_date=until_date)
                
                return True
            
            return False
            
    except Exception as e:
        print(f"Error in check_spam_protection: {e}")
        return False

async def check_forced_subscription(update: Update, context):
    """Real-time check if user is subscribed to required channel before allowing message"""
    if not global_flask_app or not update.message:
        return False
    
    try:
        chat = update.effective_chat
        user = update.effective_user
        
        if not chat or chat.type not in ['group', 'supergroup']:
            return False
        
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group or not group.is_active:
                return False
            
            # Get forced subscription settings
            settings = ForcedChannelSubscription.query.filter_by(
                group_id=group.id,
                enabled=True
            ).first()
            
            if not settings or not settings.channel_id:
                return False
            
            try:
                # Check if user is subscribed to the channel
                member = await context.bot.get_chat_member(
                    settings.channel_id,
                    user.id
                )
                
                # If user is not subscribed (left or kicked)
                if member.status in ['left', 'kicked']:
                    # Delete the message
                    try:
                        await update.message.delete()
                    except Exception as e:
                        print(f"Error deleting message for unsubscribed user: {e}")
                    
                    # Send verification message with subscribe button
                    verification_msg = settings.verification_message or "请先订阅我们的频道才能在群内发言"
                    channel_link = f"https://t.me/{settings.channel_username.lstrip('@')}" if settings.channel_username else None
                    
                    keyboard = None
                    if channel_link:
                        keyboard = InlineKeyboardMarkup([[
                            InlineKeyboardButton("📢 订阅频道", url=channel_link),
                            InlineKeyboardButton("✅ 已订阅", callback_data=f"check_sub_{user.id}")
                        ]])
                    
                    try:
                        alert_msg = await context.bot.send_message(
                            chat_id=chat.id,
                            text=f"⚠️ @{user.username or user.first_name}\n\n{verification_msg}",
                            reply_markup=keyboard,
                            parse_mode='HTML'
                        )
                        # Auto-delete the alert after 30 seconds
                        _task = asyncio.create_task(delete_message_after_delay(context, chat.id, alert_msg.message_id, 30))
                        _background_tasks.add(_task)
                        _task.add_done_callback(_background_tasks.discard)
                    except Exception as e:
                        print(f"Error sending subscription alert: {e}")
                    
                    # Apply punishment if configured (align with cron check_channel_subscriptions)
                    if settings.unsubscribe_action == 'kick':
                        try:
                            print(f"🔄 [实时订阅检测] 用户 {user.id} 未订阅频道，准备踢出 in group {group.id} (chat_id={chat.id})", flush=True)
                            await context.bot.ban_chat_member(chat.id, user.id)
                            await context.bot.unban_chat_member(chat.id, user.id)
                            print(f"✅ [实时订阅检测] Successfully kicked unsubscribed user {user.id} in group {group.id} (chat_id={chat.id})", flush=True)
                        except Exception as e:
                            print(f"❌ [实时订阅检测] Failed to kick unsubscribed user {user.id} in group {group.id} (chat_id={chat.id})", flush=True)
                            print(f"   Error type: {type(e).__name__}", flush=True)
                            print(f"   Error details: {str(e)}", flush=True)
                    elif settings.unsubscribe_action == 'ban':
                        try:
                            print(f"🔄 [实时订阅检测] 用户 {user.id} 未订阅频道，准备封禁 in group {group.id} (chat_id={chat.id})", flush=True)
                            await context.bot.ban_chat_member(chat.id, user.id)
                            print(f"✅ [实时订阅检测] Successfully banned unsubscribed user {user.id} in group {group.id} (chat_id={chat.id})", flush=True)
                        except Exception as e:
                            print(f"❌ [实时订阅检测] Failed to ban unsubscribed user {user.id} in group {group.id} (chat_id={chat.id})", flush=True)
                            print(f"   Error type: {type(e).__name__}", flush=True)
                            print(f"   Error details: {str(e)}", flush=True)
                    elif settings.unsubscribe_action == 'mute':
                        try:
                            print(f"🔄 [实时订阅检测] 用户 {user.id} 未订阅频道，准备禁言 in group {group.id} (chat_id={chat.id})", flush=True)
                            await context.bot.restrict_chat_member(
                                chat_id=chat.id,
                                user_id=user.id,
                                permissions=get_muted_permissions()
                            )
                            print(f"✅ [实时订阅检测] Successfully muted unsubscribed user {user.id} in group {group.id} (chat_id={chat.id})", flush=True)
                        except Exception as e:
                            print(f"❌ [实时订阅检测] Failed to mute unsubscribed user {user.id} in group {group.id} (chat_id={chat.id})", flush=True)
                            print(f"   Error type: {type(e).__name__}", flush=True)
                            print(f"   Error details: {str(e)}", flush=True)
                    
                    return True  # Message was handled (deleted)
                    
            except Exception as e:
                # User may not be in channel or bot doesn't have access to check
                print(f"Error checking channel subscription for user {user.id}: {e}")
        
        return False
        
    except Exception as e:
        print(f"Error in check_forced_subscription: {e}")
        return False

async def delete_message_after_delay(context, chat_id, message_id, delay_seconds):
    """Helper function to delete a message after a delay"""
    try:
        await asyncio.sleep(delay_seconds)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        print(f"Error deleting message after delay: {e}")

async def handle_subscription_check_callback(update: Update, context):
    """Handle callback when user clicks 'Already Subscribed' button"""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    
    if not query.data.startswith("check_sub_"):
        return
    
    try:
        target_user_id = int(query.data.split("_")[2])
        
        # Only allow the user who triggered the check to verify
        if user.id != target_user_id:
            await query.answer("❌ 此按钮不是您的", show_alert=True)
            return
        
        if not global_flask_app:
            await query.answer("❌ 系统错误", show_alert=True)
            return
        
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                await query.answer("❌ 群组不存在", show_alert=True)
                return
            
            settings = ForcedChannelSubscription.query.filter_by(
                group_id=group.id,
                enabled=True
            ).first()
            
            if not settings or not settings.channel_id:
                await query.answer("❌ 未配置订阅频道", show_alert=True)
                return
            
            try:
                member = await context.bot.get_chat_member(
                    settings.channel_id,
                    user.id
                )
                
                if member.status not in ['left', 'kicked']:
                    # User is now subscribed, unmute them if they were muted
                    try:
                        print(f"🔄 [即时订阅验证] 用户 {user.id} 已订阅频道，准备立即解除禁言 in group {group.id} (chat_id={chat.id})", flush=True)
                        await context.bot.restrict_chat_member(
                            chat_id=chat.id,
                            user_id=user.id,
                            permissions=get_unrestricted_permissions()
                        )
                        print(f"✅ [即时订阅验证] Successfully unmuted user {user.id} immediately after subscription verification in group {group.id} (chat_id={chat.id})", flush=True)
                    except Exception as e:
                        print(f"❌ [即时订阅验证] Failed to unmute user {user.id} in group {group.id} (chat_id={chat.id})", flush=True)
                        print(f"   Error type: {type(e).__name__}", flush=True)
                        print(f"   Error details: {str(e)}", flush=True)
                    
                    await query.answer("✅ 验证成功！您现在可以发言了", show_alert=True)
                    
                    # Delete the verification message
                    try:
                        await query.message.delete()
                    except:
                        pass
                else:
                    await query.answer("❌ 您还没有订阅频道，请先订阅", show_alert=True)
                    
            except Exception as e:
                print(f"Error verifying subscription: {e}")
                await query.answer("❌ 验证失败，请稍后重试", show_alert=True)
                
    except Exception as e:
        print(f"Error in handle_subscription_check_callback: {e}")
        await query.answer("❌ 系统错误", show_alert=True)

async def check_timed_group_control(context):
    """Background task to check and apply timed group controls"""
    if not global_flask_app:
        return
    
    try:
        def _get_controls():
            with global_flask_app.app_context():
                return TimedGroupControl.query.filter_by(enabled=True).all()
        
        controls = await asyncio.get_running_loop().run_in_executor(None, _get_controls)
        
        for control in controls:
            try:
                # Get current time in the specified timezone
                tz = pytz.timezone(control.timezone)
                now = datetime.now(tz)
                current_time = now.time()
                
                # Check if we should open or close the group
                if control.open_time and control.close_time:
                    # Determine if group should be open or closed
                    should_be_open = False
                    
                    if control.open_time < control.close_time:
                        # Normal case: open at 9am, close at 5pm
                        should_be_open = control.open_time <= current_time < control.close_time
                    else:
                        # Overnight case: open at 9pm, close at 6am
                        should_be_open = current_time >= control.open_time or current_time < control.close_time
                    
                    # Get group to check current state; capture clone_id before awaiting
                    with global_flask_app.app_context():
                        group = BotGroup.query.get(control.group_id)
                        if not group:
                            continue
                        
                        chat_id = int(group.chat_id)
                        group_clone_id = group.clone_id
                        
                        # Track state in group config to avoid repeated messages
                        conf = get_group_conf(group)
                        last_state = conf.get('_timed_control_state', None)
                        
                        # Only act if state has changed; pre-save new state here so we
                        # can commit inside the app context without mixing DB + async.
                        if last_state == should_be_open:
                            continue
                        conf['_timed_control_state'] = should_be_open
                        group.config = json.dumps(conf, ensure_ascii=False)
                        db.session.commit()
                    
                    # Now perform the Telegram API call outside the app context
                    bot = _get_bot_for_clone(group_clone_id, context.bot)
                    if bot is None:
                        continue
                    try:
                        if should_be_open:
                            if control.open_message:
                                open_text = sanitize_html_for_telegram(control.open_message)
                                await bot.send_message(
                                    chat_id=chat_id,
                                    text=open_text,
                                    parse_mode='HTML'
                                )
                        else:
                            if control.close_message:
                                close_text = sanitize_html_for_telegram(control.close_message)
                                await bot.send_message(
                                    chat_id=chat_id,
                                    text=close_text,
                                    parse_mode='HTML'
                                )
                    except Exception as e:
                        print(f"Error applying timed control for group {chat_id}: {e}")
                        
            except Exception as e:
                print(f"Error processing timed control for group {control.group_id}: {e}")
                
    except Exception as e:
        print(f"Error in check_timed_group_control: {e}")

async def track_user_name_change(update: Update, context):
    """Track when users change their names"""
    if not global_flask_app or not update.message:
        return
    
    try:
        chat = update.effective_chat
        user = update.effective_user
        
        if not chat or chat.type not in ['group', 'supergroup']:
            return
        
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return
            
            # Get the last known name for this user
            last_change = UserNameChange.query.filter_by(
                group_id=group.id,
                user_id=user.id
            ).order_by(UserNameChange.changed_at.desc()).first()
            
            current_name = user.first_name
            if user.last_name:
                current_name += f" {user.last_name}"
            
            # Check if name has changed
            if last_change:
                if last_change.new_name != current_name:
                    # Name has changed
                    name_change = UserNameChange(
                        group_id=group.id,
                        user_id=user.id,
                        old_name=last_change.new_name,
                        new_name=current_name,
                        changed_at=get_beijing_now()
                    )
                    db.session.add(name_change)
                    db.session.commit()
                    
                    # 🆕 Send alert message in group
                    try:
                        # Sanitize names for HTML safety
                        old_name_safe = sanitize_html_for_telegram(last_change.new_name)
                        new_name_safe = sanitize_html_for_telegram(current_name)
                        alert_message = (
                            f"📝 用户改名提醒\n\n"
                            f"用户ID: <code>{user.id}</code>\n"
                            f"旧昵称: {old_name_safe}\n"
                            f"新昵称: {new_name_safe}\n"
                            f"时间: {get_beijing_now().strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        await context.bot.send_message(
                            chat_id=chat.id,
                            text=alert_message,
                            parse_mode='HTML'
                        )
                    except Exception as e:
                        print(f"Error sending name change alert: {e}")
            else:
                # First time seeing this user, record initial name
                name_change = UserNameChange(
                    group_id=group.id,
                    user_id=user.id,
                    old_name=None,
                    new_name=current_name,
                    changed_at=get_beijing_now()
                )
                db.session.add(name_change)
                db.session.commit()
                
    except Exception as e:
        print(f"Error in track_user_name_change: {e}")

def update_or_create_group_member(group_id, user_id, username=None, first_name=None, last_name=None, status='member', is_bot=False):
    """Helper function to update or create a group member record"""
    member = GroupMember.query.filter_by(
        group_id=group_id,
        user_id=user_id
    ).first()
    
    is_new_member = not member
    if is_new_member:
        member = GroupMember(
            group_id=group_id,
            user_id=user_id,
            joined_at=datetime.now()  # 记录进群时间
        )
        db.session.add(member)
    
    # 统一处理 None 值，避免数据库不一致
    member.username = username or ''
    member.first_name = first_name or ''
    member.last_name = last_name or ''
    member.status = status
    member.is_bot = is_bot
    member.synced_at = datetime.now()
    
    return member

async def sync_group_members_task(group):
    """
    从 Telegram 同步群成员到数据库
    
    Returns:
        tuple: (success: bool, message: str, synced_count: int)
    """
    if not global_ptb_app or not global_flask_app:
        error_msg = "Bot or Flask app not initialized"
        print(f"❌ {error_msg}")
        return (False, error_msg, 0)
    
    try:
        chat_id = int(group.chat_id)
        bot = global_ptb_app.bot
        
        # 获取群管理员
        admins = await bot.get_chat_administrators(chat_id)
        
        # 获取群信息
        chat = await bot.get_chat(chat_id)
        member_count = chat.member_count if hasattr(chat, 'member_count') else 0
        
        with global_flask_app.app_context():
            now = datetime.now()
            synced_count = 0
            
            # 更新管理员
            for admin in admins:
                user = admin.user
                update_or_create_group_member(
                    group_id=group.id,
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    status=admin.status,
                    is_bot=user.is_bot
                )
                synced_count += 1
            
            # Update group's last sync timestamp (with safety check for missing column)
            try:
                group.members_last_sync = now
            except (ProgrammingError, OperationalError) as col_err:
                # Column doesn't exist yet in database, log but don't fail the sync
                print(f"⚠️ Warning: Could not update members_last_sync: {col_err}")
                print("   Please run: python migrate_database.py")
            
            db.session.commit()
            
            success_msg = f"同步成功: {synced_count} 位管理员 (总成员约 {member_count} 人)"
            print(f"✅ 同步群 {group.title} 管理员成功: {synced_count} 人 (总成员约 {member_count} 人)")
            return (True, success_msg, synced_count)
            
    except Exception as e:
        error_msg = f"同步失败: {str(e)}"
        print(f"❌ 同步群成员失败: {e}")
        traceback.print_exc()
        return (False, error_msg, 0)

async def update_group_member(update: Update, context):
    """在消息处理时自动记录/更新成员信息"""
    if not global_flask_app:
        return
    
    try:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        
        if not chat or not user or chat.type not in ['group', 'supergroup']:
            return
        
        def _update_member():
            with global_flask_app.app_context():
                group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                if not group:
                    return
                
                update_or_create_group_member(
                    group_id=group.id,
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    status='member',  # 默认为普通成员，管理员由sync_group_members_task更新
                    is_bot=user.is_bot
                )
                
                db.session.commit()
        
        await asyncio.get_running_loop().run_in_executor(None, _update_member)
        
    except Exception as e:
        print(f"Error in update_group_member: {e}")

# --- Sync media-group (album) buffer -------------------------------------------------
# Telegram delivers each album item as a separate update with the same media_group_id.
# We debounce-buffer them and flush via copy_messages / send_media_group so the target
# receives one album instead of N separate messages.
_SYNC_MEDIA_GROUP_BUFFERS: dict = {}
def _album_flush_delay_default() -> float:
    """Quiet window (seconds after the last album part) before an album is flushed.

    Telegram delivers webhook updates one at a time, interleaved with every other chat
    the bot is in, so parts of one album can arrive several seconds apart (production
    14:18 UTC+8 2026-10-07: ~4.5s between parts in one discussion group). Buffers are keyed
    by media_group_id, so a longer window only adds latency, never merges different albums.
    Override with ALBUM_FLUSH_DELAY_SECONDS.
    """
    try:
        return max(0.5, float(os.getenv('ALBUM_FLUSH_DELAY_SECONDS', '6')))
    except (TypeError, ValueError):
        return 6.0


ALBUM_MAX_ITEMS = 10  # Telegram album limit — flush immediately once complete
_ALBUM_FLUSH_DELAY = _album_flush_delay_default()
_SYNC_MEDIA_GROUP_FLUSH_DELAY = _ALBUM_FLUSH_DELAY
# Recently flushed album keys → (monotonic flush time, item count); detects late parts.
_RECENT_ALBUM_FLUSHES: dict = {}
_RECENT_ALBUM_TTL = 300.0


def _sync_from_chat_id(msg, fallback_chat_id=None):
    if hasattr(msg, 'chat_id') and msg.chat_id is not None:
        return msg.chat_id
    chat = getattr(msg, 'chat', None)
    if chat is not None and getattr(chat, 'id', None) is not None:
        return chat.id
    return fallback_chat_id


def _sync_combined_html(sender_prefix: str, body_html: str) -> str:
    from app.services.sync_service import combine_prefix_html
    return combine_prefix_html(sender_prefix, body_html)


async def _send_sync_header(bot, target_chat_id, sender_prefix: str):
    """Send the sender header line as its own message (used when it can't be attached)."""
    return await bot.send_message(
        chat_id=target_chat_id,
        text=(sender_prefix or '').rstrip('\n') or '未知',
        parse_mode='HTML',
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


def _first_message_id_obj(result):
    """copy_messages / forward_messages return a tuple; normalise to an object with message_id."""
    if not result:
        return None
    first = result[0]
    return SimpleNamespace(message_id=getattr(first, 'message_id', first))


async def _deliver_sync_media_group(bot, target_chat_id, messages, sender_prefix: str, from_chat_id,
                                    prefix_style: str = 'newline'):
    """Send buffered album messages as one media group. Returns (sent_msg, 'media_group').

    * no header → ``copy_messages`` (perfect copy);
    * header + ``forward`` style → ``forward_messages`` (native "Forwarded from"), falls back to newline;
    * header + ``newline`` style → name line prepended to the caption-bearing item (or first item);
      if that would exceed the 1024 caption limit, the name is sent as its own message right
      before an exact ``copy_messages`` of the album.
    """
    from app.services.sync_loop_guard import track_bot
    bot = track_bot(bot)  # record outputs → precise anti-loop
    from app.services.sync_service import (
        CAPTION_LIMIT,
        album_file_id,
        album_media_kind,
        album_prefix_index,
        prefix_fits,
        sync_message_html,
    )

    if not messages:
        return None, None

    ordered = sorted(messages, key=lambda m: getattr(m, 'message_id', 0) or 0)
    ids = [m.message_id for m in ordered]

    # Any album item with an inline keyboard? forward_messages cannot re-attach them.
    album_has_buttons = any(getattr(m, 'reply_markup', None) for m in ordered)

    if sender_prefix and prefix_style == 'forward' and from_chat_id is not None and not album_has_buttons:
        try:
            fwd = await bot.forward_messages(chat_id=target_chat_id, from_chat_id=from_chat_id, message_ids=ids)
            sent = _first_message_id_obj(fwd)
            if sent:
                return sent, 'media_group'
        except Exception as e:
            print(f"[sync] forward_messages album failed, fallback newline header: {e}")

    header_separate = False
    if sender_prefix:
        idx = album_prefix_index(ordered)
        header_separate = not prefix_fits(sender_prefix, getattr(ordered[idx], 'caption', None), CAPTION_LIMIT)
        if header_separate:
            await _send_sync_header(bot, target_chat_id, sender_prefix)

    # Prefer native copy so formatting / spoiler media / etc. stay intact when captions are unchanged.
    # copyMessages drops reply markup — re-attach afterwards when needed.
    if (not sender_prefix or header_separate) and from_chat_id is not None:
        try:
            copied = await bot.copy_messages(chat_id=target_chat_id, from_chat_id=from_chat_id, message_ids=ids)
            sent = _first_message_id_obj(copied)
            if sent:
                if album_has_buttons:
                    await _reattach_album_reply_markups(bot, target_chat_id, ordered, copied)
                return sent, 'media_group'
        except Exception as e:
            print(f"[sync] copy_messages album failed, fallback send_media_group: {e}")

    prefix_idx = album_prefix_index(ordered) if (sender_prefix and not header_separate) else -1
    media_items = []
    for i, m in enumerate(ordered):
        kind = album_media_kind(m)
        file_id = album_file_id(m)
        if not kind or not file_id:
            continue
        cap = sync_message_html(m, use_caption=True) if getattr(m, 'caption', None) else ''
        if i == prefix_idx:
            cap = _sync_combined_html(sender_prefix, cap)
        cap = cap or None
        parse_mode = 'HTML' if cap else None
        if kind == 'photo':
            media_items.append(InputMediaPhoto(media=file_id, caption=cap, parse_mode=parse_mode))
        elif kind == 'video':
            media_items.append(InputMediaVideo(media=file_id, caption=cap, parse_mode=parse_mode))
        elif kind == 'document':
            media_items.append(InputMediaDocument(media=file_id, caption=cap, parse_mode=parse_mode))
        elif kind == 'audio':
            media_items.append(InputMediaAudio(media=file_id, caption=cap, parse_mode=parse_mode))

    if not media_items:
        return None, None

    sent_list = await bot.send_media_group(chat_id=target_chat_id, media=media_items)
    sent_msg = sent_list[0] if sent_list else None
    if album_has_buttons and sent_list:
        await _reattach_album_reply_markups(bot, target_chat_id, ordered, sent_list)
    return sent_msg, 'media_group'


async def _flush_sync_media_group(buffer_key):
    """Flush one album buffer to all planned targets and write SyncMessageLog rows."""
    buf = _SYNC_MEDIA_GROUP_BUFFERS.pop(buffer_key, None)
    if not buf:
        return
    _note_album_flushed('sync', buffer_key, buf)

    messages = buf.get('messages') or []
    plans = buf.get('plans') or []
    bot = buf.get('bot')
    flask_app = buf.get('flask_app')
    source_group_id = buf.get('source_group_id')
    user = buf.get('user')
    chat = buf.get('chat')
    from_chat_id = buf.get('from_chat_id')

    # Loop guard: album parts that are our own sync outputs are never re-synced.
    from app.services.sync_loop_guard import filter_out_sync_outputs
    src_chat_id = from_chat_id if from_chat_id is not None else getattr(chat, 'id', None)
    if src_chat_id is not None and messages:
        kept = filter_out_sync_outputs(src_chat_id, messages)
        if len(kept) != len(messages):
            print(f"{_album_log_prefix('sync', buffer_key)} loop guard dropped "
                  f"{len(messages) - len(kept)} sync-output part(s)", flush=True)
        messages = kept

    if not messages or not plans or not bot or not flask_app:
        return

    preview = None
    for m in messages:
        if getattr(m, 'caption', None):
            preview = m.caption[:100]
            break

    with flask_app.app_context():
        from app.services.sync_service import keyword_blocks_sync

        for plan in plans:
            target_chat_id = plan['target_chat_id']
            sender_prefix = plan.get('sender_prefix') or ''
            prefix_style = plan.get('prefix_style') or 'newline'
            try:
                if keyword_blocks_sync(preview, plan.get('filter_keywords')):
                    log_entry = SyncMessageLog(
                        source_group_id=source_group_id,
                        target_group_id=target_chat_id,
                        source_message_id=messages[0].message_id,
                        user_id=user.id if user else None,
                        username=user.username if user else None,
                        message_type='media_group',
                        content_preview=preview,
                        status='filtered',
                        synced_at=get_beijing_now(),
                    )
                    db.session.add(log_entry)
                    db.session.commit()
                    continue
                sent_msg, message_type = await _deliver_sync_media_group(
                    bot, target_chat_id, messages, sender_prefix, from_chat_id, prefix_style
                )
                print(
                    f"{_album_log_prefix('sync', buffer_key)} -> {target_chat_id} "
                    f"items={len(messages)} prefix={'on/' + prefix_style if sender_prefix else 'off'} "
                    f"sent={'yes' if sent_msg else 'no'}",
                    flush=True,
                )
                if not sent_msg:
                    continue
                log_entry = SyncMessageLog(
                    source_group_id=source_group_id,
                    target_group_id=target_chat_id,
                    source_message_id=messages[0].message_id,
                    target_message_id=getattr(sent_msg, 'message_id', None),
                    user_id=user.id if user else None,
                    username=(
                        user.username if user else (
                            chat.username if chat is not None and getattr(chat, 'username', None) else None
                        )
                    ),
                    message_type=message_type or 'media_group',
                    content_preview=preview,
                    status='success',
                    synced_at=get_beijing_now(),
                )
                db.session.add(log_entry)
                db.session.commit()
            except Exception as e:
                print(f"[sync] album flush to {target_chat_id} failed: {e}")
                try:
                    log_entry = SyncMessageLog(
                        source_group_id=source_group_id,
                        target_group_id=target_chat_id,
                        source_message_id=messages[0].message_id,
                        user_id=user.id if user else None,
                        username=user.username if user else None,
                        status='failed',
                        error_message=str(e),
                        synced_at=get_beijing_now(),
                    )
                    db.session.add(log_entry)
                    db.session.commit()
                except Exception:
                    pass


async def _delayed_media_group_flush(flush_fn, buffer_key, delay: float):
    """Sleep *delay* then call ``await flush_fn(buffer_key)`` (debounced album flush)."""
    try:
        await asyncio.sleep(delay)
        await flush_fn(buffer_key)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[album] delayed flush error ({getattr(flush_fn, '__name__', flush_fn)}): {e}")


def _schedule_media_group_flush(buffers: dict, buffer_key, flush_fn, delay: float):
    """(Re)start the debounce timer for one album buffer in *buffers*.

    Shared by group/channel sync and channel forward rules: every new album part
    cancels the pending timer, so the flush runs *delay* seconds after the last part.
    A full album (10 items) flushes right away.
    """
    buf = buffers.get(buffer_key)
    if not buf:
        return
    old = buf.get('task')
    if old and not old.done():
        old.cancel()
    if len(buf.get('messages') or []) >= ALBUM_MAX_ITEMS:
        delay = 0
    buf['task'] = asyncio.create_task(_delayed_media_group_flush(flush_fn, buffer_key, delay))
    _background_tasks.add(buf['task'])
    buf['task'].add_done_callback(_background_tasks.discard)


def _album_log_prefix(kind, buffer_key):
    clone_id, chat_id, mgid = (list(buffer_key) + [None, None, None])[:3]
    return f"[album] kind={kind} clone={clone_id} chat={chat_id} mgid={mgid}"


def _note_album_flushed(kind, buffer_key, buf):
    """Log one concise line per flush and remember it to detect late parts."""
    now = time.monotonic()
    msgs = buf.get('messages') or []
    ids = sorted(getattr(m, 'message_id', 0) or 0 for m in msgs)
    waited = now - buf.get('first_seen', now)
    print(
        f"{_album_log_prefix(kind, buffer_key)} flush items={len(msgs)} "
        f"ids={ids[0] if ids else '-'}..{ids[-1] if ids else '-'} span={waited:.1f}s",
        flush=True,
    )
    _RECENT_ALBUM_FLUSHES[(kind,) + tuple(buffer_key)] = (now, len(msgs))
    if len(_RECENT_ALBUM_FLUSHES) > 500:
        cutoff = now - _RECENT_ALBUM_TTL
        for k in [k for k, v in _RECENT_ALBUM_FLUSHES.items() if v[0] < cutoff]:
            _RECENT_ALBUM_FLUSHES.pop(k, None)


def _media_group_buffer_add(buffers: dict, buffer_key, msg, init: dict, kind: str = 'album'):
    """Append *msg* to the album buffer (created from *init* on first part); dedupe by id."""
    buf = buffers.get(buffer_key)
    if not buf:
        recent = _RECENT_ALBUM_FLUSHES.get((kind,) + tuple(buffer_key))
        if recent and time.monotonic() - recent[0] < _RECENT_ALBUM_TTL:
            print(
                f"{_album_log_prefix(kind, buffer_key)} LATE part msg={getattr(msg, 'message_id', None)} "
                f"arrived {time.monotonic() - recent[0]:.1f}s after flush of {recent[1]} item(s) "
                f"(window={_ALBUM_FLUSH_DELAY}s) — will be sent separately",
                flush=True,
            )
        buf = dict(init)
        buf.setdefault('messages', [])
        buf.setdefault('task', None)
        buf['first_seen'] = time.monotonic()
        buffers[buffer_key] = buf
    if msg is not None:
        seen = {getattr(m, 'message_id', None) for m in buf['messages']}
        if getattr(msg, 'message_id', None) not in seen:
            buf['messages'].append(msg)
    return buf


def _schedule_sync_media_group_flush(buffer_key):
    _schedule_media_group_flush(
        _SYNC_MEDIA_GROUP_BUFFERS, buffer_key, _flush_sync_media_group, _SYNC_MEDIA_GROUP_FLUSH_DELAY
    )


def _enqueue_sync_media_group_item(
    *,
    bot,
    flask_app,
    clone_id,
    chat,
    user,
    msg,
    source_group_id,
    plans: list,
):
    """Buffer one album part. *plans* is a list of {target_chat_id, sender_prefix}."""
    mgid = getattr(msg, 'media_group_id', None)
    if not mgid or not plans:
        return False

    buffer_key = (clone_id, str(chat.id), str(mgid))
    _media_group_buffer_add(_SYNC_MEDIA_GROUP_BUFFERS, buffer_key, msg, kind='sync', init={
        'plans': plans,
        'bot': bot,
        'flask_app': flask_app,
        'source_group_id': source_group_id,
        'user': user,
        'chat': chat,
        'from_chat_id': chat.id,
    })

    _schedule_sync_media_group_flush(buffer_key)
    return True


# --- Channel forward-rule album buffer ---------------------------------------------
# Linked-channel posts arrive in the discussion group as one automatic-forward message
# per album item. Buffer them per (clone, discussion chat, media_group_id) and send each
# matching rule's target the whole album via forward_messages / copy_messages.
_FORWARD_MEDIA_GROUP_BUFFERS: dict = {}
_FORWARD_MEDIA_GROUP_FLUSH_DELAY = _ALBUM_FLUSH_DELAY  # quiet window after the last album part


def _forward_album_buffer_key(clone_id, chat_id, media_group_id):
    return (clone_id, str(chat_id), str(media_group_id))


def _enqueue_forward_media_group_item(*, bot, clone_id, chat_id, msg, rules, blocked=False):
    """Buffer one album part for channel forward rules.

    *blocked* marks the whole album as dropped (e.g. the captioned part was deleted
    by the discussion keyword filter) so the remaining parts are not forwarded.
    """
    mgid = getattr(msg, 'media_group_id', None)
    if not mgid:
        return False
    key = _forward_album_buffer_key(clone_id, chat_id, mgid)
    buf = _media_group_buffer_add(_FORWARD_MEDIA_GROUP_BUFFERS, key, None if blocked else msg, kind='forward', init={
        'bot': bot,
        'rules': list(rules or []),
        'from_chat_id': chat_id,
        'blocked': False,
    })
    if blocked:
        buf['blocked'] = True
    elif rules and not buf.get('rules'):
        buf['rules'] = list(rules)
    _schedule_media_group_flush(
        _FORWARD_MEDIA_GROUP_BUFFERS, key, _flush_forward_media_group, _FORWARD_MEDIA_GROUP_FLUSH_DELAY
    )
    return True


async def _send_forward_rule_album(bot, rule, from_chat_id, message_ids, source_messages=None):
    """Deliver an album to one forward rule target as a single group.

    Uses forward_messages / copy_messages (Bot API 7.0+), which keep the album grouped
    and preserve captions/entities. Falls back to per-message calls on failure.
    Inline keyboards are re-attached afterwards (copy/forward never keep them).
    """
    from app.services.sync_loop_guard import track_bot
    bot = track_bot(bot)  # record outputs → precise anti-loop
    kwargs = {
        'chat_id': rule['target_chat_id'],
        'from_chat_id': from_chat_id,
        'message_ids': list(message_ids),
    }
    if rule.get('target_thread_id'):
        kwargs['message_thread_id'] = rule['target_thread_id']
    ordered = []
    if source_messages:
        ordered = sorted(source_messages, key=lambda m: getattr(m, 'message_id', 0) or 0)
    album_has_buttons = any(getattr(m, 'reply_markup', None) for m in ordered)
    # forward_messages cannot carry a new keyboard — force copy when buttons exist
    is_forward = rule.get('forward_mode') == 'forward' and not album_has_buttons
    try:
        if is_forward:
            result = await bot.forward_messages(**kwargs)
        else:
            result = await bot.copy_messages(**kwargs)
        if album_has_buttons:
            await _reattach_album_reply_markups(bot, rule['target_chat_id'], ordered, result)
        return result
    except Exception as batch_err:
        print(f"[forward] batch album send failed for {rule.get('rule_name')}, per-message fallback: {batch_err}")
    single = {k: v for k, v in kwargs.items() if k != 'message_ids'}
    results = []
    for i, mid in enumerate(message_ids):
        markup = getattr(ordered[i], 'reply_markup', None) if i < len(ordered) else None
        if is_forward:
            results.append(await bot.forward_message(message_id=mid, **single))
        else:
            copy_kw = dict(single)
            if markup is not None:
                copy_kw['reply_markup'] = markup
            results.append(await bot.copy_message(message_id=mid, **copy_kw))
    return results


async def _flush_forward_media_group(buffer_key):
    """Evaluate forward rules once against the album caption and send the whole album."""
    from app.services.channel_automation import album_filter_text, sorted_album_message_ids

    buf = _FORWARD_MEDIA_GROUP_BUFFERS.pop(buffer_key, None)
    if not buf:
        return
    _note_album_flushed('forward', buffer_key, buf)
    if buf.get('blocked'):
        print(f"{_album_log_prefix('forward', buffer_key)} dropped (blocked by keyword filter)", flush=True)
        return
    messages = buf.get('messages') or []
    rules = buf.get('rules') or []
    bot = buf.get('bot')
    if not messages or not rules or bot is None:
        return

    album_text = album_filter_text(messages)
    message_ids = sorted_album_message_ids(messages)
    for rule in rules:
        if not text_matches_keywords(album_text, rule.get('source_keywords')):
            continue
        try:
            await _send_forward_rule_album(
                bot, rule, buf.get('from_chat_id'), message_ids, source_messages=messages,
            )
            print(
                f"{_album_log_prefix('forward', buffer_key)} rule={rule.get('rule_name')!r} "
                f"-> {rule.get('target_chat_id')} via "
                f"{'forward_messages' if rule.get('forward_mode') == 'forward' else 'copy_messages'} "
                f"items={len(message_ids)}",
                flush=True,
            )
        except Exception as rule_err:
            print(f"Error applying forward rule {rule.get('rule_name')} to album: {rule_err}")



# --- Channel post auto-buttons (edit original post reply_markup) -------------------
# Independent of discussion-group coupons. Skips posts that already have buttons.
_CHANNEL_POST_BUTTON_ALBUM_BUFFERS: dict = {}
_CHANNEL_POST_BUTTON_FLUSH_DELAY = _ALBUM_FLUSH_DELAY


async def _edit_channel_post_buttons(bot, chat_id, message_id, reply_markup):
    """editMessageReplyMarkup with clear logs for missing can_edit_messages."""
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
        )
        print(
            f"[channel_post_buttons] attached chat={chat_id} msg={message_id}",
            flush=True,
        )
        return True
    except Exception as e:
        err = str(e).lower()
        if 'not enough rights' in err or "can't edit" in err or 'chat_admin_required' in err:
            print(
                f"[channel_post_buttons] permission denied chat={chat_id} msg={message_id}: "
                f"bot needs can_edit_messages. {e}",
                flush=True,
            )
        elif 'message is not modified' in err or 'reply markup is not modified' in err:
            print(
                f"[channel_post_buttons] unchanged chat={chat_id} msg={message_id}",
                flush=True,
            )
        else:
            print(
                f"[channel_post_buttons] edit failed chat={chat_id} msg={message_id}: {e}",
                flush=True,
            )
        return False


async def _flush_channel_post_button_album(buffer_key):
    """After album debounce: attach buttons only to caption / first message."""
    from app.services.channel_post_buttons import (
        message_has_inline_buttons,
        normalize_button_links,
        pick_album_anchor_message,
    )

    buf = _CHANNEL_POST_BUTTON_ALBUM_BUFFERS.pop(buffer_key, None)
    if not buf:
        return
    _note_album_flushed('channel_post_buttons', buffer_key, buf)
    messages = buf.get('messages') or []
    bot = buf.get('bot')
    chat_id = buf.get('chat_id')
    links = normalize_button_links(buf.get('links') or [])
    if not messages or bot is None or chat_id is None or not links:
        return

    # If any album part already has buttons, skip the whole album (do not replace).
    if any(message_has_inline_buttons(m) for m in messages):
        print(
            f"{_album_log_prefix('channel_post_buttons', buffer_key)} skip: album already has buttons",
            flush=True,
        )
        return

    anchor = pick_album_anchor_message(messages)
    if anchor is None:
        return
    mid = getattr(anchor, 'message_id', None)
    if mid is None:
        return
    keyboard = build_inline_keyboard_from_links(links)
    if not keyboard:
        return
    await _edit_channel_post_buttons(bot, chat_id, mid, InlineKeyboardMarkup(keyboard))


def _enqueue_channel_post_button_album(*, bot, clone_id, chat_id, msg, links):
    """Buffer album parts; flush attaches buttons to the anchor message only."""
    mgid = getattr(msg, 'media_group_id', None)
    if not mgid:
        return
    buffer_key = (clone_id, chat_id, str(mgid))
    buf = _CHANNEL_POST_BUTTON_ALBUM_BUFFERS.get(buffer_key)
    if not buf:
        buf = {
            'messages': [],
            'bot': bot,
            'chat_id': chat_id,
            'links': links,
            'task': None,
        }
        _CHANNEL_POST_BUTTON_ALBUM_BUFFERS[buffer_key] = buf
    # Keep latest links/bot in case config was loaded per-part
    buf['bot'] = bot
    buf['chat_id'] = chat_id
    buf['links'] = links
    # Deduplicate by message_id
    mid = getattr(msg, 'message_id', None)
    existing_ids = {getattr(m, 'message_id', None) for m in buf['messages']}
    if mid not in existing_ids:
        buf['messages'].append(msg)
    print(
        f"{_album_log_prefix('channel_post_buttons', buffer_key)} part "
        f"msg={mid} items={len(buf['messages'])}",
        flush=True,
    )
    _schedule_media_group_flush(
        _CHANNEL_POST_BUTTON_ALBUM_BUFFERS,
        buffer_key,
        _flush_channel_post_button_album,
        _CHANNEL_POST_BUTTON_FLUSH_DELAY,
    )


async def _maybe_attach_channel_post_buttons(bot, msg, chat, group, clone_id):
    """If enabled, edit the channel post to add configured URL buttons.

    Skips when the post already has inline buttons, or when it is a sync/forward
    output (caller already applied the loop guard). Albums are debounced.
    """
    from app.services.channel_post_buttons import (
        message_has_inline_buttons,
        normalize_button_links,
        should_attach_channel_post_buttons,
    )

    try:
        cfg = ChannelPostButtonConfig.query.filter_by(group_id=group.id).first()
        if not cfg or not cfg.enabled:
            return
        links = normalize_button_links(cfg.get_links_list() if hasattr(cfg, 'get_links_list') else (cfg.links or '[]'))
        if not links:
            return

        mgid = getattr(msg, 'media_group_id', None)
        if mgid:
            # Album: always buffer; flush decides skip-if-has-buttons across all parts
            _enqueue_channel_post_button_album(
                bot=bot,
                clone_id=clone_id,
                chat_id=chat.id,
                msg=msg,
                links=links,
            )
            return

        if not should_attach_channel_post_buttons(True, links, msg):
            if message_has_inline_buttons(msg):
                print(
                    f"[channel_post_buttons] skip existing buttons chat={chat.id} "
                    f"msg={getattr(msg, 'message_id', None)}",
                    flush=True,
                )
            return

        keyboard = build_inline_keyboard_from_links(links)
        if not keyboard:
            return
        await _edit_channel_post_buttons(
            bot, chat.id, msg.message_id, InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        print(f"[channel_post_buttons] error: {e}", flush=True)


async def _sync_reply_markup(msg):
    """Inline keyboard from the source message, or None.

    Telegram ``copyMessage`` / ``copyMessages`` do **not** copy reply markup unless
    it is passed again. URL buttons keep working; callback buttons will hit *our*
    bot (other bots' callbacks cannot be proxied — Telegram limitation).
    """
    return getattr(msg, 'reply_markup', None) or None


async def _reattach_album_reply_markups(bot, target_chat_id, ordered_src, sent_list):
    """After album copy/send, put original inline keyboards back on matching items."""
    if not ordered_src or not sent_list:
        return
    # Normalise sent_list to a sequence of objects with message_id
    if not isinstance(sent_list, (list, tuple)):
        sent_list = [sent_list]
    n = min(len(ordered_src), len(sent_list))
    for i in range(n):
        src = ordered_src[i]
        markup = getattr(src, 'reply_markup', None)
        if not markup:
            continue
        sent = sent_list[i]
        mid = getattr(sent, 'message_id', None)
        if mid is None:
            continue
        try:
            await bot.edit_message_reply_markup(
                chat_id=target_chat_id, message_id=mid, reply_markup=markup,
            )
        except Exception as e:
            print(f"[sync] reattach album reply_markup failed msg={mid}: {e}", flush=True)


async def _deliver_sync_copy(bot, target_chat_id, msg, sender_prefix: str, sync_media: bool, from_chat_id=None,
                             prefix_style: str = 'newline'):
    """Copy *msg* to *target_chat_id*, preserving formatting.

    * no header → ``copy_message`` (perfect copy);
    * header + ``forward`` style → ``forward_message`` (native "Forwarded from" header);
      falls back to the newline style if forwarding is not allowed;
    * header + ``newline`` style → name on its own first line, original content (HTML) below.
      Text/captions that would exceed Telegram's 4096/1024 limits get the name as a separate
      message followed by an exact copy. Non-captionable content (sticker, location, …) gets
      the name line first, then the copy.
    Returns (sent_msg, message_type) or (None, None) to skip.
    """
    from app.services.sync_loop_guard import track_bot
    bot = track_bot(bot)  # record outputs → precise anti-loop
    from app.services.sync_service import (
        CAPTION_LIMIT,
        TEXT_LIMIT,
        classify_sync_message_type,
        is_media_sync_message,
        prefix_fits,
        sync_message_html,
    )

    from_chat_id = _sync_from_chat_id(msg, from_chat_id)
    message_type = classify_sync_message_type(msg)

    if is_media_sync_message(msg) and not sync_media:
        return None, None

    has_content = bool(
        msg.text or msg.photo or msg.video or msg.document or msg.audio or msg.voice
        or msg.video_note or msg.sticker or msg.animation or msg.location
        or msg.contact or msg.venue or msg.poll
    )
    if not has_content:
        return None, None

    reply_markup = await _sync_reply_markup(msg)

    async def _copy(**extra):
        kw = dict(
            chat_id=target_chat_id, from_chat_id=from_chat_id, message_id=msg.message_id, **extra
        )
        # Always re-pass markup: Bot API copyMessage drops the original keyboard otherwise.
        if reply_markup is not None and 'reply_markup' not in kw:
            kw['reply_markup'] = reply_markup
        return await bot.copy_message(**kw)

    # Native forward ("Forwarded from …") when the header uses the forward style.
    # forward_message cannot carry a new reply_markup — if the source has buttons,
    # fall through to copy so media + keyboard stay together.
    if sender_prefix and prefix_style == 'forward' and from_chat_id is not None and not reply_markup:
        try:
            sent_msg = await bot.forward_message(
                chat_id=target_chat_id, from_chat_id=from_chat_id, message_id=msg.message_id
            )
            return sent_msg, message_type
        except Exception as e:
            print(f"[sync] forward_message failed, fallback newline header: {e}")

    header_sent = False
    if from_chat_id is not None:
        try:
            if not sender_prefix:
                return await _copy(), message_type

            captionable = bool(
                msg.photo or msg.video or msg.document or msg.audio or msg.voice or msg.animation
            )
            if msg.text:
                if prefix_fits(sender_prefix, msg.text, TEXT_LIMIT):
                    sent_msg = await bot.send_message(
                        chat_id=target_chat_id,
                        text=_sync_combined_html(sender_prefix, sync_message_html(msg, use_caption=False)),
                        parse_mode='HTML',
                        reply_markup=reply_markup,
                    )
                    return sent_msg, 'text'
                await _send_sync_header(bot, target_chat_id, sender_prefix)
                header_sent = True
                return await _copy(), 'text'
            if captionable:
                if prefix_fits(sender_prefix, msg.caption, CAPTION_LIMIT):
                    caption = _sync_combined_html(sender_prefix, sync_message_html(msg, use_caption=True))
                    return await _copy(caption=caption, parse_mode='HTML'), message_type
                await _send_sync_header(bot, target_chat_id, sender_prefix)
                header_sent = True
                return await _copy(), message_type
            # sticker / video_note / location / contact / venue / poll: header line, then copy
            await _send_sync_header(bot, target_chat_id, sender_prefix)
            header_sent = True
            return await _copy(), message_type
        except Exception as e:
            print(f"[sync] copy_message failed, fallback rebuild: {e}")
            if header_sent:
                sender_prefix = ''  # name line already delivered

    sent_msg = None

    if msg.text:
        message_type = 'text'
        body = sync_message_html(msg, use_caption=False)
        synced_text = _sync_combined_html(sender_prefix, body)
        sent_msg = await bot.send_message(
            chat_id=target_chat_id, text=synced_text, parse_mode='HTML',
            reply_markup=reply_markup,
        )
    elif msg.photo and sync_media:
        message_type = 'photo'
        body = sync_message_html(msg, use_caption=True)
        caption = _sync_combined_html(sender_prefix, body) or None
        sent_msg = await bot.send_photo(
            chat_id=target_chat_id,
            photo=msg.photo[-1].file_id,
            caption=caption,
            parse_mode='HTML' if caption else None,
            reply_markup=reply_markup,
        )
    elif msg.video and sync_media:
        message_type = 'video'
        body = sync_message_html(msg, use_caption=True)
        caption = _sync_combined_html(sender_prefix, body) or None
        sent_msg = await bot.send_video(
            chat_id=target_chat_id,
            video=msg.video.file_id,
            caption=caption,
            parse_mode='HTML' if caption else None,
            reply_markup=reply_markup,
        )
    elif msg.document and sync_media:
        message_type = 'document'
        body = sync_message_html(msg, use_caption=True)
        caption = _sync_combined_html(sender_prefix, body) or None
        sent_msg = await bot.send_document(
            chat_id=target_chat_id,
            document=msg.document.file_id,
            caption=caption,
            parse_mode='HTML' if caption else None,
            reply_markup=reply_markup,
        )
    elif msg.audio and sync_media:
        message_type = 'audio'
        body = sync_message_html(msg, use_caption=True)
        caption = _sync_combined_html(sender_prefix, body) or None
        sent_msg = await bot.send_audio(
            chat_id=target_chat_id,
            audio=msg.audio.file_id,
            caption=caption,
            parse_mode='HTML' if caption else None,
            reply_markup=reply_markup,
        )
    elif msg.voice and sync_media:
        message_type = 'voice'
        body = sync_message_html(msg, use_caption=True)
        caption = _sync_combined_html(sender_prefix, body) or None
        sent_msg = await bot.send_voice(
            chat_id=target_chat_id,
            voice=msg.voice.file_id,
            caption=caption,
            parse_mode='HTML' if caption else None,
            reply_markup=reply_markup,
        )
    elif msg.video_note and sync_media:
        message_type = 'video_note'
        if sender_prefix:
            await bot.send_message(chat_id=target_chat_id, text=sender_prefix, parse_mode='HTML')
        sent_msg = await bot.send_video_note(
            chat_id=target_chat_id, video_note=msg.video_note.file_id, reply_markup=reply_markup,
        )
    elif msg.sticker and sync_media:
        message_type = 'sticker'
        if sender_prefix:
            await bot.send_message(chat_id=target_chat_id, text=sender_prefix, parse_mode='HTML')
        sent_msg = await bot.send_sticker(
            chat_id=target_chat_id, sticker=msg.sticker.file_id, reply_markup=reply_markup,
        )
    elif msg.animation and sync_media:
        message_type = 'animation'
        body = sync_message_html(msg, use_caption=True)
        caption = _sync_combined_html(sender_prefix, body) or None
        sent_msg = await bot.send_animation(
            chat_id=target_chat_id,
            animation=msg.animation.file_id,
            caption=caption,
            parse_mode='HTML' if caption else None,
            reply_markup=reply_markup,
        )
    elif msg.poll:
        message_type = 'poll'
        sent_msg = await bot.forward_message(
            chat_id=target_chat_id,
            from_chat_id=from_chat_id,
            message_id=msg.message_id,
        )
    elif msg.location:
        message_type = 'location'
        if sender_prefix:
            await bot.send_message(chat_id=target_chat_id, text=sender_prefix, parse_mode='HTML')
        sent_msg = await bot.send_location(
            chat_id=target_chat_id,
            latitude=msg.location.latitude,
            longitude=msg.location.longitude,
        )
    elif msg.contact:
        message_type = 'contact'
        if sender_prefix:
            await bot.send_message(chat_id=target_chat_id, text=sender_prefix, parse_mode='HTML')
        sent_msg = await bot.send_contact(
            chat_id=target_chat_id,
            phone_number=msg.contact.phone_number,
            first_name=msg.contact.first_name,
            last_name=msg.contact.last_name,
        )
    elif msg.venue:
        message_type = 'venue'
        if sender_prefix:
            await bot.send_message(chat_id=target_chat_id, text=sender_prefix, parse_mode='HTML')
        sent_msg = await bot.send_venue(
            chat_id=target_chat_id,
            latitude=msg.venue.location.latitude,
            longitude=msg.venue.location.longitude,
            title=msg.venue.title,
            address=msg.venue.address,
        )
    else:
        return None, None

    return sent_msg, message_type


def _is_edit_update(update) -> bool:
    """True for edited_message / edited_channel_post updates.

    PTB MessageHandler filters match ``effective_message``, which includes edits. Sync must
    not treat an edit as a new post: editing an old album (caption / buttons) arrives as one
    edited update per item, seconds apart, and each used to be copied again as a lone photo.
    """
    return bool(getattr(update, 'edited_message', None) or getattr(update, 'edited_channel_post', None))


async def handle_sync_group_messages(update: Update, context):
    """Sync messages from source group to target groups - Enhanced to sync all message types"""
    if not global_flask_app or not update.effective_message:
        return
    if _is_edit_update(update):
        return

    try:
        from app.services.sync_service import (
            build_group_sender_prefix,
            normalize_prefix_style,
            is_group_chat,
            is_same_source_target,
            keyword_blocks_sync,
            should_skip_bot_sender,
            sync_filter_text,
        )

        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user

        if not is_group_chat(chat):
            return

        # Bot API never delivers bot-authored group messages (ours or others') to bots; those
        # (incl. our own bot's posts) are synced via the userbot (小号). Keep the bot skip here
        # and additionally drop anything that is itself a sync / forward output (loop guard).
        if should_skip_bot_sender(user):
            return
        from app.services.sync_loop_guard import is_sync_output_memory
        if is_sync_output_memory(chat.id, msg.message_id):
            return

        with global_flask_app.app_context():
            clone_id = context.application.bot_data.get('clone_id')
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=clone_id).first()
            if not group:
                print(f"[sync] skip group chat {chat.id}: no BotGroup for clone_id={clone_id}")
                return

            # Single gate: SyncGroupMessages.enabled (save/toggle keep plugin row mirrored)
            sync_settings = SyncGroupMessages.query.filter_by(
                source_group_id=group.id,
                enabled=True
            ).all()
            if not sync_settings:
                return

            from app.services.sync_service import album_media_kind, is_album_message

            album_plans = []
            is_album = is_album_message(msg) and album_media_kind(msg) is not None

            for sync_setting in sync_settings:
                try:
                    # target_group_id is stored as string chat_id, not database id
                    target_chat_id = sync_setting.target_group_id

                    # Skip self-sync (source == target)
                    if is_same_source_target(chat.id, target_chat_id):
                        continue

                    # Blacklist keyword filter (text or caption)
                    filter_text = sync_filter_text(msg)
                    if keyword_blocks_sync(filter_text, sync_setting.filter_keywords):
                        log_entry = SyncMessageLog(
                            source_group_id=group.id,
                            target_group_id=target_chat_id,
                            source_message_id=msg.message_id,
                            user_id=user.id if user else None,
                            username=user.username if user else None,
                            message_type='text' if msg.text else 'media',
                            content_preview=(filter_text[:100] if filter_text else None),
                            status='filtered',
                            synced_at=get_beijing_now()
                        )
                        db.session.add(log_entry)
                        db.session.commit()
                        continue

                    # Skip forwards if not enabled (use hasattr for safety)
                    if hasattr(msg, 'forward_origin') and msg.forward_origin and not sync_setting.sync_forwards:
                        continue

                    if not sync_setting.sync_media and not msg.text:
                        continue

                    sender_prefix = build_group_sender_prefix(
                        user, bool(getattr(sync_setting, 'include_sender_prefix', False))
                    )
                    prefix_style = normalize_prefix_style(getattr(sync_setting, 'sender_prefix_style', None))

                    # Albums: buffer parts and flush as one media group
                    if is_album and sync_setting.sync_media:
                        album_plans.append({
                            'target_chat_id': target_chat_id,
                            'sender_prefix': sender_prefix,
                            'prefix_style': prefix_style,
                            'filter_keywords': sync_setting.filter_keywords,
                        })
                        continue

                    sent_msg, message_type = await _deliver_sync_copy(
                        context.bot, target_chat_id, msg, sender_prefix,
                        bool(sync_setting.sync_media), from_chat_id=chat.id,
                        prefix_style=prefix_style,
                    )
                    if message_type is None:
                        continue

                    # Log the sync if message was sent
                    if sent_msg:
                        log_entry = SyncMessageLog(
                            source_group_id=group.id,
                            target_group_id=target_chat_id,
                            source_message_id=msg.message_id,
                            target_message_id=sent_msg.message_id,
                            user_id=user.id if user else None,
                            username=user.username if user else None,
                            message_type=message_type,
                            content_preview=msg.text[:100] if msg.text else (msg.caption[:100] if msg.caption else None),
                            status='success',
                            synced_at=get_beijing_now()
                        )
                        db.session.add(log_entry)
                        db.session.commit()

                except Exception as e:
                    print(f"Error syncing message to {sync_setting.target_group_id}: {e}")
                    # Log the failure
                    log_entry = SyncMessageLog(
                        source_group_id=group.id,
                        target_group_id=sync_setting.target_group_id,
                        source_message_id=msg.message_id,
                        user_id=user.id if user else None,
                        username=user.username if user else None,
                        status='failed',
                        error_message=str(e),
                        synced_at=get_beijing_now()
                    )
                    db.session.add(log_entry)
                    db.session.commit()

            if album_plans:
                _enqueue_sync_media_group_item(
                    bot=context.bot,
                    flask_app=global_flask_app,
                    clone_id=clone_id,
                    chat=chat,
                    user=user,
                    msg=msg,
                    source_group_id=group.id,
                    plans=album_plans,
                )

    except Exception as e:
        print(f"Error in handle_sync_group_messages: {e}")



async def _maybe_send_channel_auto_reply(bot, msg, chat, group, context):
    """Reply in-channel when a channel post hits an AutoReply rule for this channel BotGroup.

    Channel sidebar exposes 自动回复, but on_message returns early for channels;
    channel_post updates only hit handle_sync_channel_posts. Exact keyword match
    (comma-separated), same as group on_message path.
    """
    try:
        conf = get_group_conf(group)
        if not conf.get('auto_reply_open', True):
            return
        txt = (msg.text or msg.caption or '').strip()
        if not txt:
            return
        auto_reply = None
        all_auto_replies = AutoReply.query.filter_by(group_id=group.id, is_active=True).all()
        for ar in all_auto_replies:
            keywords = [k.strip() for k in (ar.trigger_keyword or '').split(',') if k.strip()]
            if txt in keywords:
                auto_reply = ar
                break
        if not auto_reply:
            return

        buttons = []
        try:
            links = json.loads(auto_reply.links or '[]')
            buttons = build_inline_keyboard_from_links(links)
        except Exception:
            pass
        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        content = sanitize_html_for_telegram(auto_reply.content or '')
        media_urls = auto_reply.get_media_url_list()
        sent_reply = None
        if auto_reply.media_type in ('image', 'video', 'media') and media_urls:
            url_with_types = auto_reply.get_media_url_with_types()
            sent_reply = await send_multi_media(
                bot, chat.id, auto_reply.media_type, media_urls,
                content=content, reply_markup=reply_markup,
                media_url_types=[t for _, t in url_with_types] if url_with_types else None,
            )
        elif content:
            sent_reply = await bot.send_message(
                chat_id=chat.id,
                text=content,
                parse_mode='HTML',
                reply_markup=reply_markup,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        if sent_reply and getattr(auto_reply, 'delete_after', 0) and auto_reply.delete_after > 0:
            try:
                context.job_queue.run_once(_auto_delete_msg, auto_reply.delete_after, data=sent_reply)
            except Exception:
                pass
    except Exception as e:
        print(f"Channel auto reply error: {e}")


async def handle_sync_channel_posts(update: Update, context):
    """Sync channel posts (channel_post) to configured target chats.

    Separate from group-member sync: only runs for Telegram channel chats.
    Delivers content as-is (no ``[频道名]`` prefix). Reuses SyncGroupMessages
    rows keyed by the channel BotGroup id.
    """
    if not global_flask_app:
        return

    try:
        from app.services.sync_service import (
            build_channel_sender_prefix,
            normalize_prefix_style,
            is_channel_chat,
            is_same_source_target,
            keyword_blocks_sync,
            sync_filter_text,
        )

        # Only brand-new posts. Edits of old posts are NOT re-synced (they used to arrive one
        # album item at a time and were copied again as lone photos / duplicates).
        if _is_edit_update(update):
            ep = update.edited_channel_post or update.edited_message
            print(
                f"[sync] skip edited post chat={getattr(update.effective_chat, 'id', None)} "
                f"msg={getattr(ep, 'message_id', None)} mgid={getattr(ep, 'media_group_id', None)}",
                flush=True,
            )
            return
        msg = update.channel_post
        chat = update.effective_chat
        user = update.effective_user

        if not msg or not is_channel_chat(chat):
            return

        # Anti-loop (precise): skip only posts that are themselves sync / forward-rule outputs.
        # Posts by any bot — including our own (scheduled posts, broadcasts …) — are synced.
        # Updates are processed sequentially, so an output is registered before its echo arrives.
        from app.services.sync_loop_guard import is_sync_output
        if is_sync_output(chat.id, msg.message_id, global_flask_app):
            print(
                f"[sync] skip sync-output channel post chat={getattr(chat, 'id', None)} "
                f"msg={getattr(msg, 'message_id', None)} (loop guard)",
                flush=True,
            )
            return

        clone_id = context.application.bot_data.get('clone_id')
        await _sync_channel_post_core(context.bot, msg, chat, user, clone_id, context=context)

    except Exception as e:
        print(f"Error in handle_sync_channel_posts: {e}")


async def _sync_channel_post_core(bot, msg, chat, user, clone_id, *, context=None, own_bot_post=False):
    """Channel → targets delivery for one post.

    ``own_bot_post=True``: the post was sent by OUR bot (Telegram never delivers a bot's own
    messages back to it as channel_post updates, so :mod:`app.services.own_post_sync` calls
    this after a successful send). Only targets with ``sync_own_bot_posts`` enabled are used,
    and channel auto-reply / auto-buttons are not run for it.
    """
    from app.services.sync_service import (
        build_channel_sender_prefix,
        normalize_prefix_style,
        is_same_source_target,
        keyword_blocks_sync,
        sync_filter_text,
    )
    try:
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(
                chat_id=str(chat.id),
                clone_id=clone_id,
            ).first()
            if not group:
                print(f"[sync] skip channel {chat.id}: no BotGroup for clone_id={clone_id}")
                return

            if not own_bot_post:
                # Channel auto-reply is independent of sync settings
                await _maybe_send_channel_auto_reply(bot, msg, chat, group, context)

                # Channel post auto-buttons (edit original post); independent of sync
                await _maybe_attach_channel_post_buttons(
                    bot, msg, chat, group, clone_id,
                )

            # Single gate: SyncGroupMessages.enabled
            sync_settings = SyncGroupMessages.query.filter_by(
                source_group_id=group.id,
                enabled=True,
            ).all()
            if own_bot_post:
                sync_settings = [s for s in sync_settings
                                 if getattr(s, 'sync_own_bot_posts', True) is not False]
                print(f"[sync] own-bot channel post chat={chat.id} msg={msg.message_id} "
                      f"mgid={getattr(msg, 'media_group_id', None)} targets={len(sync_settings)}",
                      flush=True)
            if not sync_settings:
                return

            from app.services.sync_service import album_media_kind, is_album_message

            album_plans = []
            is_album = is_album_message(msg) and album_media_kind(msg) is not None

            for sync_setting in sync_settings:
                try:
                    target_chat_id = sync_setting.target_group_id

                    if is_same_source_target(chat.id, target_chat_id):
                        continue

                    filter_text = sync_filter_text(msg)
                    if keyword_blocks_sync(filter_text, sync_setting.filter_keywords):
                        log_entry = SyncMessageLog(
                            source_group_id=group.id,
                            target_group_id=target_chat_id,
                            source_message_id=msg.message_id,
                            user_id=user.id if user else None,
                            username=user.username if user else (chat.username if getattr(chat, 'username', None) else None),
                            message_type='text' if msg.text else 'media',
                            content_preview=(filter_text[:100] if filter_text else None),
                            status='filtered',
                            synced_at=get_beijing_now(),
                        )
                        db.session.add(log_entry)
                        db.session.commit()
                        continue

                    if hasattr(msg, 'forward_origin') and msg.forward_origin and not sync_setting.sync_forwards:
                        continue

                    if not sync_setting.sync_media and not msg.text:
                        continue

                    # Default off (clean copy, no [频道名]); optional channel-name header / forward style
                    sender_prefix = build_channel_sender_prefix(
                        chat, bool(getattr(sync_setting, 'include_sender_prefix', False))
                    )
                    prefix_style = normalize_prefix_style(getattr(sync_setting, 'sender_prefix_style', None))

                    if is_album and sync_setting.sync_media:
                        album_plans.append({
                            'target_chat_id': target_chat_id,
                            'sender_prefix': sender_prefix,
                            'prefix_style': prefix_style,
                            'filter_keywords': sync_setting.filter_keywords,
                        })
                        continue

                    sent_msg, message_type = await _deliver_sync_copy(
                        bot, target_chat_id, msg, sender_prefix,
                        bool(sync_setting.sync_media), from_chat_id=chat.id,
                        prefix_style=prefix_style,
                    )
                    if message_type is None:
                        continue

                    if sent_msg:
                        log_entry = SyncMessageLog(
                            source_group_id=group.id,
                            target_group_id=target_chat_id,
                            source_message_id=msg.message_id,
                            target_message_id=sent_msg.message_id,
                            user_id=user.id if user else None,
                            username=user.username if user else (chat.username if getattr(chat, 'username', None) else None),
                            message_type=message_type,
                            content_preview=msg.text[:100] if msg.text else (msg.caption[:100] if msg.caption else None),
                            status='success',
                            synced_at=get_beijing_now(),
                        )
                        db.session.add(log_entry)
                        db.session.commit()

                except Exception as e:
                    print(f"Error syncing channel post to {sync_setting.target_group_id}: {e}")
                    log_entry = SyncMessageLog(
                        source_group_id=group.id,
                        target_group_id=sync_setting.target_group_id,
                        source_message_id=msg.message_id,
                        user_id=user.id if user else None,
                        username=user.username if user else None,
                        status='failed',
                        error_message=str(e),
                        synced_at=get_beijing_now(),
                    )
                    db.session.add(log_entry)
                    db.session.commit()

            if album_plans:
                _enqueue_sync_media_group_item(
                    bot=bot,
                    flask_app=global_flask_app,
                    clone_id=clone_id,
                    chat=chat,
                    user=user,
                    msg=msg,
                    source_group_id=group.id,
                    plans=album_plans,
                )

    except Exception as e:
        print(f"Error in _sync_channel_post_core: {e}")


async def handle_auto_delete_messages(update: Update, context):
    """Auto-delete system messages based on settings"""
    if not global_flask_app or not update.message:
        return
    
    try:
        msg = update.message
        chat = update.effective_chat
        
        if not chat or chat.type not in ['group', 'supergroup']:
            return
        
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return
            
            settings = OtherSettings.query.filter_by(group_id=group.id).first()
            if not settings:
                return
            
            should_delete = False
            
            # Check for join messages
            if settings.auto_delete_join_msg and msg.new_chat_members:
                should_delete = True
            
            # Check for leave messages
            if settings.auto_delete_leave_msg and msg.left_chat_member:
                should_delete = True
            
            # Check for pin messages
            if settings.auto_delete_pin_msg and msg.pinned_message:
                should_delete = True
            
            if should_delete:
                try:
                    await msg.delete()
                except Exception as e:
                    print(f"Error deleting message: {e}")
                    
    except Exception as e:
        print(f"Error in handle_auto_delete_messages: {e}")

async def check_channel_subscriptions(context):
    """Background task to check forced channel subscriptions"""
    if not global_flask_app:
        return
    
    try:
        def _get_subscription_settings():
            with global_flask_app.app_context():
                return ForcedChannelSubscription.query.filter_by(enabled=True).all()
        
        settings_list = await asyncio.get_running_loop().run_in_executor(None, _get_subscription_settings)
        
        for settings in settings_list:
            try:
                if not settings.channel_id:
                    continue
                
                def _get_group_and_users(group_id):
                    with global_flask_app.app_context():
                        group = BotGroup.query.get(group_id)
                        if not group:
                            return None, None, None, None
                        group_users = GroupUser.query.filter_by(group_id=group.id).order_by(GroupUser.id.asc()).limit(SUBSCRIPTION_CHECK_BATCH_SIZE).all()
                        return int(group.chat_id), group.clone_id, group.id, [
                            {'row_id': u.id, 'tg_id': u.tg_id, 'is_channel_subscribed': bool(u.is_channel_subscribed)}
                            for u in group_users
                        ]

                def _update_subscription_state(group_id, row_id, tg_id, subscribed, reward_points, settings_id=None):
                    with global_flask_app.app_context():
                        group_user = GroupUser.query.get(row_id)
                        if not group_user:
                            return
                        was_subscribed = bool(group_user.is_channel_subscribed)
                        group_user.is_channel_subscribed = subscribed
                        if subscribed and not was_subscribed and reward_points > 0:
                            # Check reward_given_user_ids to avoid awarding points more than once
                            if settings_id:
                                fcs = ForcedChannelSubscription.query.get(settings_id)
                                if fcs:
                                    try:
                                        given_ids = json.loads(fcs.reward_given_user_ids or '[]')
                                    except (json.JSONDecodeError, TypeError):
                                        given_ids = []
                                    if tg_id in given_ids:
                                        db.session.commit()
                                        return
                                    given_ids.append(tg_id)
                                    fcs.reward_given_user_ids = json.dumps(given_ids)
                            user_points = UserPoints.query.filter_by(group_id=group_id, user_id=tg_id).first()
                            if not user_points:
                                user_points = UserPoints(group_id=group_id, user_id=tg_id, points_balance=0)
                                db.session.add(user_points)
                            user_points.points_balance += reward_points
                            db.session.add(PointsLog(
                                group_id=group_id,
                                user_id=tg_id,
                                points_change=reward_points,
                                reason=_REASON_CHANNEL_SUB,
                                balance_after=user_points.points_balance
                            ))
                        db.session.commit()
                
                chat_id, group_clone_id, current_group_id, group_users = await asyncio.get_running_loop().run_in_executor(
                    None, _get_group_and_users, settings.group_id
                )
                if chat_id is None:
                    continue
                
                # Use the bot that owns this group
                bot = _get_bot_for_clone(group_clone_id, context.bot)
                if bot is None:
                    continue
                
                group_chat_gone = False
                for user_info in group_users:
                    if group_chat_gone:
                        break
                    tg_id = user_info['tg_id']
                    try:
                        # Check if user is subscribed to the channel
                        member = await bot.get_chat_member(
                            settings.channel_id,
                            tg_id
                        )
                        
                        # Cache the owner check result: the group owner rarely changes
                        # during a single scheduler tick and we may need it in both branches.
                        is_owner = None

                        # If not subscribed (left or kicked), apply action
                        if member.status in ['left', 'kicked']:
                            await asyncio.get_running_loop().run_in_executor(
                                None, _update_subscription_state, current_group_id, user_info['row_id'], tg_id, False, 0
                            )
                            # Check if user is chat owner/admin in the group - skip all actions for them
                            is_owner = await is_user_chat_owner(bot, chat_id, tg_id)
                            if is_owner:
                                print(f"⏭️ [频道订阅检测] 跳过惩罚操作 - 用户 {tg_id} 是群主/管理员 (Owner/Admin) in group {settings.group_id} (chat_id={chat_id})", flush=True)
                                continue
                            
                            if settings.unsubscribe_action == 'kick':
                                await bot.ban_chat_member(chat_id, tg_id)
                                await bot.unban_chat_member(chat_id, tg_id)
                            elif settings.unsubscribe_action == 'ban':
                                await bot.ban_chat_member(chat_id, tg_id)
                            elif settings.unsubscribe_action == 'mute':
                                try:
                                    print(f"🔄 [频道订阅检测] 准备禁言未订阅用户 {tg_id} in group {settings.group_id} (chat_id={chat_id})", flush=True)
                                    await bot.restrict_chat_member(
                                        chat_id=chat_id,
                                        user_id=tg_id,
                                        permissions=get_muted_permissions()
                                    )
                                    print(f"✅ [频道订阅检测] Successfully called restrict_chat_member API - Muted unsubscribed user {tg_id} in group {settings.group_id} (chat_id={chat_id})", flush=True)
                                except Exception as restrict_error:
                                    print(f"❌ [频道订阅检测] restrict_chat_member API failed for user {tg_id} in group {settings.group_id} (chat_id={chat_id})", flush=True)
                                    print(f"   Error type: {type(restrict_error).__name__}", flush=True)
                                    print(f"   Error details: {str(restrict_error)}", flush=True)
                                    if 'Chat not found' in str(restrict_error):
                                        raise
                                    print(f"   Traceback: {traceback.format_exc()}", flush=True)
                        # If subscribed (member, administrator, creator), unmute if action was mute
                        elif member.status in ['member', 'administrator', 'creator']:
                            await asyncio.get_running_loop().run_in_executor(
                                None, _update_subscription_state, current_group_id, user_info['row_id'], tg_id, True, safe_int(settings.reward_points, 0), settings.id
                            )
                            if settings.unsubscribe_action != 'mute':
                                continue
                            # Reuse cached owner check if already done; otherwise look it up
                            if is_owner is None:
                                is_owner = await is_user_chat_owner(bot, chat_id, tg_id)
                            if is_owner:
                                print(f"⏭️ [频道订阅检测] 跳过解除禁言操作 - 用户 {tg_id} 是群主/管理员 (Owner/Admin) in group {settings.group_id} (chat_id={chat_id})", flush=True)
                                continue
                            
                            try:
                                print(f"🔄 [频道订阅检测] 检测到用户 {tg_id} 已订阅频道，准备解除禁言 in group {settings.group_id} (chat_id={chat_id})", flush=True)
                                await bot.restrict_chat_member(
                                    chat_id=chat_id,
                                    user_id=tg_id,
                                    permissions=get_unrestricted_permissions()
                                )
                                print(f"✅ [频道订阅检测] Successfully unmuted subscribed user {tg_id} in group {settings.group_id} (chat_id={chat_id})", flush=True)
                            except Exception as unmute_error:
                                print(f"❌ [频道订阅检测] Failed to unmute user {tg_id} in group {settings.group_id} (chat_id={chat_id})", flush=True)
                                print(f"   Error type: {type(unmute_error).__name__}", flush=True)
                                print(f"   Error details: {str(unmute_error)}", flush=True)
                                if 'Chat not found' in str(unmute_error):
                                    raise
                    except Exception as e:
                        if 'Chat not found' in str(e):
                            print(f"⚠️ [频道订阅检测] Group {settings.group_id} (chat_id={chat_id}) not found, skipping remaining users", flush=True)
                            group_chat_gone = True
                        else:
                            # User may not be in channel or bot doesn't have access
                            print(f"Error checking subscription for user {tg_id}: {e}")
                        
            except Exception as e:
                print(f"Error processing channel subscription for group {settings.group_id}: {e}")
                
    except Exception as e:
        print(f"Error in check_channel_subscriptions: {e}")

async def update_member_levels(context):
    """Background task to update member levels based on points.

    When a user's level changes AND the new level has a non-empty ``permissions``
    JSON object, this task also calls ``restrict_chat_member`` to apply the
    corresponding Telegram ``ChatPermissions``.

    Supported permission keys (all optional, default ``True`` when omitted):
        can_send_messages (bool)  – allow/block sending text messages
        can_send_media    (bool)  – allow/block sending media / stickers / GIFs
        can_add_links     (bool)  – allow/block link previews
        can_invite_users  (bool)  – allow/block inviting new members
        can_pin_messages  (bool)  – allow/block pinning messages
        can_change_info   (bool)  – allow/block changing group info
    """
    if not global_flask_app:
        return
    
    try:
        def _update_levels():
            with global_flask_app.app_context():
                # Get all users with points across ALL groups (main + clone)
                user_points_list = UserPoints.query.join(
                    BotGroup, UserPoints.group_id == BotGroup.id
                ).all()
                
                updated_count = 0
                # Collect (chat_id_str, user_tg_id, perms_str, clone_id) for changed users
                permission_actions = []
                for user_points in user_points_list:
                    # Find the highest level this user qualifies for
                    levels = MemberLevel.query.filter_by(
                        group_id=user_points.group_id
                    ).order_by(MemberLevel.required_points.desc()).all()
                    
                    # Determine the appropriate level for this user
                    new_level = None
                    for level in levels:
                        if user_points.points_balance >= level.required_points:
                            new_level = level
                            break
                    
                    # Only update if the level has changed
                    new_level_id = new_level.id if new_level else None
                    if user_points.current_level_id != new_level_id:
                        user_points.current_level_id = new_level_id
                        updated_count += 1

                        # Collect Telegram permission application if new level defines them
                        perms_str = (new_level.permissions or '{}') if new_level else '{}'
                        if perms_str and perms_str != '{}':
                            group = BotGroup.query.get(user_points.group_id)
                            if group and group.chat_id:
                                permission_actions.append(
                                    (group.chat_id, user_points.user_id, perms_str, group.clone_id)
                                )
                
                db.session.commit()
                return updated_count, permission_actions
        
        count, permission_actions = await asyncio.get_running_loop().run_in_executor(None, _update_levels)
        if count > 0:
            print(f"✅ Updated member levels for {count} users")

        # Apply Telegram ChatPermissions for users whose level changed
        for chat_id_str, user_tg_id, perms_str, group_clone_id in permission_actions:
            try:
                bot = _get_bot_for_clone(group_clone_id, context.bot)
                if bot is None:
                    continue
                perms = json.loads(perms_str)
                # JSON key → Telegram ChatPermissions field mapping.
                # All keys default to True when omitted (permissive default).
                # Admin-facing key names use shorter aliases for readability:
                #   can_send_media  →  can_send_other_messages
                #   can_add_links   →  can_add_web_page_previews
                tg_perms = ChatPermissions(
                    can_send_messages=perms.get('can_send_messages', True),
                    can_send_other_messages=perms.get('can_send_media', True),
                    can_add_web_page_previews=perms.get('can_add_links', True),
                    can_invite_users=perms.get('can_invite_users', True),
                    can_pin_messages=perms.get('can_pin_messages', True),
                    can_change_info=perms.get('can_change_info', True),
                )
                await bot.restrict_chat_member(
                    chat_id=int(chat_id_str),
                    user_id=int(user_tg_id),
                    permissions=tg_perms,
                )
            except Exception as perm_err:
                print(f"⚠️ [等级权限] 无法为用户 {user_tg_id} 设置权限: {perm_err}")
        
    except Exception as e:
        print(f"❌ Error in update_member_levels: {e}")

async def update_lottery_status(context):
    """Update lottery status: pending -> active when start time is reached"""
    if not global_flask_app:
        logging.warning("🎲 [状态更新] global_flask_app 未初始化")
        return
    
    try:
        def _update_pending_lotteries():
            with global_flask_app.app_context():
                # Ensure we get fresh data from the database (not cached)
                db.session.expire_all()
                
                now = get_beijing_now()
                # Find all lotteries that should be active now, across ALL groups (main + clone)
                group_ids = [g.id for g in BotGroup.query.all() if is_plugin_enabled(g.id, 'lottery')]
                
                if not group_ids:
                    return 0
                
                updated = GroupLottery.query.filter(
                    GroupLottery.status == 'pending',
                    GroupLottery.start_time != None,
                    GroupLottery.start_time <= now,
                    GroupLottery.group_id.in_(group_ids)
                ).update(
                    {'status': 'active'},
                    # synchronize_session=False: safe here because we commit immediately
                    # and no caller holds in-memory references to these lottery objects.
                    synchronize_session=False
                )
                
                if updated > 0:
                    db.session.commit()
                    logging.info(f"✅ [状态更新] 已更新 {updated} 个抽奖状态: pending -> active")
                
                return updated
        
        await asyncio.get_running_loop().run_in_executor(None, _update_pending_lotteries)
        
    except Exception as e:
        logging.error(f"❌ [状态更新] update_lottery_status 执行失败: {e}")
        logging.error(traceback.format_exc())

async def run_lottery_draws(context):
    """Background task to run lottery draws when time is up"""
    if not global_flask_app:
        logging.warning("🎲 [抽奖任务] global_flask_app 未初始化")
        return
    
    try:
        def _get_active_lottery_ids():
            with global_flask_app.app_context():
                # Ensure we get fresh data from the database (not cached)
                db.session.expire_all()
                
                now = get_beijing_now()
                # Find lotteries that have ended but not yet drawn, across ALL groups (main + clone)
                # Query both pending and active status to handle edge cases where:
                # 1. Lottery goes from pending directly to end_time without status update task running first
                # 2. Status update task hasn't run yet but end_time has been reached
                # Return only IDs to avoid detached object issues
                lotteries = GroupLottery.query.filter(
                    GroupLottery.status.in_(['pending', 'active']),
                    GroupLottery.end_time != None,
                    GroupLottery.end_time <= now,
                ).all()
                lottery_ids = [lottery.id for lottery in lotteries]
                if lottery_ids:
                    logging.info(f"🎲 [抽奖任务] 发现 {len(lottery_ids)} 个需要开奖的抽奖活动: {lottery_ids}")
                return lottery_ids
        
        lottery_ids = await asyncio.get_running_loop().run_in_executor(None, _get_active_lottery_ids)
        
        for lottery_id in lottery_ids:
            try:
                def _process_lottery(lid):
                    with global_flask_app.app_context():
                        # Ensure we get fresh data from the database (not cached)
                        db.session.expire_all()
                        
                        # Re-fetch lottery in this context to ensure it's attached to the session
                        lottery = GroupLottery.query.get(lid)
                        if not lottery:
                            logging.warning(f"🎲 [抽奖任务] 抽奖 ID {lid} 未找到")
                            return None
                        
                        # Double-check status hasn't changed (race condition protection)
                        if lottery.status == 'ended':
                            logging.info(f"🎲 [抽奖任务] 抽奖 '{lottery.lottery_name}' (ID: {lid}) 已结束，跳过")
                            return None
                        
                        group = BotGroup.query.get(lottery.group_id)
                        if not group:
                            logging.warning(f"🎲 [抽奖任务] 群组 ID {lottery.group_id} 未找到")
                            return None

                        if not is_plugin_enabled(lottery.group_id, 'lottery'):
                            logging.info(f"🎲 [抽奖任务] 群组 {lottery.group_id} 抽奖插件已关闭，跳过 '{lottery.lottery_name}' (ID: {lid})")
                            return None
                        
                        logging.info(f"🎲 [抽奖任务] 开始处理抽奖 '{lottery.lottery_name}' (ID: {lid}, 类型: {lottery.lottery_type}, 群组 ID: {lottery.group_id})")
                        
                        chat_id = int(group.chat_id)
                        winners = []
                        
                        if lottery.lottery_type == 'message_count':
                            # 🆕 Use actual message tracking data
                            # Get participants who sent at least one message
                            # Limit to top MAX_LOTTERY_MESSAGE_COUNT_RECORDS participants for performance in very large groups
                            message_counts = LotteryMessageCount.query.filter_by(
                                lottery_id=lottery.id
                            ).filter(LotteryMessageCount.message_count > 0).order_by(
                                LotteryMessageCount.message_count.desc()
                            ).limit(MAX_LOTTERY_MESSAGE_COUNT_RECORDS).all()
                            
                            logging.info(f"🎲 [抽奖任务] 消息计数抽奖: 找到 {len(message_counts)} 个参与者")
                            
                            if message_counts:
                                # Create weighted random selection based on message counts
                                # Users with more messages have higher chance to win
                                participants = [(mc.user_id, mc.message_count) for mc in message_counts]
                                
                                # Weighted random selection
                                total_messages = sum(count for _, count in participants)
                                if total_messages > 0:
                                    rand_val = random.uniform(0, total_messages)
                                    cumulative = 0
                                    for user_id, count in participants:
                                        cumulative += count
                                        if cumulative >= rand_val:
                                            winners = [user_id]
                                            logging.info(f"🎲 [抽奖任务] 选中获奖者: 用户 {user_id} (消息数: {count})")
                                            break
                            else:
                                # Fallback: if no one sent messages, pick from group users (limited sample)
                                logging.warning(f"🎲 [抽奖任务] 无消息记录，使用随机备选方案")
                                eligible_users = GroupUser.query.filter_by(group_id=group.id).limit(MAX_LOTTERY_PARTICIPANTS).all()
                                if eligible_users:
                                    winner = random.choice(eligible_users)
                                    winners = [winner.tg_id]
                                    logging.info(f"🎲 [抽奖任务] 从备选池中选中获奖者: 用户 {winner.tg_id}")
                        
                        elif lottery.lottery_type == 'message_rank':
                            # 🆕 Use actual message tracking data - top N senders
                            # Optimized: use limit() to avoid loading all records
                            top_n = min(lottery.top_n_winners or 1, MAX_AUCTION_WINNERS)
                            top_senders = LotteryMessageCount.query.filter_by(
                                lottery_id=lottery.id
                            ).order_by(LotteryMessageCount.message_count.desc()).limit(
                                top_n
                            ).all()
                            
                            logging.info(f"🎲 [抽奖任务] 消息排名抽奖: 需要前 {top_n} 名，找到 {len(top_senders)} 个参与者")
                            
                            winners = [mc.user_id for mc in top_senders]
                            
                            if winners:
                                logging.info(f"🎲 [抽奖任务] 选中获奖者: {winners}")
                            else:
                                # Fallback: if no tracking data, use group users (limited sample)
                                logging.warning(f"🎲 [抽奖任务] 无消息记录，使用随机备选方案")
                                top_users = GroupUser.query.filter_by(group_id=group.id).limit(top_n).all()
                                winners = [u.tg_id for u in top_users]
                                if winners:
                                    logging.info(f"🎲 [抽奖任务] 从备选池中选中获奖者: {winners}")

                        elif lottery.lottery_type == 'random':
                            # Pure random draw from all registered group members
                            from app.services.lottery_service import pick_winners_random
                            top_n = min(lottery.top_n_winners or 1, MAX_AUCTION_WINNERS)
                            all_users = GroupUser.query.filter_by(group_id=group.id).all()
                            logging.info(f"🎲 [抽奖任务] 随机抽奖: 共 {len(all_users)} 名群成员，抽取 {top_n} 名")
                            pool = [u.tg_id for u in all_users]
                            winners = pick_winners_random(pool, top_n)
                            if winners:
                                logging.info(f"🎲 [抽奖任务] 随机抽中获奖者: {winners}")
                        
                        # Update lottery with winners
                        lottery.winner_ids = json.dumps(winners)
                        lottery.status = 'ended'
                        db.session.commit()
                        
                        logging.info(f"✅ [抽奖任务] 抽奖 '{lottery.lottery_name}' (ID: {lid}) 已结束，获奖者: {winners}")
                        
                        return {
                            'chat_id': chat_id,
                            'clone_id': group.clone_id,  # which bot to use
                            'lottery_name': lottery.lottery_name,
                            'prize_description': lottery.prize_description,
                            'winners': winners,
                            'announce_channel_id': lottery.announce_channel_id,
                        }
                
                result = await asyncio.get_running_loop().run_in_executor(None, _process_lottery, lottery_id)
                
                # Announce winners (outside the DB context)
                # Only announce if there are actual winners (empty list = no winners)
                if result and result.get('winners'):
                    winner_mentions = [f"<a href='tg://user?id={uid}'>用户{uid}</a>" for uid in result['winners']]
                    message = f"🎉 <b>抽奖结束！</b>\n\n"
                    message += f"活动：{result['lottery_name']}\n"
                    message += f"获奖者：{', '.join(winner_mentions)}\n"
                    if result['prize_description']:
                        message += f"奖品：{result['prize_description']}\n"
                    
                    bot = _get_bot_for_clone(result.get('clone_id'), context.bot)
                    if bot is None:
                        logging.warning(f"⏭️ [抽奖任务] 跳过公告：对应克隆机器人未运行 (群组: {result['chat_id']})")
                    else:
                        announce_targets = [result['chat_id']]
                        if result.get('announce_channel_id') and result['announce_channel_id'] not in announce_targets:
                            announce_targets.append(result['announce_channel_id'])
                        for target_chat_id in announce_targets:
                            try:
                                await bot.send_message(
                                    chat_id=target_chat_id,
                                    text=message,
                                    parse_mode='HTML'
                                )
                                logging.info(f"📢 [抽奖任务] 成功在 {target_chat_id} 发送获奖公告")
                            except Exception as send_error:
                                logging.error(f"❌ [抽奖任务] 发送获奖公告失败 ({target_chat_id}): {send_error}")
                        
            except Exception as e:
                logging.error(f"❌ [抽奖任务] 处理抽奖 {lottery_id} 时出错: {e}")
                logging.error(traceback.format_exc())
                
    except Exception as e:
        logging.error(f"❌ [抽奖任务] run_lottery_draws 执行失败: {e}")
        logging.error(traceback.format_exc())


async def check_inactive_users(context):
    """检查并处理不活跃用户 - Background task"""
    if not global_flask_app: return
    try:
        def _check_inactive():
            """Sync part: query database and collect users for moderation actions"""
            with global_flask_app.app_context():
                try:
                    # Get all groups with inactive user settings enabled
                    settings_list = InactiveUserSettings.query.filter_by(enabled=True).all()
                    
                    users_to_mute = []
                    
                    for settings in settings_list:
                        try:
                            group = BotGroup.query.get(settings.group_id)
                            if not group or not group.is_active:
                                continue
                            
                            # Convert chat_id to integer for Telegram API
                            chat_id_int = convert_chat_id_to_int(group.chat_id, group.id)
                            if chat_id_int is None:
                                continue
                            
                            # Calculate threshold date
                            threshold_date = datetime.now() - timedelta(days=settings.inactivity_days)
                            
                            # Find inactive users
                            inactive_users = GroupUser.query.filter(
                                GroupUser.group_id == settings.group_id,
                                GroupUser.last_activity < threshold_date,
                                GroupUser.is_banned == False
                            ).limit(EXPIRED_USERS_BATCH_SIZE).all()  # Process in batches
                            
                            for user in inactive_users:
                                # Take action based on settings
                                if settings.action_type == 'kick':
                                    users_to_mute.append({
                                        'action': 'kick',
                                        'chat_id': chat_id_int,
                                        'user_id': user.tg_id,
                                        'group_id': settings.group_id,
                                        'user_db_id': user.id,
                                        'clone_id': group.clone_id,  # which bot to use
                                    })
                                elif settings.action_type == 'ban':
                                    users_to_mute.append({
                                        'action': 'ban',
                                        'chat_id': chat_id_int,
                                        'user_id': user.tg_id,
                                        'group_id': settings.group_id,
                                        'user_db_id': user.id,
                                        'clone_id': group.clone_id,
                                    })
                                elif settings.action_type == 'mute':
                                    users_to_mute.append({
                                        'action': 'mute',
                                        'chat_id': chat_id_int,
                                        'user_id': user.tg_id,
                                        'group_id': settings.group_id,
                                        'user_db_id': user.id,
                                        'clone_id': group.clone_id,
                                    })
                                elif settings.action_type == 'mute_permanent':
                                    users_to_mute.append({
                                        'action': 'mute_permanent',
                                        'chat_id': chat_id_int,
                                        'user_id': user.tg_id,
                                        'group_id': settings.group_id,
                                        'user_db_id': user.id,
                                        'clone_id': group.clone_id,
                                    })
                                    
                        except Exception as e:
                            print(f"Error processing group {settings.group_id}: {e}")
                            continue
                    
                    return users_to_mute
                    
                except Exception as e:
                    print(f"Error in _check_inactive: {e}")
                    return []
        
        # Execute sync part in executor
        users_to_mute = await asyncio.get_running_loop().run_in_executor(None, _check_inactive)
        
        # Now handle async operations
        if users_to_mute:
            for item in users_to_mute:
                try:
                    bot = _get_bot_for_clone(item.get('clone_id'), context.bot)
                    if bot is None:
                        print(f"⏭️ [不活跃用户检测] 跳过群组 {item['group_id']}：对应克隆机器人未运行", flush=True)
                        continue
                    if item['action'] == 'kick':
                        await bot.ban_chat_member(item['chat_id'], item['user_id'])
                        await bot.unban_chat_member(item['chat_id'], item['user_id'])
                    elif item['action'] == 'ban':
                        await bot.ban_chat_member(item['chat_id'], item['user_id'])
                        # Update database only after successful API call
                        def _update_banned_status():
                            with global_flask_app.app_context():
                                try:
                                    user = GroupUser.query.get(item['user_db_id'])
                                    if user:
                                        user.is_banned = True
                                        db.session.commit()
                                except Exception as e:
                                    print(f"Error updating is_banned for user {item['user_id']}: {e}")
                                    db.session.rollback()
                        await asyncio.get_running_loop().run_in_executor(None, _update_banned_status)
                    elif item['action'] == 'mute':
                        try:
                            print(f"🔄 [不活跃用户检测] 准备禁言不活跃用户 {item['user_id']} in group {item['group_id']} (chat_id={item['chat_id']})", flush=True)
                            await bot.restrict_chat_member(
                                chat_id=item['chat_id'],
                                user_id=item['user_id'],
                                permissions=get_muted_permissions()
                            )
                            print(f"✅ [不活跃用户检测] Successfully called restrict_chat_member API - Muted inactive user {item['user_id']} in group {item['group_id']} (chat_id={item['chat_id']})", flush=True)
                        except Exception as restrict_error:
                            print(f"❌ [不活跃用户检测] restrict_chat_member API failed for user {item['user_id']} in group {item['group_id']} (chat_id={item['chat_id']})", flush=True)
                            print(f"   Error type: {type(restrict_error).__name__}", flush=True)
                            print(f"   Error details: {str(restrict_error)}", flush=True)
                            print(f"   Traceback: {traceback.format_exc()}", flush=True)
                    elif item['action'] == 'mute_permanent':
                        try:
                            print(f"🔄 [不活跃用户检测] 准备永久禁言不活跃用户 {item['user_id']} in group {item['group_id']} (chat_id={item['chat_id']})", flush=True)
                            await bot.restrict_chat_member(
                                chat_id=item['chat_id'],
                                user_id=item['user_id'],
                                permissions=get_muted_permissions()
                            )
                            # Mark as permanently muted in database
                            def _mark_permanent_mute():
                                with global_flask_app.app_context():
                                    try:
                                        user = GroupUser.query.get(item['user_db_id'])
                                        if user:
                                            user.is_muted_permanent = True
                                            user.mute_reason = MUTE_REASON_INACTIVE
                                            db.session.commit()
                                    except Exception as e:
                                        print(f"Error marking permanent mute for user {item['user_id']}: {e}")
                                        db.session.rollback()
                            await asyncio.get_running_loop().run_in_executor(None, _mark_permanent_mute)
                            print(f"✅ [不活跃用户检测] Successfully permanently muted inactive user {item['user_id']} in group {item['group_id']} (chat_id={item['chat_id']})", flush=True)
                        except Exception as restrict_error:
                            print(f"❌ [不活跃用户检测] Permanent mute failed for user {item['user_id']} in group {item['group_id']} (chat_id={item['chat_id']})", flush=True)
                            print(f"   Error type: {type(restrict_error).__name__}", flush=True)
                            print(f"   Error details: {str(restrict_error)}", flush=True)
                except Exception as e:
                    print(f"Error executing action for user {item['user_id']}: {e}")
                    continue
                    
    except Exception as e:
        print(f"Error in check_inactive_users: {e}")


async def track_message_statistics(update: Update, context):
    """跟踪消息统计 - Called from on_message"""
    if not global_flask_app: return
    try:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        
        if not user or chat.type not in ['group', 'supergroup']:
            return
        
        def _track_stats():
            with global_flask_app.app_context():
                group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                if not group:
                    return
                
                today = datetime.now().date()
                
                # Use upsert-like pattern to avoid race conditions
                stats = MessageStatistics.query.filter_by(
                    group_id=group.id,
                    user_id=user.id,
                    date=today
                ).with_for_update().first()
                
                if not stats:
                    stats = MessageStatistics(
                        group_id=group.id,
                        user_id=user.id,
                        date=today,
                        message_count=0,
                        text_count=0,
                        photo_count=0,
                        video_count=0,
                        sticker_count=0,
                        document_count=0,
                        voice_count=0
                    )
                    db.session.add(stats)
                    try:
                        db.session.flush()
                    except:
                        # Another thread created it, re-fetch
                        db.session.rollback()
                        stats = MessageStatistics.query.filter_by(
                            group_id=group.id,
                            user_id=user.id,
                            date=today
                        ).first()
                        if not stats:
                            return
                
                # Update counts
                stats.message_count += 1
                
                if msg.text:
                    stats.text_count += 1
                elif msg.photo:
                    stats.photo_count += 1
                elif msg.video:
                    stats.video_count += 1
                elif msg.sticker:
                    stats.sticker_count += 1
                elif msg.document:
                    stats.document_count += 1
                elif msg.voice:
                    stats.voice_count += 1
                
                db.session.commit()
        
        await asyncio.get_running_loop().run_in_executor(None, _track_stats)
    except Exception as e:
        print(f"Error tracking message statistics: {e}")


async def check_keyword_filter(update: Update, context):
    """检查关键词过滤 - Called from on_message, returns True if message should be deleted"""
    if not global_flask_app: return False
    try:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        
        from app.services.channel_automation import message_filter_text
        filter_text = message_filter_text(msg)
        if not filter_text or chat.type not in ['group', 'supergroup']:
            return False
        
        def _check_filters():
            with global_flask_app.app_context():
                from app.services.keyword_filter_service import select_filter_to_enforce

                group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                if not group:
                    return None

                # Plugin gate: spam covers keyword filter (default-on when no settings row)
                if not is_plugin_enabled(group.id, 'spam'):
                    return None

                # Get active keyword filters
                filters_list = KeywordFilter.query.filter_by(
                    group_id=group.id,
                    is_active=True
                ).all()

                # Whitelist gate first (if any whitelist rules), then blacklist.
                # See app.services.keyword_filter_service module docstring.
                return select_filter_to_enforce(filter_text, filters_list)
        
        matched_filter = await asyncio.get_running_loop().run_in_executor(None, _check_filters)
        
        if matched_filter:
            # Take action based on filter settings
            if matched_filter.action == 'delete':
                await msg.delete()
                return True
            elif matched_filter.action == 'warn':
                warn_count = 0
                def _persist_kw_warn():
                    from app.services.warning_service import record_warning
                    with global_flask_app.app_context():
                        group = BotGroup.query.filter_by(
                            chat_id=str(chat.id),
                            clone_id=context.application.bot_data.get('clone_id'),
                        ).first()
                        if not group:
                            return 0
                        return record_warning(
                            group.id,
                            user.id,
                            admin_id=None,
                            reason='关键词过滤',
                            source='keyword',
                        )
                try:
                    warn_count = await asyncio.get_running_loop().run_in_executor(None, _persist_kw_warn)
                except Exception as e:
                    print(f"Error persisting keyword warning: {e}")
                    warn_count = 0
                from app.services.warning_service import format_keyword_warn_reply
                await msg.reply_text(format_keyword_warn_reply(warn_count or 1))
            elif matched_filter.action == 'mute':
                try:
                    print(f"🔄 [内容过滤] 准备禁言触发关键词的用户 {user.id} in group {chat.id}", flush=True)
                    await context.bot.restrict_chat_member(
                        chat_id=chat.id,
                        user_id=user.id,
                        permissions=get_muted_permissions(),
                        until_date=datetime.now() + timedelta(minutes=10)
                    )
                    print(f"✅ [内容过滤] Successfully called restrict_chat_member API - Muted user {user.id} for 10 minutes in group {chat.id}", flush=True)
                except Exception as restrict_error:
                    print(f"❌ [内容过滤] restrict_chat_member API failed for user {user.id} in group {chat.id}", flush=True)
                    print(f"   Error type: {type(restrict_error).__name__}", flush=True)
                    print(f"   Error details: {str(restrict_error)}", flush=True)
                    print(f"   Traceback: {traceback.format_exc()}", flush=True)
                await msg.delete()
                return True
            elif matched_filter.action == 'kick':
                await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
                await context.bot.unban_chat_member(chat_id=chat.id, user_id=user.id)
                await msg.delete()
                return True
            elif matched_filter.action == 'ban':
                await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
                await msg.delete()
                return True
        
        return False
    except Exception as e:
        print(f"Error in check_keyword_filter: {e}")
        return False


async def update_user_activity(update: Update, context):
    """更新用户最后活动时间 - Called from on_message"""
    if not global_flask_app: return
    try:
        chat = update.effective_chat
        user = update.effective_user
        
        if not user or chat.type not in ['group', 'supergroup']:
            return
        
        def _update_activity():
            with global_flask_app.app_context():
                group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                if not group:
                    return
                
                group_user = GroupUser.query.filter_by(
                    group_id=group.id,
                    tg_id=user.id
                ).first()
                
                if group_user:
                    group_user.last_activity = datetime.now()
                    # Persist Telegram username so @username lookups work reliably
                    if user.username:
                        try:
                            pd = json.loads(group_user.profile_data or '{}')
                            if pd.get('tg_username') != user.username:
                                pd['tg_username'] = user.username
                                group_user.profile_data = json.dumps(pd, ensure_ascii=False)
                        except Exception:
                            pass
                    db.session.commit()
        
        await asyncio.get_running_loop().run_in_executor(None, _update_activity)
    except Exception as e:
        print(f"Error updating user activity: {e}")


async def check_and_mute_expired_user(update: Update, context):
    """检查并立即禁言过期用户 - Called from on_message
    
    当认证用户在群内发消息时，即时检查其认证状态。
    如果已过期且未被禁言，则立即自动禁言。
    """
    if not global_flask_app:
        return
    
    try:
        chat = update.effective_chat
        user = update.effective_user
        
        # 只处理群组消息
        if not user or chat.type not in ['group', 'supergroup']:
            return
        
        # 排除机器人自身
        if user.is_bot:
            return
        
        # 检查是否为管理员，管理员不受限制
        is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
        if is_admin:
            return
        
        # 同步检查数据库并准备禁言操作
        def _check_expiration():
            with global_flask_app.app_context():
                try:
                    # 获取群组信息
                    group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                    if not group or not group.is_active:
                        return None
                    
                    # 查找用户记录
                    group_user = GroupUser.query.filter_by(
                        group_id=group.id,
                        tg_id=user.id
                    ).first()
                    
                    # 如果用户没有记录，或者没有设置过期时间，则不处理
                    if not group_user or not group_user.expiration_date:
                        return None
                    
                    # 如果用户已经被禁言，仍然尝试调用API（补救机制）
                    # 因为之前的API调用可能失败了，但数据库已标记为banned
                    # 这样可以确保用户真正被禁言
                    
                    # 检查是否过期
                    now = get_beijing_now()
                    if group_user.expiration_date >= now:
                        # 未过期，无需处理
                        return None
                    
                    # 用户已过期，需要立即禁言
                    # ✅ NEW: 不再先更新数据库，而是在API成功后更新
                    
                    # 获取配置信息用于通知消息
                    conf = get_group_conf(group)
                    ban_msg = conf.get('msg_expired_ban', '⛔️ <b>您的认证已过期，已被暂时禁言。请联系管理员续费。</b>')
                    
                    # Extract scalar values to avoid detached instance issues
                    return {
                        'group_chat_id': group.chat_id,
                        'group_id': group.id,
                        'group_user_id': group_user.id,  # Store group_user.id to update database later
                        'group_title': group.title,
                        'user_id': user.id,
                        'ban_msg': ban_msg
                    }
                    
                except Exception as e:
                    print(f"Error in _check_expiration: {e}")
                    db.session.rollback()
                    return None
        
        # 在线程池中执行数据库操作
        result = await asyncio.get_running_loop().run_in_executor(None, _check_expiration)
        
        # 如果不需要禁言，直接返回
        if not result:
            return
        
        print(f"🚀 [群消息触发] User {user.id} in group {chat.id} needs to be muted (expired)", flush=True)
        
        # Helper function to mark user as banned in database after successful API call
        def _mark_user_banned_in_db(group_user_id):
            """Mark user as banned in database - called after successful API mute"""
            with global_flask_app.app_context():
                try:
                    group_user = GroupUser.query.get(group_user_id)
                    if group_user:
                        group_user.is_banned = True
                        db.session.commit()
                        return True
                except Exception as e:
                    print(f"❌ [群消息触发] Failed to mark user {group_user_id} as banned in DB: {e}", flush=True)
                    db.session.rollback()
                    return False
            return False
        
        # 执行禁言操作
        try:
            group_chat_id = result['group_chat_id']
            group_id = result['group_id']
            group_user_id = result['group_user_id']
            group_title = result['group_title']
            user_id = result['user_id']
            ban_msg = result['ban_msg']
            
            # 转换 chat_id 为整数
            chat_id_int = convert_chat_id_to_int(group_chat_id, group_id, group_title)
            if chat_id_int is None:
                print(f"❌ [群消息触发] Failed to convert chat_id for group {group_id} ({group_title}), chat_id={group_chat_id}", flush=True)
                return
            
            print(f"🔧 [群消息触发] About to call restrict_chat_member for user {user_id} in group {group_title}", flush=True)
            
            # 禁言用户
            try:
                await context.bot.restrict_chat_member(
                    chat_id=chat_id_int,
                    user_id=user_id,
                    permissions=get_muted_permissions()
                )
                print(f"✅ [群消息触发] Successfully called restrict_chat_member API - Instantly muted expired user {user_id} in group {group_title} (chat_id={chat_id_int})", flush=True)
                
                # ✅ NEW: Only mark as banned in DB after successful API call
                success = await asyncio.get_running_loop().run_in_executor(None, _mark_user_banned_in_db, group_user_id)
                if success:
                    print(f"✅ [群消息触发] Marked user {user_id} as banned in database after successful API call", flush=True)
                else:
                    print(f"⚠️  [群消息触发] API succeeded but failed to update database for user {user_id}", flush=True)
                    
            except Exception as restrict_error:
                # 详细记录 restrict_chat_member API 调用失败的错误
                print(f"❌ [群消息触发] restrict_chat_member API failed for user {user_id} in group {group_title} (chat_id={chat_id_int})", flush=True)
                print(f"   Error type: {type(restrict_error).__name__}", flush=True)
                print(f"   Error details: {str(restrict_error)}", flush=True)
                print(f"   Traceback: {traceback.format_exc()}", flush=True)
                print(f"   ⚠️  Database NOT updated - user will be retried on next message or scheduled check", flush=True)
                # Don't re-raise to avoid duplicate logging in outer exception handler
                return
            
            # 尝试发送私信通知（不阻塞主流程）
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=ban_msg,
                    parse_mode='HTML'
                )
                print(f"✅ [群消息触发] Sent ban notification to user {user_id}", flush=True)
            except Exception as e:
                # 私信失败不影响主流程（用户可能未与机器人对话）
                print(f"⚠️  [群消息触发] Could not send DM to user {user_id}: {e}", flush=True)
                
        except Exception as e:
            print(f"❌ [群消息触发] Unexpected error muting expired user {user_id}", flush=True)
            print(f"   Error: {type(e).__name__}: {str(e)}", flush=True)
            print(f"   Full traceback: {traceback.format_exc()}", flush=True)
            
    except Exception as e:
        print(f"Error in check_and_mute_expired_user: {e}")


async def cmd_vote(update: Update, context):
    """创建投票命令 /vote 标题|选项1|选项2|..."""
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'games'):
        return
    
    is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
    if not is_admin:
        _sched_del(context, await update.message.reply_text("❌ 只有管理员才能创建投票"), 20)
        return
    
    # Parse vote content
    if not context.args:
        _sched_del(context, await update.message.reply_text(
            "📊 <b>创建投票</b>\n\n"
            "格式：/vote 标题|选项1|选项2|选项3...\n\n"
            "示例：/vote 今天吃什么|火锅|烧烤|快餐|自助餐",
            parse_mode='HTML'
        ), 20)
        return
    
    content = ' '.join(context.args)
    parts = content.split('|')
    
    if len(parts) < 3:
        _sched_del(context, await update.message.reply_text("❌ 至少需要标题和两个选项"), 20)
        return
    
    title = parts[0].strip()
    options = [opt.strip() for opt in parts[1:] if opt.strip()]
    
    if len(options) < 2:
        _sched_del(context, await update.message.reply_text("❌ 至少需要两个选项"), 20)
        return
    
    if len(options) > 10:
        _sched_del(context, await update.message.reply_text("❌ 选项最多10个"), 20)
        return
    
    if not global_flask_app:
        return
    
    def _create_vote():
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return None, "群组不存在"
            
            # Create vote
            vote = GroupVote(
                group_id=group.id,
                title=title,
                options=json.dumps(options, ensure_ascii=False),
                vote_type='single',
                max_choices=1,
                is_anonymous=False,
                allow_revote=True,
                start_time=get_beijing_now(),
                end_time=get_beijing_now() + timedelta(days=7),  # Default 7 days
                status='active',
                created_by=user.id
            )
            db.session.add(vote)
            db.session.commit()
            return vote.id, None
    
    vote_id, error = await asyncio.get_running_loop().run_in_executor(None, _create_vote)
    
    if error:
        _sched_del(context, await update.message.reply_text(f"❌ {error}"), 20)
        return
    
    # Create inline keyboard with vote options
    buttons = []
    for idx, option in enumerate(options):
        buttons.append([InlineKeyboardButton(
            f"{option}",
            callback_data=f"vote_{vote_id}_{idx}"
        )])
    
    # Add result button
    buttons.append([InlineKeyboardButton(
        "📊 查看结果",
        callback_data=f"vote_result_{vote_id}"
    )])
    
    keyboard = InlineKeyboardMarkup(buttons)
    
    message = await update.message.reply_text(
        f"📊 <b>投票：{title}</b>\n\n"
        f"👆 点击下方按钮投票\n"
        f"⏰ 截止时间：7天后\n"
        f"✅ 允许改投",
        reply_markup=keyboard,
        parse_mode='HTML'
    )
    
    # Update vote with message_id
    def _update_message_id():
        with global_flask_app.app_context():
            vote = GroupVote.query.get(vote_id)
            if vote:
                vote.message_id = message.message_id
                db.session.commit()
    
    await asyncio.get_running_loop().run_in_executor(None, _update_message_id)


async def cmd_quiz(update: Update, context):
    """启动问答游戏命令 /quiz"""
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'games'):
        return
    
    if not global_flask_app:
        return
    
    def _get_random_quiz():
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return None
            
            # Get a random active quiz
            quiz = QuizGame.query.filter_by(
                group_id=group.id,
                is_active=True
            ).order_by(db.func.random()).first()
            
            if quiz:
                return {
                    'id': quiz.id,
                    'question': quiz.question,
                    'answers': json.loads(quiz.answers),
                    'time_limit': quiz.time_limit,
                    'points_reward': quiz.points_reward
                }
            return None
    
    quiz_data = await asyncio.get_running_loop().run_in_executor(None, _get_random_quiz)
    
    if not quiz_data:
        _sched_del(context, await update.message.reply_text("❌ 暂无可用的问答题目"), 20)
        return
    
    # Create quiz session and send question
    buttons = []
    for idx, answer in enumerate(quiz_data['answers']):
        buttons.append([InlineKeyboardButton(
            f"{chr(65+idx)}. {answer}",
            callback_data=f"quiz_answer_{quiz_data['id']}_{idx}"
        )])
    
    keyboard = InlineKeyboardMarkup(buttons)
    
    message = await update.message.reply_text(
        f"🎯 <b>问答题</b>\n\n"
        f"{quiz_data['question']}\n\n"
        f"⏱ 时限：{quiz_data['time_limit']}秒\n"
        f"🎁 奖励：{quiz_data['points_reward']}积分",
        reply_markup=keyboard,
        parse_mode='HTML'
    )
    
    # Create quiz session in database
    def _create_session():
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if group:
                session = QuizSession(
                    group_id=group.id,
                    quiz_id=quiz_data['id'],
                    message_id=message.message_id,
                    start_time=datetime.now(),
                    status='active'
                )
                db.session.add(session)
                db.session.commit()
    
    await asyncio.get_running_loop().run_in_executor(None, _create_session)


async def cmd_redpacket(update: Update, context):
    """发红包命令 /redpacket 总积分 数量 [祝福语]"""
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'games'):
        return

    if not global_flask_app:
        return

    # 先读取群组配置，用于参数校验
    def _load_group_conf():
        with global_flask_app.app_context():
            grp = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not grp:
                return None, None
            return grp.id, get_group_conf(grp)

    group_db_id, grp_conf = await asyncio.get_running_loop().run_in_executor(None, _load_group_conf)
    if group_db_id is None:
        _sched_del(context, await update.message.reply_text("❌ 群组不存在"), 20)
        return

    # 从群组配置中读取限制
    rp_enabled = grp_conf.get('red_packet_enabled', True)
    max_count = int(grp_conf.get('red_packet_max_count', 50))
    min_total = int(grp_conf.get('red_packet_min_total', 1))
    max_total = int(grp_conf.get('red_packet_max_total', 0))
    expire_hours = int(grp_conf.get('red_packet_expire_hours', 24))

    if not rp_enabled:
        _sched_del(context, await update.message.reply_text("❌ 该群已关闭红包功能"), 20)
        return

    # 解析参数
    try:
        args = context.args
        if len(args) < 2:
            _sched_del(context, await update.message.reply_text(
                "🧧 <b>发红包</b>\n\n"
                "格式：/redpacket 总积分 红包数量 [祝福语]\n\n"
                "示例：\n"
                "/redpacket 100 10 新年快乐\n"
                "/redpacket 200 5",
                parse_mode='HTML'
            ), 20)
            return
        
        total_points = int(args[0])
        packet_count = int(args[1])
        message = ' '.join(args[2:]) if len(args) > 2 else "恭喜发财 🧧"
        
        if total_points < packet_count:
            _sched_del(context, await update.message.reply_text("❌ 总积分不能少于红包数量"), 20)
            return
        
        if packet_count < 1 or packet_count > max_count:
            _sched_del(context, await update.message.reply_text(f"❌ 红包数量需要在1-{max_count}之间"), 20)
            return
        
        if total_points < min_total:
            _sched_del(context, await update.message.reply_text(f"❌ 总积分不能少于 {min_total}"), 20)
            return

        if max_total > 0 and total_points > max_total:
            _sched_del(context, await update.message.reply_text(f"❌ 总积分不能超过 {max_total}"), 20)
            return
        
    except ValueError:
        _sched_del(context, await update.message.reply_text("❌ 参数格式错误，请输入数字"), 20)
        return
    
    def _create_redpacket():
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return None, "群组不存在"
            
            # 检查用户积分
            user_points = UserPoints.query.filter_by(
                group_id=group.id,
                user_id=user.id
            ).first()
            
            if not user_points or user_points.points_balance < total_points:
                current_balance = user_points.points_balance if user_points else 0
                return None, f"积分不足！需要 {total_points} 积分，当前 {current_balance} 积分"
            
            # 扣除积分
            user_points.points_balance -= total_points
            
            # 创建红包
            packet = RedPacket(
                group_id=group.id,
                creator_id=user.id,
                packet_type='random',
                total_points=total_points,
                packet_count=packet_count,
                remaining_count=packet_count,
                remaining_points=total_points,
                message=message,
                expire_time=get_beijing_now() + timedelta(hours=expire_hours),
                status='active'
            )
            db.session.add(packet)
            
            # 记录积分日志
            log = PointsLog(
                group_id=group.id,
                user_id=user.id,
                points_change=-total_points,
                reason="发红包",
                balance_after=user_points.points_balance
            )
            db.session.add(log)
            
            db.session.commit()
            return packet.id, None
    
    packet_id, error = await asyncio.get_running_loop().run_in_executor(None, _create_redpacket)
    
    if error:
        _sched_del(context, await update.message.reply_text(f"❌ {error}"), 20)
        return
    
    # 发送红包消息
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🧧 领取红包", callback_data=f"redpacket_claim_{packet_id}")
    ]])
    
    await update.message.reply_text(
        f"🧧 <b>红包来啦！</b>\n\n"
        f"💬 {message}\n"
        f"💰 共 {total_points} 积分\n"
        f"🎁 {packet_count} 个红包\n"
        f"⏰ {expire_hours}小时内有效\n\n"
        f"👆 点击按钮领取",
        reply_markup=keyboard,
        parse_mode='HTML'
    )


async def cmd_points(update: Update, context):
    """查询自己积分余额命令 /points"""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'points'):
        return

    if not global_flask_app:
        return

    def _get_balance():
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(
                chat_id=str(chat.id),
                clone_id=context.application.bot_data.get('clone_id')
            ).first()
            if not group:
                return None, "群组不存在"

            user_points = UserPoints.query.filter_by(
                group_id=group.id,
                user_id=user.id
            ).first()
            balance = user_points.points_balance if user_points else 0

            # Determine member level
            level_text = ""
            levels = MemberLevel.query.filter_by(
                group_id=group.id
            ).order_by(MemberLevel.required_points.desc()).all()
            for level in levels:
                if balance >= level.required_points:
                    badge = level.badge_emoji or "🎖️"
                    level_text = f"{badge} {level.level_name}"
                    break

            # Today's earnings
            today_start = get_beijing_today()
            earned_today = db.session.query(
                func.coalesce(func.sum(PointsLog.points_change), 0)
            ).filter(
                PointsLog.group_id == group.id,
                PointsLog.user_id == user.id,
                PointsLog.points_change > 0,
                PointsLog.created_at >= today_start
            ).scalar() or 0

            # Compute rank among all members
            rank = UserPoints.query.filter_by(
                group_id=group.id
            ).filter(
                UserPoints.points_balance > balance
            ).count() + 1

            return {
                'balance': balance,
                'level_text': level_text,
                'earned_today': earned_today,
                'rank': rank,
            }, None

    data, error = await asyncio.get_running_loop().run_in_executor(None, _get_balance)

    if error:
        _sched_del(context, await update.message.reply_text(f"❌ {error}"), 20)
        return

    display_name = user.first_name or f"User_{user.id}"
    lines = [f"💰 <b>{display_name} 的积分</b>"]
    lines.append(f"💎 当前余额：<b>{data['balance']}</b> 积分")
    if data['level_text']:
        lines.append(f"🏅 当前等级：{data['level_text']}")
    lines.append(f"📈 今日获得：{data['earned_today']} 积分")
    lines.append(f"🏆 排名：第 {data['rank']} 名")
    _sched_del(context, await update.message.reply_html("\n".join(lines)), 90)


async def cmd_transfer(update: Update, context):
    """积分转让命令 /transfer @用户 数量 [备注]"""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'points'):
        return

    # Must be used as a reply to the recipient's message
    if not update.message.reply_to_message:
        _sched_del(context, await update.message.reply_html(
            "💸 <b>积分转让</b>\n\n"
            "用法：回复目标用户的消息，然后发送：\n"
            "<code>/transfer 数量 [备注]</code>\n\n"
            "示例：\n"
            "<code>/transfer 50</code>\n"
            "<code>/transfer 100 感谢帮忙</code>"
        ), 20)
        return

    recipient = update.message.reply_to_message.from_user
    if recipient is None or recipient.is_bot:
        _sched_del(context, await update.message.reply_text("❌ 无法向机器人转让积分"), 20)
        return

    if recipient.id == user.id:
        _sched_del(context, await update.message.reply_text("❌ 不能向自己转让积分"), 20)
        return

    # Parse arguments: /transfer <amount> [note...]
    try:
        args = context.args
        if not args:
            _sched_del(context, await update.message.reply_html(
                "❌ 请指定转让积分数量。\n用法：<code>/transfer 数量 [备注]</code>"
            ), 20)
            return
        amount = int(args[0])
        if amount <= 0:
            raise ValueError
        note = ' '.join(args[1:]) if len(args) > 1 else ""
    except (ValueError, IndexError):
        _sched_del(context, await update.message.reply_text("❌ 积分数量必须是正整数"), 20)
        return

    if not global_flask_app:
        return

    def _do_transfer():
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(
                chat_id=str(chat.id),
                clone_id=context.application.bot_data.get('clone_id')
            ).first()
            if not group:
                return None, "群组不存在"

            # Check sender's balance
            sender_points = UserPoints.query.filter_by(
                group_id=group.id,
                user_id=user.id
            ).first()
            sender_balance = sender_points.points_balance if sender_points else 0

            if sender_balance < amount:
                return None, f"积分不足。您当前有 {sender_balance} 积分，无法转让 {amount} 积分"

            # Deduct from sender
            if not sender_points:
                sender_points = UserPoints(group_id=group.id, user_id=user.id, points_balance=0)
                db.session.add(sender_points)
            sender_points.points_balance -= amount

            # Credit recipient
            recipient_points = UserPoints.query.filter_by(
                group_id=group.id,
                user_id=recipient.id
            ).first()
            if not recipient_points:
                recipient_points = UserPoints(
                    group_id=group.id, user_id=recipient.id, points_balance=0
                )
                db.session.add(recipient_points)
            recipient_points.points_balance += amount

            reason_suffix = f"（{note}）" if note else ""
            recipient_name = recipient.first_name or f"User_{recipient.id}"
            sender_name = user.first_name or f"User_{user.id}"

            # Log both sides
            db.session.add(PointsLog(
                group_id=group.id,
                user_id=user.id,
                points_change=-amount,
                reason=f"转让给 {recipient_name}{reason_suffix}",
                balance_after=sender_points.points_balance,
            ))
            db.session.add(PointsLog(
                group_id=group.id,
                user_id=recipient.id,
                points_change=amount,
                reason=f"收到来自 {sender_name} 的转让{reason_suffix}",
                balance_after=recipient_points.points_balance,
            ))

            db.session.commit()
            return {
                'sender_balance': sender_points.points_balance,
                'recipient_balance': recipient_points.points_balance,
            }, None

    result, error = await asyncio.get_running_loop().run_in_executor(None, _do_transfer)

    if error:
        _sched_del(context, await update.message.reply_text(f"❌ {error}"), 20)
        return

    recipient_name = recipient.first_name or f"User_{recipient.id}"
    _sched_del(context, await update.message.reply_html(
        f"✅ <b>积分转让成功！</b>\n\n"
        f"💸 转让 <b>{amount}</b> 积分给 {recipient_name}\n"
        f"💰 您的余额：<b>{result['sender_balance']}</b> 积分"
    ), 90)


# NOTE: cmd_rank lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.


# NOTE: cmd_invite_rank lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.


async def cmd_mylink(update: Update, context):
    """获取专属邀请链接 /mylink"""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'invitation'):
        return

    if not global_flask_app:
        return

    def _get_invitation_info():
        with global_flask_app.app_context():
            clone_id = context.application.bot_data.get('clone_id')
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=clone_id).first()
            if not group:
                return None, None
            activity = InvitationActivity.query.filter_by(group_id=group.id, enabled=True).first()
            if not activity:
                return None, None
            return group.id, activity.reward_points

    group_id, reward_points = await asyncio.get_running_loop().run_in_executor(None, _get_invitation_info)

    if not group_id:
        _sched_del(context, await update.message.reply_text("❌ 该群组未开启邀请活动，无法生成专属链接"), 20)
        return

    bot_me = await context.bot.get_me()
    bot_username = bot_me.username
    invite_link = f"https://t.me/{bot_username}?start=inv_{user.id}_{group_id}"

    msg = (
        f"🔗 <b>您的专属邀请链接</b>\n\n"
        f"<code>{invite_link}</code>\n\n"
        f"📌 将此链接分享给好友，当好友通过链接加入群组后，"
        f"您将获得 <b>{reward_points} 积分</b> 奖励！"
    )
    _sched_del(context, await update.message.reply_html(msg), 90)


# NOTE: cmd_active lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.


# ---------------------------------------------------------------------------
# /help — show available commands
# ---------------------------------------------------------------------------

async def cmd_help(update: Update, context):
    """显示帮助信息 /help"""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type in ['group', 'supergroup']:
        is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)

        lines = [
            "📋 <b>命令帮助</b>\n",
            "👤 <b>用户命令</b>",
            "• /points — 查看我的积分和等级",
            "• /rank — 积分排行榜",
            "• /active — 活跃用户排行榜",
            "• /checkin — 每日打卡",
            "• /signin — 每日签到获取积分",
            "• /mylink — 获取专属邀请链接",
            "• /invite_rank — 邀请排行榜",
            "• /lottery — 查看进行中的抽奖",
            "• /transfer @用户 数量 — 积分转让",
            "• /menu — 显示群组菜单",
        ]

        if is_admin:
            lines.extend([
                "",
                "🔧 <b>管理员命令</b>（回复目标消息使用）",
                "• /kick — 踢出用户",
                "• /ban — 永久封禁用户",
                "• /unban — 解除封禁",
                "• /mute [分钟] — 禁言用户（默认60分钟）",
                "• /unmute — 解除禁言",
                "• /warn [原因] — 警告用户",
                "• /userinfo — 查看用户详细信息",
                "• /pin — 置顶消息",
                "• /unpin — 取消置顶",
            ])
    else:
        lines = [
            "📋 <b>命令帮助</b>\n",
            "• /start — 开始使用 / 管理员登录",
            "• /help — 显示此帮助信息",
        ]

    _sched_del(context, await update.message.reply_html("\n".join(lines)), 120)


# ---------------------------------------------------------------------------
# /checkin — daily check-in (打卡)
# ---------------------------------------------------------------------------

async def cmd_checkin(update: Update, context):
    """每日打卡命令 /checkin"""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'points'):
        return

    if not global_flask_app:
        return

    def _check_and_checkin():
        with global_flask_app.app_context():
            clone_id = context.application.bot_data.get('clone_id')
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=clone_id).first()
            if not group:
                return {'status': 'error', 'msg': '群组不存在'}

            conf = get_group_conf(group)

            if not conf.get('checkin_open'):
                return {'status': 'disabled'}

            del_time = safe_int(conf.get('checkin_del_time'), 0)

            db_user = GroupUser.query.filter_by(group_id=group.id, tg_id=user.id).first()
            if not db_user:
                return {
                    'status': 'not_registered',
                    'msg': sanitize_html_for_telegram(conf.get('msg_not_registered', '⚠️ 您尚未认证，请先完成认证')),
                    'del_time': del_time,
                }

            # Check if membership has expired
            if db_user.expiration_date and get_beijing_now() > db_user.expiration_date:
                return {
                    'status': 'expired',
                    'msg': sanitize_html_for_telegram(
                        conf.get('msg_expired_ban', '⛔️ 您的认证已过期，已被暂时禁言。请联系管理员续费。')
                    ),
                    'group_user_id': db_user.id,
                    'is_banned': db_user.is_banned,
                }

            # Check if already checked in today
            today = get_beijing_today()
            if db_user.checkin_time and db_user.checkin_time >= today:
                return {
                    'status': 'repeat',
                    'msg': sanitize_html_for_telegram(conf.get('msg_repeat_checkin', '🔄 <b>今天已打卡</b>')),
                    'del_time': del_time,
                }

            # First check-in of the day
            db_user.checkin_time = get_beijing_now()
            db_user.online = True
            db.session.commit()

            return {
                'status': 'success',
                'msg': sanitize_html_for_telegram(conf.get('msg_checkin_success', '✅ 打卡成功')),
                'del_time': del_time,
            }

    result = await asyncio.get_running_loop().run_in_executor(None, _check_and_checkin)

    status = result.get('status')
    msg = result.get('msg', '')
    del_time = result.get('del_time', 0)

    if status == 'disabled':
        _sched_del(context, await update.message.reply_text("ℹ️ 该群未开启打卡功能"), 20)
    elif status == 'error':
        _sched_del(context, await update.message.reply_text(f"❌ {msg}"), 20)
    elif status == 'not_registered':
        r = await update.message.reply_html(msg)
        if del_time > 0:
            _sched_del(context, r, del_time)
    elif status == 'expired':
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=user.id,
                permissions=get_muted_permissions()
            )

            def _mark_banned():
                with global_flask_app.app_context():
                    gu = GroupUser.query.get(result['group_user_id'])
                    if gu and not gu.is_banned:
                        gu.is_banned = True
                        db.session.commit()
            await asyncio.get_running_loop().run_in_executor(None, _mark_banned)
        except Exception:
            pass
        _sched_del(context, await update.message.reply_html(msg), 30)
    elif status in ('repeat', 'success'):
        r = await update.message.reply_html(msg)
        if del_time > 0:
            _sched_del(context, r, del_time)


# ---------------------------------------------------------------------------
# /signin — daily points sign-in (签到)
# ---------------------------------------------------------------------------

async def cmd_signin(update: Update, context):
    """每日签到获取积分命令 /signin"""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'points'):
        return

    if not global_flask_app:
        return

    def _do_signin():
        with global_flask_app.app_context():
            clone_id = context.application.bot_data.get('clone_id')
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=clone_id).first()
            if not group:
                return {'status': 'error', 'msg': '群组不存在'}

            conf = get_group_conf(group)

            if not conf.get('signin_open'):
                return {'status': 'disabled'}

            del_time = safe_int(conf.get('signin_del_time'), 0)

            signin_rule = PointsRule.query.filter_by(
                group_id=group.id,
                rule_type='checkin',
                is_active=True
            ).first()

            if not signin_rule:
                return {'status': 'disabled'}

            today = get_beijing_today()

            # Check if already signed in today
            already_signed = PointsLog.query.filter_by(
                group_id=group.id,
                user_id=user.id,
                reason='每日签到'
            ).filter(PointsLog.created_at >= today).first()

            user_points = UserPoints.query.filter_by(
                group_id=group.id,
                user_id=user.id
            ).first()
            cur_balance = user_points.points_balance if user_points else 0

            def _substitute(tpl, earned=0, balance=0):
                tpl = tpl.replace('{tg_id}', str(user.id))
                tpl = tpl.replace('{积分}', str(earned))
                tpl = tpl.replace('{余额}', str(balance))
                return tpl

            if already_signed:
                raw_tpl = conf.get('msg_repeat_signin', '🔄 <b>今日已签到，当前余额：{余额}</b>')
                msg_text = sanitize_html_for_telegram(_substitute(raw_tpl, earned=0, balance=cur_balance))
                return {'status': 'repeat', 'msg': msg_text, 'del_time': del_time}

            # Award points
            if not user_points:
                user_points = UserPoints(
                    group_id=group.id,
                    user_id=user.id,
                    points_balance=0
                )
                db.session.add(user_points)

            earned_points = signin_rule.points_amount
            user_points.points_balance += earned_points
            cur_balance = user_points.points_balance

            db.session.add(PointsLog(
                group_id=group.id,
                user_id=user.id,
                points_change=earned_points,
                reason='每日签到',
                balance_after=cur_balance
            ))
            db.session.commit()

            raw_tpl = conf.get('msg_signin_success', '🎉 <b>签到成功！获得 {积分} 积分，当前余额：{余额}</b>')
            msg_text = sanitize_html_for_telegram(_substitute(raw_tpl, earned=earned_points, balance=cur_balance))
            return {'status': 'success', 'msg': msg_text, 'del_time': del_time}

    result = await asyncio.get_running_loop().run_in_executor(None, _do_signin)

    status = result.get('status')
    msg = result.get('msg', '')
    del_time = result.get('del_time', 0)

    if status == 'disabled':
        _sched_del(context, await update.message.reply_text("ℹ️ 该群未开启签到功能"), 20)
    elif status == 'error':
        _sched_del(context, await update.message.reply_text(f"❌ {msg}"), 20)
    elif status in ('repeat', 'success'):
        r = await update.message.reply_html(msg)
        if del_time > 0:
            _sched_del(context, r, del_time)


# ---------------------------------------------------------------------------
# Register bot commands with Telegram (enables autocomplete in clients)
# ---------------------------------------------------------------------------

async def _register_bot_commands(bot):
    """Register user-facing commands with Telegram so they appear as suggestions."""
    from telegram import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

    group_commands = [
        BotCommand("help", "查看帮助信息"),
        BotCommand("points", "查看我的积分和等级"),
        BotCommand("rank", "积分排行榜"),
        BotCommand("active", "活跃用户排行榜"),
        BotCommand("checkin", "每日打卡"),
        BotCommand("signin", "每日签到获取积分"),
        BotCommand("mylink", "获取专属邀请链接"),
        BotCommand("invite_rank", "邀请排行榜"),
        BotCommand("lottery", "查看进行中的抽奖"),
        BotCommand("transfer", "积分转让 (@用户 数量)"),
        BotCommand("menu", "显示群组菜单"),
    ]

    private_commands = [
        BotCommand("start", "开始使用 / 管理员登录"),
        BotCommand("help", "查看帮助信息"),
    ]

    try:
        await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
        print("✅ Bot 命令已注册到 Telegram", flush=True)
    except Exception as e:
        print(f"⚠️ 注册 Bot 命令失败: {e}", flush=True)


async def run_bot(app_instance):
    """
    初始化机器人，接收 Flask App 实例以便在回调中使用 Context
    """
    # Bot token is embedded in every Telegram API URL — keep it out of logs.
    from app.log_redaction import install_log_redaction
    install_log_redaction(wrap_std_streams=False)

    token = os.getenv('TG_BOT_TOKEN')
    if not token: 
        print("⚠️ 未设置 TG_BOT_TOKEN")
        return

    global global_bot_loop, global_flask_app
    global_bot_loop = asyncio.get_running_loop()
    global_flask_app = app_instance # 📦 存储 Flask App 实例

    # Keep app.bot.state in sync so handler modules can import from there
    from app.bot import state as _bot_state
    _bot_state.global_bot_loop = global_bot_loop
    _bot_state.global_flask_app = global_flask_app

    print("🤖 正在初始化 Bot...", flush=True)
    app = Application.builder().token(token).build()
    
    global global_ptb_app
    global_ptb_app = app
    _bot_state.global_ptb_app = app

    # Store Flask app in bot_data so report handlers can access the DB
    app.bot_data['flask_app'] = app_instance
    # Store cmd_start so report_cancel fallback can invoke it when user sends /start mid-report
    app.bot_data['cmd_start'] = cmd_start

    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    
    # 🆕 New member/leave handlers (must come before general message handler)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_member))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, handle_left_chat_member))
    
    # 🆕 Auto-delete system messages (must come early to delete before processing)
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS | 
        filters.StatusUpdate.LEFT_CHAT_MEMBER | 
        filters.StatusUpdate.PINNED_MESSAGE,
        handle_auto_delete_messages
    ))
    
    # 🆕 Handle channel messages for pin control
    app.add_handler(MessageHandler(filters.SenderChat.CHANNEL, handle_channel_pin))

    # Channel post sync (channel_post updates — not group-member messages)
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS, handle_sync_channel_posts))
    # Our own channel posts never come back as updates → hook the bot's send calls
    from app.services import own_post_sync
    own_post_sync.register_bot(app.bot, None)

    # Report module handlers – registered BEFORE the general text handler so that
    # report_conv_handler can intercept text replies in STEP_QUESTION state before
    # on_message consumes them.  audit_callback_handler must also precede the
    # catch-all CallbackQueryHandler(pagination_callback).
    from app.modules.report.bot import report_conv_handler, view_reports_handler, audit_callback_handler
    app.add_handler(audit_callback_handler)   # must be before CallbackQueryHandler(pagination_callback)
    app.add_handler(report_conv_handler)      # must be before on_message and CommandHandler("start", …)
    app.add_handler(view_reports_handler)     # must be before CommandHandler("start", cmd_start)

    # General message handler (processes text messages)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    # 🆕 Non-text message handler (stickers, photos, videos, etc.) for spam protection
    _media_filter = (
        filters.Sticker.ALL | filters.PHOTO | filters.VIDEO | filters.ANIMATION |
        filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE |
        filters.POLL | filters.LOCATION | filters.CONTACT | filters.VENUE
    )
    app.add_handler(MessageHandler(_media_filter & ~filters.COMMAND, on_non_text_message))

    # Callback query handler (catch-all – runs after audit_callback_handler)
    app.add_handler(CallbackQueryHandler(pagination_callback)) 
    
    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    
    # Group management commands (群管理机器人功能)
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CommandHandler("pin", cmd_pin))
    app.add_handler(CommandHandler("unpin", cmd_unpin))
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("userinfo", cmd_userinfo))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("buttons", cmd_menu))  # alias for menu
    
    # 🆕 Points auction commands (积分竞拍命令)
    app.add_handler(CommandHandler("bid", cmd_bid))
    app.add_handler(CommandHandler("auction", cmd_auction))
    
    # 🆕 Interactive features commands (互动功能命令)
    app.add_handler(CommandHandler("vote", cmd_vote))
    app.add_handler(CommandHandler("quiz", cmd_quiz))
    app.add_handler(CommandHandler("redpacket", cmd_redpacket))
    app.add_handler(CommandHandler("lottery_draw", cmd_lottery_draw))
    app.add_handler(CommandHandler("draw", cmd_lottery_draw))  # alias for lottery_draw
    app.add_handler(CommandHandler("lottery_history", cmd_lottery_history))
    app.add_handler(CommandHandler("lottery", cmd_lottery))
    app.add_handler(CommandHandler("lottery_list", cmd_lottery))  # alias for lottery
    app.add_handler(CommandHandler("rank", cmd_rank))
    app.add_handler(CommandHandler("top", cmd_rank))  # alias for rank
    app.add_handler(CommandHandler("active", cmd_active))
    app.add_handler(CommandHandler("invite_rank", cmd_invite_rank))  # 🆕 Invitation rank command
    app.add_handler(CommandHandler("invites", cmd_invite_rank))  # alias for invite_rank
    app.add_handler(CommandHandler("mylink", cmd_mylink))  # 获取专属邀请链接
    app.add_handler(CommandHandler("points", cmd_points))  # 查询自己积分余额
    app.add_handler(CommandHandler("balance", cmd_points))  # alias for points
    app.add_handler(CommandHandler("transfer", cmd_transfer))  # 积分转让
    app.add_handler(CommandHandler("help", cmd_help))  # 显示帮助信息
    app.add_handler(CommandHandler("checkin", cmd_checkin))  # 每日打卡
    app.add_handler(CommandHandler("signin", cmd_signin))  # 每日签到
    
    # Periodic jobs
    app.job_queue.run_repeating(check_expired_users, interval=EXPIRATION_CHECK_INTERVAL, first=10)
    app.job_queue.run_repeating(check_scheduled_messages, interval=SCHEDULED_MESSAGE_CHECK_INTERVAL, first=15)
    app.job_queue.run_repeating(check_timed_group_control, interval=60, first=20)  # 🆕 Check every minute
    app.job_queue.run_repeating(check_channel_subscriptions, interval=3600, first=30)  # 🆕 Check every hour
    app.job_queue.run_repeating(update_member_levels, interval=1800, first=40)  # 🆕 Update every 30 minutes
    app.job_queue.run_repeating(update_lottery_status, interval=60, first=45)  # 🆕 Update lottery status every minute
    app.job_queue.run_repeating(run_lottery_draws, interval=300, first=50)  # 🆕 Check every 5 minutes
    app.job_queue.run_repeating(check_inactive_users, interval=86400, first=60)  # 🆕 Check inactive users daily
    app.job_queue.run_repeating(check_auction_expiration, interval=300, first=70)  # 🆕 Check auction expiration every 5 minutes
    app.job_queue.run_repeating(check_redpacket_expiration, interval=300, first=80)  # 🆕 Check red packet expiration every 5 minutes
    app.job_queue.run_repeating(check_and_send_votes, interval=60, first=90)  # 🆕 Auto-send pending votes every minute
    # 同步日志保留时长：每小时清理一次过期 SyncMessageLog（启动约 2 分钟后先跑一次）
    from app.services.sync_log_retention import sync_log_cleanup_job
    app.job_queue.run_repeating(sync_log_cleanup_job, interval=3600, first=120, name='sync_log_retention')
    
    await app.initialize()
    await app.start()
    await _register_bot_commands(app.bot)

    # 根据环境变量自动判断运行模式
    # Explicit allowed_updates so channel_post (频道帖同步) is never dropped.
    # Omitting allowed_updates keeps Telegram's *previous* webhook setting, which
    # may exclude channel_post after an older deploy.
    _all_allowed = [t.value for t in Update.ALL_TYPES]

    domain = os.getenv('RAILWAY_PUBLIC_DOMAIN', '').strip()
    if domain:
        # Webhook 模式：注册 Webhook 地址到 Telegram
        webhook_url = f"https://{domain}/core/webhook"
        try:
            await app.bot.set_webhook(url=webhook_url, allowed_updates=_all_allowed)
            print(f"✅ Bot 初始化完成 (Webhook 模式)，Webhook URL: {webhook_url}", flush=True)
        except Exception as e:
            print(f"❌ Webhook 设置失败: {e}", flush=True)
            raise
    else:
        # Polling 模式：开始轮询拉取消息
        print("✅ Bot 初始化完成 (Polling 模式)，开始轮询...", flush=True)
        try:
            await app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=_all_allowed,
            )
            print("✅ Polling 已启动", flush=True)
        except Exception as e:
            print(f"❌ Polling 启动失败: {e}", flush=True)
            raise
    
    # 🆕 Start all active clone bots after main bot is ready
    await start_all_clone_bots(app_instance)

def setup_clone_handlers(app, flask_app, clone_id):
    """
    为克隆机器人设置处理器
    这个函数会为每个克隆机器人设置完整的命令和消息处理器
    
    Args:
        app: Telegram Application实例
        flask_app: Flask应用实例
        clone_id: 克隆机器人的数据库ID
    """
    # Note: Clone bots use the same handlers as the main bot
    # But we need to add permission checks for clone_id

    # Store clone_id and flask_app in bot_data so handlers can identify which clone
    # is running and access the database (required by report handlers).
    app.bot_data['clone_id'] = clone_id
    app.bot_data['flask_app'] = flask_app
    app.bot_data['cmd_start'] = cmd_start

    # Add chat member handler
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    
    # Add member join/leave handlers
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_member))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, handle_left_chat_member))
    
    # Add auto-delete system messages
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS | 
        filters.StatusUpdate.LEFT_CHAT_MEMBER | 
        filters.StatusUpdate.PINNED_MESSAGE,
        handle_auto_delete_messages
    ))
    
    # Handle channel messages for pin control
    app.add_handler(MessageHandler(filters.SenderChat.CHANNEL, handle_channel_pin))

    # Channel post sync (channel_post updates — not group-member messages)
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS, handle_sync_channel_posts))
    # Our own channel posts never come back as updates → hook the bot's send calls
    from app.services import own_post_sync
    own_post_sync.register_bot(app.bot, clone_id)

    # Report module handlers – registered BEFORE the general text handler so that
    # report_conv_handler can intercept text replies in STEP_QUESTION state before
    # on_message consumes them.  audit_callback_handler must also precede the
    # catch-all CallbackQueryHandler(pagination_callback).
    # Each clone bot gets its own fresh handler instances via make_report_handlers()
    # to avoid sharing state/registration across multiple Application objects.
    from app.modules.report.bot import make_report_handlers
    _rconv, _rview, _raudit = make_report_handlers()
    app.add_handler(_raudit)   # must be before CallbackQueryHandler(pagination_callback)
    app.add_handler(_rconv)    # must be before on_message and CommandHandler("start", …)
    app.add_handler(_rview)    # must be before CommandHandler("start", cmd_start)

    # General message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    # 🆕 Non-text message handler (stickers, photos, videos, etc.) for spam protection
    _media_filter = (
        filters.Sticker.ALL | filters.PHOTO | filters.VIDEO | filters.ANIMATION |
        filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE |
        filters.POLL | filters.LOCATION | filters.CONTACT | filters.VENUE
    )
    app.add_handler(MessageHandler(_media_filter & ~filters.COMMAND, on_non_text_message))

    # Callback query handler
    app.add_handler(CallbackQueryHandler(pagination_callback))
    
    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CommandHandler("pin", cmd_pin))
    app.add_handler(CommandHandler("unpin", cmd_unpin))
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("userinfo", cmd_userinfo))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("buttons", cmd_menu))
    app.add_handler(CommandHandler("bid", cmd_bid))
    app.add_handler(CommandHandler("auction", cmd_auction))
    app.add_handler(CommandHandler("vote", cmd_vote))
    app.add_handler(CommandHandler("quiz", cmd_quiz))
    app.add_handler(CommandHandler("redpacket", cmd_redpacket))
    app.add_handler(CommandHandler("lottery_draw", cmd_lottery_draw))
    app.add_handler(CommandHandler("draw", cmd_lottery_draw))
    app.add_handler(CommandHandler("lottery_history", cmd_lottery_history))
    app.add_handler(CommandHandler("lottery", cmd_lottery))
    app.add_handler(CommandHandler("lottery_list", cmd_lottery))
    app.add_handler(CommandHandler("rank", cmd_rank))
    app.add_handler(CommandHandler("top", cmd_rank))
    app.add_handler(CommandHandler("active", cmd_active))
    app.add_handler(CommandHandler("clones", cmd_clones))
    app.add_handler(CommandHandler("invite_rank", cmd_invite_rank))  # 邀请排行榜
    app.add_handler(CommandHandler("invites", cmd_invite_rank))       # alias for invite_rank
    app.add_handler(CommandHandler("mylink", cmd_mylink))  # 获取专属邀请链接
    app.add_handler(CommandHandler("points", cmd_points))  # 查询自己积分余额
    app.add_handler(CommandHandler("balance", cmd_points))  # alias for points
    app.add_handler(CommandHandler("transfer", cmd_transfer))  # 积分转让
    app.add_handler(CommandHandler("help", cmd_help))  # 显示帮助信息
    app.add_handler(CommandHandler("checkin", cmd_checkin))  # 每日打卡
    app.add_handler(CommandHandler("signin", cmd_signin))  # 每日签到

    # Note: Periodic background jobs are intentionally NOT registered here.
    # All system-wide jobs (check_expired_users, check_scheduled_messages, etc.)
    # are registered only on the main bot (see run_bot()). Registering them on
    # every clone bot would cause each job to fire N+1 times per interval.


async def start_all_clone_bots(flask_app):
    """
    启动所有激活的克隆机器人
    在主Bot启动后调用
    仅在主实例中执行，克隆实例跳过
    
    Args:
        flask_app: Flask应用实例
    """
    # 克隆实例不应该启动其他克隆
    if is_clone_instance():
        print("ℹ️ 克隆实例不启动其他克隆机器人 (Clone instance does not start other clones)")
        return
    
    if not flask_app:
        print("⚠️ Flask app not available, skipping clone bot startup")
        return
    
    def _get_active_clones():
        with flask_app.app_context():
            now = get_beijing_now()
            # Get all active clones that haven't expired
            clones = BotClone.query.filter_by(is_active=True).all()
            
            active_clones = []
            for clone in clones:
                # Check expiration
                if clone.expiration_date and clone.expiration_date < now:
                    print(f"⏰ Clone bot {clone.id} ({clone.clone_name}) has expired, skipping")
                    continue
                
                active_clones.append({
                    'id': clone.id,
                    'name': clone.clone_name,
                    'token': clone.get_bot_token(),
                    'webhook_url': clone.webhook_url
                })
            
            return active_clones
    
    try:
        clones = await asyncio.get_running_loop().run_in_executor(None, _get_active_clones)
        
        if not clones:
            print("ℹ️ No active clone bots to start")
            return
        
        print(f"🤖 Starting {len(clones)} clone bot(s)...")
        
        for clone_data in clones:
            try:
                success = await bot_clone_manager.start_clone_bot(
                    clone_id=clone_data['id'],
                    bot_token=clone_data['token'],
                    webhook_url=get_clone_webhook_url(clone_data['id'], clone_data['webhook_url']),
                    flask_app=flask_app,
                    handlers_setup_func=setup_clone_handlers,
                    post_init_func=_register_bot_commands,
                )
                
                if success:
                    print(f"✅ Clone bot {clone_data['id']} ({clone_data['name']}) started", flush=True)
                else:
                    print(f"❌ Failed to start clone bot {clone_data['id']} ({clone_data['name']})", flush=True)
                    
            except Exception as e:
                logging.error(f"Error starting clone bot {clone_data['id']}: {e}")
                traceback.print_exc()
        
        print(f"✅ Clone bot startup complete ({len(bot_clone_manager.get_active_clone_ids())} running)", flush=True)
        
    except Exception as e:
        logging.error(f"Error in start_all_clone_bots: {e}")
        traceback.print_exc()


def do_like(chat_id, message_id, emoji, token=None):
    """Set a message reaction using a Telegram-allowed emoji only.

    Telegram's setMessageReaction only accepts the Bot API reaction whitelist
    (e.g. ❤ not ❤️, and not arbitrary icons). Invalid emojis are normalized
    first; API failures are logged instead of failing silently.
    """
    if not token:
        token = os.getenv('TG_BOT_TOKEN')
    if not token:
        return
    clean_emoji = normalize_like_emoji(emoji)
    try:
        url = f"https://api.telegram.org/bot{token}/setMessageReaction"
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "reaction": [{"type": "emoji", "emoji": clean_emoji}],
            },
            timeout=5,
        )
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not resp.ok or not data.get('ok', False):
            print(
                f"❌ [Like] setMessageReaction failed chat={chat_id} msg={message_id} "
                f"emoji={clean_emoji!r} status={resp.status_code} body={data or resp.text}",
                flush=True,
            )
    except Exception as e:
        print(f"❌ [Like] 请求异常: {redact_secrets(str(e))}", flush=True)


# Telegram setChatMemberTag cannot change tags of other administrators
# (API error is often CHAT_CREATOR_REQUIRED, which is misleading).
# Cache: chat_id -> (can_manage_tags, already_logged, checked_at_epoch)
_bot_manage_tags_cache = {}
_BOT_MANAGE_TAGS_RETRY_SECS = 3600
_MEMBER_TAG_ADMIN_STATUSES = frozenset({'administrator', 'creator'})
_MEMBER_TAG_LEFT_STATUSES = frozenset({'left', 'kicked'})


def _telegram_api_error_text(data, raw='') -> str:
    if isinstance(data, dict):
        desc = data.get('description') or ''
        if desc:
            return str(desc)
    return str(raw or data or '')


def _telegram_error_matches(text, *needles) -> bool:
    hay = (text or '').upper()
    return any(str(n).upper() in hay for n in needles if n)


def _bot_user_id_from_token(token):
    """Best-effort bot id from the token prefix. Never logs or returns the secret."""
    if not token or not isinstance(token, str) or ':' not in token:
        return None
    prefix = token.split(':', 1)[0]
    if prefix.isdigit():
        return int(prefix)
    return None


def _telegram_get_chat_member(chat_id, user_id, token):
    """Return ``(result_dict_or_None, error_text_or_None)`` for getChatMember."""
    if not token or not chat_id or not user_id:
        return None, 'missing_args'
    try:
        url = f"https://api.telegram.org/bot{token}/getChatMember"
        resp = requests.post(
            url, json={"chat_id": chat_id, "user_id": int(user_id)}, timeout=5
        )
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if data.get('ok') and isinstance(data.get('result'), dict):
            return data['result'], None
        return None, _telegram_api_error_text(data, getattr(resp, 'text', ''))
    except Exception as e:
        return None, redact_secrets(str(e))


def _bot_can_manage_member_tags(chat_id, token):
    """Whether the bot may call setChatMemberTag in *chat_id*.

    True / False when known; None when the permission could not be determined.
    A False result is cached for an hour and logged once per chat so a missing
    ``can_manage_tags`` right is not silently swallowed, and is not persisted
    as a per-user skip (granting the right later should resume tagging).
    """
    now = time.time()
    cached = _bot_manage_tags_cache.get(chat_id)
    if cached:
        can, _logged, checked_at = cached
        if can is True:
            return True
        if can is False and (now - checked_at) < _BOT_MANAGE_TAGS_RETRY_SECS:
            return False
    bot_id = _bot_user_id_from_token(token)
    if not bot_id:
        return None
    member, _err = _telegram_get_chat_member(chat_id, bot_id, token)
    if not member:
        return None
    status = member.get('status')
    if status == 'creator':
        can = True
    elif status == 'administrator':
        can = bool(member.get('can_manage_tags'))
    else:
        can = False
    already_logged = cached[1] if cached else False
    if can is False and not already_logged:
        print(
            f"[MemberTag] bot lacks can_manage_tags in chat={chat_id} status={status}",
            flush=True,
        )
        already_logged = True
    _bot_manage_tags_cache[chat_id] = (can, already_logged, now)
    return can


def _remember_member_tag_skip(db_user, reason: str) -> None:
    if db_user is None:
        return
    if getattr(db_user, 'telegram_tag_skip', None) == reason:
        return
    try:
        db_user.telegram_tag_skip = reason
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"❌ [MemberTag] failed to persist telegram_tag_skip: {e}", flush=True)


def _clear_member_tag_skip(db_user) -> None:
    if db_user is None or not getattr(db_user, 'telegram_tag_skip', None):
        return
    db_user.telegram_tag_skip = None


def _set_chat_member_tag(chat_id, user_id, tag, token=None):
    """Call setChatMemberTag. Returns ``(ok, error_text)``.

    Expected failures (admin target, user left) are not printed here; the
    caller logs a one-line skip and persists a retry marker.
    """
    if not token:
        token = os.getenv('TG_BOT_TOKEN')
    if not token or not chat_id or not user_id:
        return False, 'missing_args'
    clean_tag = normalize_telegram_member_tag(tag, default='')
    try:
        url = f"https://api.telegram.org/bot{token}/setChatMemberTag"
        payload = {
            "chat_id": chat_id,
            "user_id": int(user_id),
            "tag": clean_tag,
        }
        resp = requests.post(url, json=payload, timeout=5)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not resp.ok or not data.get('ok', False):
            err = _telegram_api_error_text(data, getattr(resp, 'text', ''))
            if not _telegram_error_matches(
                err,
                'CHAT_CREATOR_REQUIRED',
                'PARTICIPANT_ID_INVALID',
                'USER_NOT_PARTICIPANT',
                'CHAT_ADMIN_REQUIRED',
                'RIGHT_FORBIDDEN',
                'NOT ENOUGH RIGHTS',
                'CAN_MANAGE_TAGS',
            ):
                print(
                    f"❌ [MemberTag] setChatMemberTag failed chat={chat_id} user={user_id} "
                    f"tag={clean_tag!r} status={resp.status_code} body={data or resp.text}",
                    flush=True,
                )
            return False, err
        return True, None
    except Exception as e:
        err = redact_secrets(str(e))
        print(f"❌ [MemberTag] 请求异常: {err}", flush=True)
        return False, err


def do_set_member_tag(chat_id, user_id, tag, token=None) -> bool:
    """Apply a Telegram nickname member tag via setChatMemberTag.

    The bot must be a group admin with ``can_manage_tags``. Tag text is
    normalized to 0–16 characters without emoji. Returns True on API success.
    """
    ok, _err = _set_chat_member_tag(chat_id, user_id, tag, token=token)
    return ok


def _resolve_bot_token_for_group(group, clone_id_from_context=None):
    """Return the bot token that manages *group* (clone token or main env token)."""
    clone_id = clone_id_from_context
    if clone_id is None and group is not None:
        clone_id = getattr(group, 'clone_id', None)
    if clone_id:
        clone = BotClone.query.get(clone_id)
        if clone:
            token = clone.get_bot_token()
            if token:
                return token
    return os.getenv('TG_BOT_TOKEN')


def apply_telegram_member_tag_for_user(group, db_user, conf=None, token=None, force: bool = False) -> bool:
    """Ensure the authenticated user's Telegram nickname tag matches config.

    Skips the API call when the desired tag already matches
    ``db_user.applied_telegram_tag`` unless *force* is True.
    Administrators/creators are skipped (Telegram does not let a bot change
    another admin's tag); the skip is persisted so later triggers do not retry
    or log an error. Users who have left are remembered the same way, but
    membership is re-checked so a returning member can still be tagged.
    Safe to call from the request thread with live ORM objects.
    """
    if not group or not db_user:
        return False
    conf = conf or get_group_conf(group)
    if not conf.get('auto_member_tag', True):
        return False
    desired = resolve_telegram_member_tag(getattr(db_user, 'member_tags', None), conf)
    current = getattr(db_user, 'applied_telegram_tag', None)
    skip = getattr(db_user, 'telegram_tag_skip', None)
    if not force and current is not None and current == desired and not skip:
        return True
    if not force and skip == 'admin':
        return True
    token = token or _resolve_bot_token_for_group(group)
    chat_id = convert_chat_id_to_int(group.chat_id, getattr(group, 'id', None))
    if chat_id is None:
        return False

    can_manage = _bot_can_manage_member_tags(chat_id, token)
    if can_manage is False:
        return False

    member, member_err = _telegram_get_chat_member(chat_id, db_user.tg_id, token)
    if member:
        status = member.get('status')
        if status in _MEMBER_TAG_ADMIN_STATUSES:
            print(
                f"[MemberTag] skip admin user={db_user.tg_id} chat={chat_id} status={status}",
                flush=True,
            )
            _remember_member_tag_skip(db_user, 'admin')
            return False
        if status in _MEMBER_TAG_LEFT_STATUSES:
            if skip != 'left':
                print(
                    f"[MemberTag] skip left user={db_user.tg_id} chat={chat_id} status={status}",
                    flush=True,
                )
            _remember_member_tag_skip(db_user, 'left')
            return False
    elif _telegram_error_matches(member_err, 'PARTICIPANT_ID_INVALID', 'USER_NOT_PARTICIPANT'):
        if skip != 'left':
            print(
                f"[MemberTag] skip left user={db_user.tg_id} chat={chat_id}",
                flush=True,
            )
        _remember_member_tag_skip(db_user, 'left')
        return False

    ok, err = _set_chat_member_tag(chat_id, db_user.tg_id, desired, token=token)
    if ok:
        db_user.applied_telegram_tag = desired
        _clear_member_tag_skip(db_user)
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"❌ [MemberTag] failed to persist applied_telegram_tag: {e}", flush=True)
        return True

    if _telegram_error_matches(err, 'CHAT_CREATOR_REQUIRED'):
        print(
            f"[MemberTag] skip admin user={db_user.tg_id} chat={chat_id}",
            flush=True,
        )
        _remember_member_tag_skip(db_user, 'admin')
        return False
    if _telegram_error_matches(err, 'PARTICIPANT_ID_INVALID', 'USER_NOT_PARTICIPANT'):
        if skip != 'left':
            print(
                f"[MemberTag] skip left user={db_user.tg_id} chat={chat_id}",
                flush=True,
            )
        _remember_member_tag_skip(db_user, 'left')
        return False
    if _telegram_error_matches(
        err,
        'CHAT_ADMIN_REQUIRED',
        'RIGHT_FORBIDDEN',
        'NOT ENOUGH RIGHTS',
        'CAN_MANAGE_TAGS',
    ):
        cached = _bot_manage_tags_cache.get(chat_id)
        already_logged = cached[1] if cached else False
        if not already_logged:
            print(
                f"[MemberTag] bot lacks can_manage_tags in chat={chat_id}: {err}",
                flush=True,
            )
        _bot_manage_tags_cache[chat_id] = (False, True, time.time())
        return False
    return False


def apply_telegram_member_tag_by_ids(group_id, user_db_id, conf=None, token=None, force: bool = False) -> bool:
    """Thread-safe wrapper: reload ORM rows inside the Flask app context."""
    app = global_flask_app
    if not app:
        return False
    with app.app_context():
        group = BotGroup.query.get(group_id)
        db_user = GroupUser.query.get(user_db_id)
        if not group or not db_user:
            return False
        return apply_telegram_member_tag_for_user(group, db_user, conf=conf, token=token, force=force)


def clear_telegram_member_tag_for_user(group, db_user, token=None) -> bool:
    """Clear the Telegram nickname tag when a user is removed/unauthenticated."""
    if not group or not db_user:
        return False
    token = token or _resolve_bot_token_for_group(group)
    chat_id = convert_chat_id_to_int(group.chat_id, getattr(group, 'id', None))
    if chat_id is None:
        return False
    # Do not try to clear a tag Telegram will refuse (other admins / departed users).
    member, member_err = _telegram_get_chat_member(chat_id, db_user.tg_id, token)
    if member and member.get('status') in _MEMBER_TAG_ADMIN_STATUSES | _MEMBER_TAG_LEFT_STATUSES:
        return False
    if _telegram_error_matches(member_err, 'PARTICIPANT_ID_INVALID', 'USER_NOT_PARTICIPANT'):
        return False
    ok, _err = _set_chat_member_tag(chat_id, db_user.tg_id, '', token=token)
    if ok:
        db_user.applied_telegram_tag = ''
        _clear_member_tag_skip(db_user)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    return ok

# 🤖 Group Management Commands (群管理机器人功能)

# NOTE: cmd_kick lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.

# NOTE: cmd_ban lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.

# NOTE: cmd_unban lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.

# NOTE: cmd_mute lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.

# NOTE: cmd_unmute lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.

# NOTE: cmd_pin lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.

# NOTE: cmd_unpin lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.

# NOTE: cmd_warn lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.


# NOTE: cmd_userinfo lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.

async def cmd_menu(update: Update, context):
    """显示群底部按钮菜单命令 /menu 或 /buttons - 使用菜单键盘"""
    chat = update.effective_chat
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        await update.message.reply_text("❌ 此命令只能在群组中使用")
        return
    
    # Get bottom buttons as reply keyboard (menu keyboard)
    bottom_buttons_markup = await display_bottom_buttons(chat.id, context, use_reply_keyboard=True)
    
    if not bottom_buttons_markup:
        await update.message.reply_text("ℹ️ 该群组暂未设置底部按钮")
        return
    
    # Send message with menu keyboard
    await update.message.reply_text(
        "📋 群组菜单（点击下方按钮使用功能）：",
        reply_markup=bottom_buttons_markup
    )

async def cmd_bid(update: Update, context):
    """积分竞拍出价命令 /bid <auction_id> <amount>"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        await update.message.reply_text("❌ 此命令只能在群组中使用")
        return
    if not await _ensure_plugin_enabled(update, context, 'points'):
        return
    
    # Parse arguments
    if len(context.args) < 2:
        await update.message.reply_text("❌ 用法: /bid <竞拍ID> <出价金额>\n例如: /bid 1 100")
        return
    
    try:
        auction_id = int(context.args[0])
        bid_amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ 无效的参数，请输入数字")
        return
    
    if bid_amount <= 0:
        await update.message.reply_text("❌ 出价金额必须大于0")
        return
    
    def _process_bid():
        with global_flask_app.app_context():
            try:
                # Use pessimistic locking to prevent race conditions
                group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                if not group:
                    return "error", "群组未找到"
                if not get_group_conf(group).get('points_shop_enabled', True):
                    return "error", "积分商城已关闭"
                
                # Get auction with row-level lock
                auction = PointsAuction.query.filter_by(
                    id=auction_id,
                    group_id=group.id
                ).with_for_update().first()
                
                if not auction:
                    return "error", "竞拍不存在"
                
                if auction.status != 'active':
                    return "error", "竞拍未激活或已结束"
                
                # Check if auction has ended
                if auction.auction_end and get_beijing_now() > auction.auction_end:
                    auction.status = 'ended'
                    db.session.commit()
                    return "error", "竞拍已结束"
                
                # Check if bid is higher than current
                if bid_amount <= auction.current_bid:
                    return "error", f"出价必须高于当前价格 {auction.current_bid} 积分"
                
                # Get user points with lock
                user_points = UserPoints.query.filter_by(
                    group_id=group.id,
                    user_id=user.id
                ).with_for_update().first()
                
                if not user_points or user_points.points_balance < bid_amount:
                    current_balance = user_points.points_balance if user_points else 0
                    return "error", f"积分不足。当前积分: {current_balance}，需要: {bid_amount}"
                
                # Refund previous bidder if exists
                if auction.current_bidder_id and auction.current_bidder_id != user.id:
                    prev_bidder_points = UserPoints.query.filter_by(
                        group_id=group.id,
                        user_id=auction.current_bidder_id
                    ).with_for_update().first()
                    
                    if prev_bidder_points:
                        prev_bidder_points.points_balance += auction.current_bid
                        
                        # Log the refund
                        points_log = PointsLog(
                            group_id=group.id,
                            user_id=auction.current_bidder_id,
                            points_change=auction.current_bid,
                            reason=f"竞拍退款: {auction.item_name}",
                            balance_after=prev_bidder_points.points_balance
                        )
                        db.session.add(points_log)
                    else:
                        # Previous bidder points record doesn't exist - log error but continue
                        print(f"Warning: Previous bidder {auction.current_bidder_id} has no points record for refund")
                
                # Deduct points from current bidder
                user_points.points_balance -= bid_amount
                
                # Log the bid
                points_log = PointsLog(
                    group_id=group.id,
                    user_id=user.id,
                    points_change=-bid_amount,
                    reason=f"竞拍出价: {auction.item_name}",
                    balance_after=user_points.points_balance
                )
                db.session.add(points_log)
                
                # Update auction
                auction.current_bid = bid_amount
                auction.current_bidder_id = user.id
                
                db.session.commit()
                
                return "success", auction.item_name, bid_amount, user_points.points_balance, get_group_conf(group).get('auction_channel_id', '').strip() or None
            
            except Exception as e:
                db.session.rollback()
                print(f"Error processing bid: {e}")
                return "error", "出价处理失败，请重试"
    
    result = await asyncio.get_running_loop().run_in_executor(None, _process_bid)
    
    if result[0] == "error":
        await update.message.reply_text(f"❌ {result[1]}")
    else:
        _, item_name, bid_amount, remaining_balance, auction_channel_id = result
        user_display = _format_tg_user_display(user)
        reply_text = (
            f"✅ 出价成功！\n\n"
            f"📦 物品: {item_name}\n"
            f"💰 出价: {bid_amount} 积分\n"
            f"💎 剩余积分: {remaining_balance}"
        )
        await update.message.reply_text(reply_text)

        # Push bid notification to auction channel if configured
        if auction_channel_id:
            try:
                channel_text = (
                    f"🔔 <b>竞拍新出价</b>\n\n"
                    f"📦 物品: {item_name}\n"
                    f"💰 最新出价: <b>{bid_amount} 积分</b>\n"
                    f"👤 出价者: {user_display}"
                )
                await context.bot.send_message(
                    chat_id=auction_channel_id,
                    text=channel_text,
                    parse_mode='HTML'
                )
            except Exception as e:
                print(f"Error sending bid notification to auction channel: {e}")

async def cmd_auction(update: Update, context):
    """查看当前活跃的竞拍 /auction"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        await update.message.reply_text("❌ 此命令只能在群组中使用")
        return
    if not await _ensure_plugin_enabled(update, context, 'points'):
        return
    
    def _get_auctions():
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return None, "群组未找到"
            if not get_group_conf(group).get('points_shop_enabled', True):
                return None, "积分商城已关闭"
            
            auctions = PointsAuction.query.filter_by(
                group_id=group.id,
                status='active'
            ).order_by(PointsAuction.auction_end).all()
            
            return auctions, None
    
    auctions, err = await asyncio.get_running_loop().run_in_executor(None, _get_auctions)
    if err:
        await update.message.reply_text(f"❌ {err}")
        return
    
    if not auctions:
        await update.message.reply_text("📭 当前没有进行中的竞拍")
        return
    
    message_lines = ["🏆 当前竞拍列表\n"]
    message_lines.append("━━━━━━━━━━━━━━━━")
    
    for auction in auctions:
        message_lines.append(f"\n📦 ID: {auction.id}")
        message_lines.append(f"   物品: {auction.item_name}")
        if auction.item_description:
            message_lines.append(f"   描述: {auction.item_description}")
        message_lines.append(f"   起拍价: {auction.starting_price} 积分")
        message_lines.append(f"   当前价: {auction.current_bid} 积分")
        if auction.current_bidder_id:
            message_lines.append(f"   领先者: 用户 {auction.current_bidder_id}")
        if auction.auction_end:
            message_lines.append(f"   结束时间: {auction.auction_end.strftime('%Y-%m-%d %H:%M:%S')}")
        message_lines.append(f"\n   💡 出价: /bid {auction.id} <金额>")
    
    await update.message.reply_text("\n".join(message_lines))

async def handle_channel_pin(update: Update, context):
    """处理频道消息：取消自动置顶 / 自动删除互推消息 / 频道扩展动作"""
    if not global_flask_app or not update.message:
        return
    
    try:
        msg = update.message
        chat = update.effective_chat
        
        if not chat or chat.type not in ['group', 'supergroup']:
            return
        
        # Check if this is a channel post forwarded to the group
        if not msg.sender_chat or msg.sender_chat.type != 'channel':
            return

        from app.services.channel_automation import (
            resolve_channel_automation_group_id,
            should_delete_as_promote,
        )

        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return

            # Discussion-group settings (pin/delete). Missing row must NOT block forward rules.
            settings = OtherSettings.query.filter_by(group_id=group.id).first()

            # Channel sidebar saves forward/templates/coupons on the *channel* BotGroup.
            channel_group = None
            if msg.sender_chat and getattr(msg.sender_chat, 'id', None) is not None:
                channel_group = BotGroup.query.filter_by(
                    chat_id=str(msg.sender_chat.id),
                    clone_id=context.application.bot_data.get('clone_id'),
                ).first()
            automation_gid = resolve_channel_automation_group_id(
                channel_group.id if channel_group else None,
                group.id,
            )
            # Legacy fallback: if channel has no active automation rows, also try discussion group.
            def _load_automation(gid):
                rules = [{
                    'rule_name': rule.rule_name,
                    'source_keywords': rule.source_keywords,
                    'target_chat_id': rule.target_chat_id,
                    'target_thread_id': rule.target_thread_id,
                    'forward_mode': rule.forward_mode or 'copy',
                } for rule in ChannelForwardRule.query.filter_by(group_id=gid, is_active=True).all()]
                tmpls = [{
                    'template_name': item.template_name,
                    'trigger_keywords': item.trigger_keywords,
                    'media_type': item.media_type or 'text',
                    'media_url': item.media_url,
                    'content': item.content,
                    'links': item.links or '[]',
                    'send_as_reply': bool(item.send_as_reply),
                } for item in ChannelMessageTemplate.query.filter_by(group_id=gid, is_active=True).all()]
                cps = [{
                    'coupon_name': item.coupon_name,
                    'coupon_code': item.coupon_code,
                    'button_text': item.button_text or item.coupon_name,
                    'button_url': item.button_url,
                    'trigger_keywords': item.trigger_keywords,
                    'sort_order': item.sort_order or 0,
                } for item in ChannelCoupon.query.filter_by(group_id=gid, is_active=True).order_by(ChannelCoupon.sort_order.asc(), ChannelCoupon.id.asc()).all()]
                return rules, tmpls, cps

            forward_rules, templates, coupons = _load_automation(automation_gid)
            if not (forward_rules or templates or coupons) and automation_gid != group.id:
                forward_rules, templates, coupons = _load_automation(group.id)
            stats_group_id = automation_gid

        # Auto-delete linked channel discussion messages (is_automatic_forward) if enabled
        if settings and settings.auto_delete_channel_discussion_msg and msg.is_automatic_forward:
            try:
                await msg.delete()
                return
            except Exception as e:
                print(f"Error deleting channel discussion message {msg.message_id} in {chat.id}: {e}")
                return

        # Auto-delete promote (channel) messages if enabled — not linked discussion posts
        if settings and should_delete_as_promote(bool(settings.auto_delete_promote_msg), bool(msg.is_automatic_forward)):
            try:
                await msg.delete()
                return
            except Exception as e:
                print(f"Error deleting promote message: {e}")
                return

        content_text = (msg.text or msg.caption or '').strip()

        if settings and settings.cancel_channel_pin:
            # Wait a moment for Telegram to auto-pin, then unpin — in the background so
            # sequential update processing is not stalled (album parts must arrive within
            # the forward-rule debounce window).
            async def _delayed_unpin(bot=context.bot, chat_id=chat.id, message_id=msg.message_id):
                await asyncio.sleep(1)
                try:
                    await bot.unpin_chat_message(chat_id=chat_id, message_id=message_id)
                    print(f"Unpinned channel message {message_id} in chat {chat_id}")
                except Exception as e:
                    print(f"Error unpinning channel message: {e}")

            _unpin_task = asyncio.create_task(_delayed_unpin())
            _background_tasks.add(_unpin_task)
            _unpin_task.add_done_callback(_background_tasks.discard)

        if settings and settings.channel_discussion_keyword_filter and text_matches_keywords(content_text, settings.channel_discussion_keyword_filter):
            try:
                await msg.delete()
                # Deleted album caption → drop the whole album from forward rules
                if getattr(msg, 'media_group_id', None) and forward_rules:
                    _enqueue_forward_media_group_item(
                        bot=context.bot,
                        clone_id=context.application.bot_data.get('clone_id'),
                        chat_id=chat.id,
                        msg=msg,
                        rules=forward_rules,
                        blocked=True,
                    )
                return
            except Exception as e:
                print(f"Error deleting channel discussion message by keyword filter: {e}")

        matched_rule_names = []

        async def _send_template_message(template_data):
            reply_markup = None
            try:
                kb = build_inline_keyboard_from_links(json.loads(template_data.get('links') or '[]'))
                reply_markup = InlineKeyboardMarkup(kb) if kb else None
            except Exception:
                reply_markup = None
            send_kwargs = {
                'chat_id': chat.id,
                'reply_markup': reply_markup,
            }
            if template_data.get('send_as_reply'):
                send_kwargs['reply_to_message_id'] = msg.message_id

            media_type = template_data.get('media_type') or 'text'
            media_url = template_data.get('media_url')
            content = sanitize_html_for_telegram(template_data.get('content') or '')
            if media_type == 'image' and media_url:
                await context.bot.send_photo(
                    photo=media_url,
                    caption=content or None,
                    parse_mode='HTML' if content else None,
                    **send_kwargs
                )
            elif media_type == 'video' and media_url:
                await context.bot.send_video(
                    video=media_url,
                    caption=content or None,
                    parse_mode='HTML' if content else None,
                    **send_kwargs
                )
            elif content:
                await context.bot.send_message(
                    text=content,
                    parse_mode='HTML',
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    **send_kwargs
                )

        for template_data in templates:
            if text_matches_keywords(content_text, template_data.get('trigger_keywords')):
                try:
                    await _send_template_message(template_data)
                    matched_rule_names.append(f"模板:{template_data['template_name']}")
                except Exception as template_err:
                    print(f"Error sending channel template {template_data['template_name']}: {template_err}")

        if settings and settings.channel_auto_buttons:
            coupon_links = []
            coupon_lines = []
            matched_coupons = [coupon for coupon in coupons if coupon.get('button_url') and text_matches_keywords(content_text, coupon.get('trigger_keywords'))]
            for idx, coupon in enumerate(matched_coupons):
                coupon_links.append({
                    'text': coupon.get('button_text') or coupon.get('coupon_name') or f'按钮{idx + 1}',
                    'url': coupon.get('button_url'),
                    'row': idx // 2,
                    'order': idx % 2,
                })
                if coupon.get('coupon_code'):
                    coupon_lines.append(f"• {coupon['coupon_name']}：<code>{coupon['coupon_code']}</code>")
                matched_rule_names.append(f"优惠券:{coupon['coupon_name']}")
            if coupon_links:
                try:
                    await context.bot.send_message(
                        chat_id=chat.id,
                        reply_to_message_id=msg.message_id,
                        text='🎁 <b>频道福利</b>\n' + ('\n'.join(coupon_lines) if coupon_lines else '点击下方按钮查看详情'),
                        parse_mode='HTML',
                        reply_markup=InlineKeyboardMarkup(build_inline_keyboard_from_links(coupon_links)),
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
                except Exception as coupon_err:
                    print(f"Error sending channel coupon buttons: {coupon_err}")

        if forward_rules and getattr(msg, 'media_group_id', None):
            # Album part: buffer and send as one group after the last part arrives.
            # Keyword rules are evaluated once on the album caption (see _flush_forward_media_group).
            _enqueue_forward_media_group_item(
                bot=context.bot,
                clone_id=context.application.bot_data.get('clone_id'),
                chat_id=chat.id,
                msg=msg,
                rules=forward_rules,
            )
            forward_rules_for_msg = []
        else:
            forward_rules_for_msg = forward_rules

        for rule in forward_rules_for_msg:
            if not text_matches_keywords(content_text, rule.get('source_keywords')):
                continue
            try:
                kwargs = {
                    'chat_id': rule['target_chat_id'],
                    'from_chat_id': chat.id,
                    'message_id': msg.message_id,
                }
                if rule.get('target_thread_id'):
                    kwargs['message_thread_id'] = rule['target_thread_id']
                from app.services.sync_loop_guard import track_bot
                fwd_bot = track_bot(context.bot)  # record outputs → precise anti-loop
                if rule.get('forward_mode') == 'forward' and not getattr(msg, 'reply_markup', None):
                    await fwd_bot.forward_message(**kwargs)
                else:
                    # copyMessage drops inline keyboards unless reply_markup is passed again
                    if getattr(msg, 'reply_markup', None):
                        kwargs['reply_markup'] = msg.reply_markup
                    await fwd_bot.copy_message(**kwargs)
                matched_rule_names.append(f"转发:{rule['rule_name']}")
            except Exception as rule_err:
                print(f"Error applying forward rule {rule['rule_name']}: {rule_err}")

        with global_flask_app.app_context():
            db.session.add(ChannelMessageStats(
                group_id=stats_group_id,
                channel_chat_id=str(msg.sender_chat.id) if msg.sender_chat else None,
                channel_message_id=getattr(msg, 'forward_from_message_id', None) or msg.message_id,
                discussion_chat_id=str(chat.id),
                discussion_message_id=msg.message_id,
                media_type='image' if msg.photo else ('video' if msg.video else 'text'),
                message_text=content_text[:1000] if content_text else None,
                matched_rules=json.dumps(matched_rule_names, ensure_ascii=False)
            ))
            db.session.commit()
                    
    except Exception as e:
        print(f"Error in handle_channel_pin: {e}")

async def cmd_lottery_draw(update: Update, context):
    """手动开奖命令 /lottery_draw <lottery_id>"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'lottery'):
        return
    
    # Check if user is admin
    is_admin = await is_user_admin_in_group(context.bot, chat.id, user.id)
    if not is_admin:
        _sched_del(context, await update.message.reply_text("❌ 只有管理员才能手动开奖"), 20)
        return
    
    # Parse lottery_id
    if len(context.args) < 1:
        _sched_del(context, await update.message.reply_text("❌ 用法: /lottery_draw <抽奖ID>\n例如: /lottery_draw 1"), 20)
        return
    
    try:
        lottery_id = int(context.args[0])
    except ValueError:
        _sched_del(context, await update.message.reply_text("❌ 无效的抽奖ID，请输入数字"), 20)
        return
    
    if not global_flask_app:
        return
    
    def _draw_lottery():
        with global_flask_app.app_context():
            try:
                group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                if not group:
                    return "error", "群组未找到"
                
                lottery = GroupLottery.query.filter_by(
                    id=lottery_id,
                    group_id=group.id
                ).first()
                
                if not lottery:
                    return "error", "抽奖不存在"
                
                if lottery.status == 'ended':
                    return "error", "抽奖已经结束"
                
                if lottery.status != 'active':
                    return "error", "抽奖未激活"
                
                # Perform the draw
                winners = []
                
                if lottery.lottery_type == 'message_count':
                    # Weighted random based on message count
                    message_counts = LotteryMessageCount.query.filter_by(
                        lottery_id=lottery.id
                    ).filter(LotteryMessageCount.message_count > 0).order_by(
                        LotteryMessageCount.message_count.desc()
                    ).limit(MAX_LOTTERY_MESSAGE_COUNT_RECORDS).all()
                    
                    if message_counts:
                        participants = [(mc.user_id, mc.message_count) for mc in message_counts]
                        total_messages = sum(count for _, count in participants)
                        
                        if total_messages > 0:
                            rand_val = random.uniform(0, total_messages)
                            cumulative = 0
                            for uid, count in participants:
                                cumulative += count
                                if cumulative >= rand_val:
                                    winners = [uid]
                                    break
                    else:
                        # Fallback to random group user
                        eligible_users = GroupUser.query.filter_by(group_id=group.id).limit(MAX_LOTTERY_PARTICIPANTS).all()
                        if eligible_users:
                            winner = random.choice(eligible_users)
                            winners = [winner.tg_id]
                
                elif lottery.lottery_type == 'message_rank':
                    # Top N senders
                    top_senders = LotteryMessageCount.query.filter_by(
                        lottery_id=lottery.id
                    ).order_by(LotteryMessageCount.message_count.desc()).limit(
                        min(lottery.top_n_winners or 1, MAX_AUCTION_WINNERS)
                    ).all()
                    
                    winners = [mc.user_id for mc in top_senders]
                    
                    if not winners:
                        # Fallback
                        top_users = GroupUser.query.filter_by(group_id=group.id).limit(
                            min(lottery.top_n_winners or 1, MAX_AUCTION_WINNERS)
                        ).all()
                        winners = [u.tg_id for u in top_users]

                elif lottery.lottery_type == 'random':
                    # Pure random draw from all registered group members
                    top_n = min(lottery.top_n_winners or 1, MAX_AUCTION_WINNERS)
                    all_users = GroupUser.query.filter_by(group_id=group.id).all()
                    if all_users:
                        sample_size = min(top_n, len(all_users))
                        chosen = random.sample(all_users, sample_size)
                        winners = [u.tg_id for u in chosen]
                
                # Update lottery
                lottery.winner_ids = json.dumps(winners)
                lottery.status = 'ended'
                db.session.commit()
                
                return "success", winners, lottery.lottery_name, lottery.prize_description
            
            except Exception as e:
                db.session.rollback()
                print(f"Error drawing lottery: {e}")
                traceback.print_exc()
                return "error", f"开奖失败: {str(e)}"
    
    result = await asyncio.get_running_loop().run_in_executor(None, _draw_lottery)
    
    if result[0] == "error":
        _sched_del(context, await update.message.reply_text(f"❌ {result[1]}"), 20)
    else:
        _, winners, lottery_name, prize_desc = result
        
        if winners:
            winner_mentions = [f"<a href='tg://user?id={uid}'>用户{uid}</a>" for uid in winners]
            message = f"🎉 <b>抽奖结束！</b>\n\n"
            message += f"活动：{lottery_name}\n"
            message += f"获奖者：{', '.join(winner_mentions)}\n"
            if prize_desc:
                message += f"奖品：{prize_desc}\n"
            
            _sched_del(context, await update.message.reply_text(message, parse_mode='HTML'), 300)
        else:
            _sched_del(context, await update.message.reply_text("⚠️ 开奖完成，但未找到符合条件的获奖者"), 300)


async def cmd_lottery_history(update: Update, context):
    """查看抽奖历史命令 /lottery_history"""
    chat = update.effective_chat
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'lottery'):
        return
    
    if not global_flask_app:
        return
    
    def _get_lottery_history():
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return []
            
            # Get recent ended lotteries
            lotteries = GroupLottery.query.filter_by(
                group_id=group.id,
                status='ended'
            ).order_by(GroupLottery.end_time.desc()).limit(10).all()
            
            return lotteries
    
    lotteries = await asyncio.get_running_loop().run_in_executor(None, _get_lottery_history)
    
    if not lotteries:
        _sched_del(context, await update.message.reply_text("📭 暂无抽奖历史记录"), 20)
        return
    
    message_lines = ["🏆 抽奖历史记录\n"]
    message_lines.append("━━━━━━━━━━━━━━━━")
    
    for lottery in lotteries:
        message_lines.append(f"\n📌 {lottery.lottery_name}")
        message_lines.append(f"   类型: {'消息数量抽奖' if lottery.lottery_type == 'message_count' else '消息排名抽奖'}")
        if lottery.prize_description:
            message_lines.append(f"   奖品: {lottery.prize_description}")
        if lottery.end_time:
            message_lines.append(f"   结束时间: {lottery.end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Parse winners
        try:
            winner_ids = json.loads(lottery.winner_ids or '[]')
            if winner_ids:
                winner_mentions = [f"<a href='tg://user?id={uid}'>用户{uid}</a>" for uid in winner_ids[:5]]
                message_lines.append(f"   获奖者: {', '.join(winner_mentions)}")
                if len(winner_ids) > 5:
                    message_lines.append(f"   （共{len(winner_ids)}位获奖者）")
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            print(f"Error parsing winner_ids for lottery history: {e}")
    
    _sched_del(context, await update.message.reply_text("\n".join(message_lines), parse_mode='HTML'), 300)


async def cmd_lottery(update: Update, context):
    """查看当前活跃抽奖列表 /lottery 或 /lottery_list"""
    chat = update.effective_chat
    
    # Only work in groups
    if chat.type not in ['group', 'supergroup']:
        _sched_del(context, await update.message.reply_text("❌ 此命令只能在群组中使用"), 20)
        return
    if not await _ensure_plugin_enabled(update, context, 'lottery'):
        return
    
    if not global_flask_app:
        return
    
    def _get_active_lotteries():
        with global_flask_app.app_context():
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return []
            
            # Get active lotteries
            now = get_beijing_now()
            lotteries = GroupLottery.query.filter_by(
                group_id=group.id,
                status='active'
            ).order_by(GroupLottery.end_time).all()
            
            # Return lotteries with additional context
            results = []
            for lottery in lotteries:
                # Check if still within time window
                if lottery.start_time and lottery.end_time:
                    if lottery.start_time <= now <= lottery.end_time:
                        # Get participant count
                        participant_count = LotteryMessageCount.query.filter_by(
                            lottery_id=lottery.id
                        ).filter(LotteryMessageCount.message_count > 0).count()
                        
                        results.append({
                            'id': lottery.id,
                            'name': lottery.lottery_name,
                            'type': lottery.lottery_type,
                            'prize': lottery.prize_description,
                            'end_time': lottery.end_time,
                            'min_messages': lottery.min_messages,
                            'top_n_winners': lottery.top_n_winners,
                            'participant_count': participant_count
                        })
            
            return results
    
    lotteries = await asyncio.get_running_loop().run_in_executor(None, _get_active_lotteries)
    
    if not lotteries:
        _sched_del(context, await update.message.reply_text("📭 当前没有进行中的抽奖活动"), 20)
        return
    
    message_lines = ["🎰 进行中的抽奖活动\n"]
    message_lines.append("━━━━━━━━━━━━━━━━")
    
    for lottery in lotteries:
        message_lines.append(f"\n🎁 #{lottery['id']} {lottery['name']}")
        
        if lottery['type'] == 'message_count':
            message_lines.append(f"   类型: 消息数量抽奖")
            message_lines.append(f"   最少发言: {lottery['min_messages']}条")
        else:
            message_lines.append(f"   类型: 消息排名抽奖")
            message_lines.append(f"   获奖人数: 前{lottery['top_n_winners']}名")
        
        if lottery['prize']:
            message_lines.append(f"   奖品: {lottery['prize']}")
        
        if lottery['end_time']:
            message_lines.append(f"   结束时间: {lottery['end_time'].strftime('%Y-%m-%d %H:%M:%S')}")
        
        message_lines.append(f"   当前参与人数: {lottery['participant_count']}人")
    
    message_lines.append("\n━━━━━━━━━━━━━━━━")
    message_lines.append("💡 提示: 在群内发言即可参与抽奖")
    
    _sched_del(context, await update.message.reply_text("\n".join(message_lines)), 300)


async def check_auction_expiration(context):
    """Background task to check and notify auction expiration"""
    if not global_flask_app:
        return
    
    try:
        def _get_expired_auctions():
            with global_flask_app.app_context():
                now = get_beijing_now()
                # Find active auctions that have expired; return only IDs to avoid detached objects
                return [row.id for row in PointsAuction.query.filter(
                    PointsAuction.status == 'active',
                    PointsAuction.auction_end <= now
                ).all()]
        
        auction_ids = await asyncio.get_running_loop().run_in_executor(None, _get_expired_auctions)
        
        for auction_id in auction_ids:
            try:
                with global_flask_app.app_context():
                    # Re-query inside this context so the object is attached to the current session
                    auction = PointsAuction.query.get(auction_id)
                    if not auction or auction.status != 'active':
                        continue
                    group = BotGroup.query.get(auction.group_id)
                    if not group:
                        continue
                    if not is_plugin_enabled(auction.group_id, 'points'):
                        continue
                    
                    chat_id = int(group.chat_id)
                    group_clone_id = group.clone_id
                    
                    # Update auction status
                    auction.status = 'ended'
                    db.session.commit()
                    
                    # Collect values for the Telegram message (outside context)
                    item_name = auction.item_name
                    item_description = auction.item_description
                    current_bidder_id = auction.current_bidder_id
                    current_bid = auction.current_bid
                    conf = get_group_conf(group)
                    auction_channel_id = conf.get('auction_channel_id', '').strip() or None
                
                # Notify winner (outside app_context so we can await)
                bot = _get_bot_for_clone(group_clone_id, context.bot)
                if bot is None:
                    continue
                if current_bidder_id and current_bid > 0:
                    message = (
                        f"🎉 <b>竞拍结束！</b>\n\n"
                        f"📦 物品: {item_name}\n"
                        f"💰 成交价: {current_bid} 积分\n"
                        f"👤 获胜者: <a href='tg://user?id={current_bidder_id}'>用户{current_bidder_id}</a>\n\n"
                    )
                    if item_description:
                        message += f"📝 描述: {item_description}\n"
                    message += "请获胜者联系管理员领取物品！"
                else:
                    message = (
                        f"📭 <b>竞拍结束</b>\n\n"
                        f"📦 物品: {item_name}\n"
                        f"⚠️ 无人出价，流拍"
                    )
                
                await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode='HTML'
                )

                # Also push result to auction channel if configured
                if auction_channel_id:
                    try:
                        await bot.send_message(
                            chat_id=auction_channel_id,
                            text=message,
                            parse_mode='HTML'
                        )
                    except Exception as ch_err:
                        print(f"Error sending auction result to channel {auction_channel_id}: {ch_err}")
                    
            except Exception as e:
                print(f"Error notifying auction end {auction_id}: {e}")
                traceback.print_exc()
                
    except Exception as e:
        print(f"Error in check_auction_expiration: {e}")
        traceback.print_exc()


async def check_redpacket_expiration(context):
    """Background task to check and expire red packets"""
    if not global_flask_app:
        return
    
    try:
        def _get_expired_packets():
            with global_flask_app.app_context():
                now = get_beijing_now()
                # Find active red packets that have expired; return only IDs to avoid detached objects
                return [row.id for row in RedPacket.query.filter(
                    RedPacket.status == 'active',
                    RedPacket.expire_time <= now
                ).all()]
        
        packet_ids = await asyncio.get_running_loop().run_in_executor(None, _get_expired_packets)
        
        for packet_id in packet_ids:
            try:
                with global_flask_app.app_context():
                    # Re-query inside this context so the object is attached to the current session
                    packet = RedPacket.query.get(packet_id)
                    if not packet or packet.status != 'active':
                        continue
                    
                    group = BotGroup.query.get(packet.group_id)
                    if group is None:
                        continue
                    if not is_plugin_enabled(packet.group_id, 'games'):
                        continue
                    
                    # Refund remaining points to creator
                    if packet.remaining_points > 0:
                        user_points = UserPoints.query.filter_by(
                            group_id=group.id,
                            user_id=packet.creator_id
                        ).first()
                        
                        if not user_points:
                            user_points = UserPoints(
                                group_id=group.id,
                                user_id=packet.creator_id,
                                points_balance=0
                            )
                            db.session.add(user_points)
                        
                        user_points.points_balance += packet.remaining_points
                        
                        # Log the refund
                        log = PointsLog(
                            group_id=group.id,
                            user_id=packet.creator_id,
                            points_change=packet.remaining_points,
                            reason="红包过期退款",
                            balance_after=user_points.points_balance
                        )
                        db.session.add(log)
                    
                    # Update packet status
                    packet.status = 'expired'
                    db.session.commit()
                    
            except Exception as e:
                db.session.rollback()
                print(f"Error expiring red packet {packet_id}: {e}")
                traceback.print_exc()
                
    except Exception as e:
        print(f"Error in check_redpacket_expiration: {e}")
        traceback.print_exc()


async def check_and_send_votes(context):
    """Background job: auto-send pending votes to group when start_time arrives; auto-close expired votes."""
    if not global_flask_app:
        return

    try:
        def _get_pending_and_expired_vote_ids():
            with global_flask_app.app_context():
                db.session.expire_all()
                now = get_beijing_now()
                # Pending votes whose start_time has arrived (no message_id yet = not yet sent)
                pending = GroupVote.query.filter(
                    GroupVote.status == 'pending',
                    GroupVote.start_time != None,
                    GroupVote.start_time <= now,
                    GroupVote.message_id == None
                ).all()
                pending_ids = [v.id for v in pending]

                # Active votes whose end_time has passed
                expired = GroupVote.query.filter(
                    GroupVote.status == 'active',
                    GroupVote.end_time != None,
                    GroupVote.end_time <= now
                ).all()
                expired_ids = [v.id for v in expired]
                return pending_ids, expired_ids

        pending_ids, expired_ids = await asyncio.get_running_loop().run_in_executor(
            None, _get_pending_and_expired_vote_ids
        )

        # Auto-close expired votes
        if expired_ids:
            def _close_votes():
                with global_flask_app.app_context():
                    GroupVote.query.filter(GroupVote.id.in_(expired_ids)).update(
                        {'status': 'ended'}, synchronize_session=False
                    )
                    db.session.commit()
                    logging.info(f"✅ [投票任务] 已关闭 {len(expired_ids)} 个过期投票")
            await asyncio.get_running_loop().run_in_executor(None, _close_votes)

        # Auto-send pending votes
        for vote_id in pending_ids:
            try:
                def _get_vote_data(vid):
                    with global_flask_app.app_context():
                        vote = GroupVote.query.get(vid)
                        if not vote:
                            return None
                        group = BotGroup.query.get(vote.group_id)
                        if not group:
                            return None
                        try:
                            options = json.loads(vote.options or '[]')
                        except Exception:
                            options = []
                        return {
                            'id': vote.id,
                            'title': vote.title,
                            'options': options,
                            'allow_revote': vote.allow_revote,
                            'end_time': vote.end_time,
                            'chat_id': group.chat_id,
                            'clone_id': group.clone_id,
                        }

                vote_data = await asyncio.get_running_loop().run_in_executor(None, _get_vote_data, vote_id)
                if not vote_data or len(vote_data['options']) < 2:
                    continue

                bot = _get_bot_for_clone(vote_data['clone_id'], context.bot)
                if bot is None:
                    logging.warning(f"⏭️ [投票任务] 跳过: 克隆机器人未运行 (投票ID: {vote_id})")
                    continue

                buttons = []
                for idx, option in enumerate(vote_data['options']):
                    buttons.append([InlineKeyboardButton(option, callback_data=f"vote_{vote_data['id']}_{idx}")])
                buttons.append([InlineKeyboardButton("📊 查看结果", callback_data=f"vote_result_{vote_data['id']}")])
                keyboard = InlineKeyboardMarkup(buttons)

                end_info = ""
                if vote_data['end_time']:
                    end_info = f"\n⏰ 截止：{vote_data['end_time'].strftime('%Y-%m-%d %H:%M:%S')}"
                revote_info = "✅ 允许改投" if vote_data['allow_revote'] else "🔒 不允许改投"

                sent_msg = await bot.send_message(
                    chat_id=vote_data['chat_id'],
                    text=f"📊 <b>投票：{vote_data['title']}</b>\n\n👆 点击下方按钮投票{end_info}\n{revote_info}",
                    reply_markup=keyboard,
                    parse_mode='HTML'
                )

                def _mark_vote_active(vid, mid):
                    with global_flask_app.app_context():
                        v = GroupVote.query.get(vid)
                        if v:
                            v.status = 'active'
                            v.message_id = mid
                            db.session.commit()

                await asyncio.get_running_loop().run_in_executor(None, _mark_vote_active, vote_id, sent_msg.message_id)
                logging.info(f"📢 [投票任务] 已发送投票 '{vote_data['title']}' (ID: {vote_id}) 到群组 {vote_data['chat_id']}")

            except Exception as e:
                logging.error(f"❌ [投票任务] 处理投票 {vote_id} 失败: {e}")

    except Exception as e:
        logging.error(f"❌ [投票任务] check_and_send_votes 执行失败: {e}")
        logging.error(traceback.format_exc())


async def quiz_answer_callback(update: Update, context):
    """处理问答答案选择"""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    
    # 解析回调数据: quiz_answer_{quiz_id}_{answer_idx}
    try:
        _, _, quiz_id, answer_idx = query.data.split('_')
        quiz_id = int(quiz_id)
        answer_idx = int(answer_idx)
    except:
        await query.answer("❌ 无效的选择")
        return
    
    if not global_flask_app:
        return
    
    def _process_answer():
        with global_flask_app.app_context():
            # 1. 获取问题和会话
            quiz = QuizGame.query.get(quiz_id)
            if not quiz:
                return None, "问题不存在"
            
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                return None, "群组不存在"
            
            session = QuizSession.query.filter_by(
                group_id=group.id,
                quiz_id=quiz_id,
                status='active'
            ).first()
            
            if not session:
                return None, "问答已结束"
            
            # 2. 检查是否已回答
            existing = QuizAnswer.query.filter_by(
                session_id=session.id,
                user_id=user.id
            ).first()
            
            if existing:
                return None, "您已经回答过了"
            
            # 3. 检查是否超时
            if get_beijing_now() > session.start_time + timedelta(seconds=quiz.time_limit):
                session.status = 'ended'
                db.session.commit()
                return None, "回答超时"
            
            # 4. 验证答案
            is_correct = (answer_idx == quiz.correct_answer_index)
            points = quiz.points_reward if is_correct else 0
            
            # 5. 记录答案
            answer = QuizAnswer(
                session_id=session.id,
                user_id=user.id,
                answer_index=answer_idx,
                is_correct=is_correct,
                points_awarded=points
            )
            db.session.add(answer)
            
            # 6. 更新用户积分
            if is_correct and points > 0:
                user_points = UserPoints.query.filter_by(
                    group_id=group.id,
                    user_id=user.id
                ).first()
                
                if not user_points:
                    user_points = UserPoints(
                        group_id=group.id,
                        user_id=user.id,
                        points_balance=0
                    )
                    db.session.add(user_points)
                
                user_points.points_balance += points
                
                # 记录积分日志
                log = PointsLog(
                    group_id=group.id,
                    user_id=user.id,
                    points_change=points,
                    reason=f"答对问答题",
                    balance_after=user_points.points_balance
                )
                db.session.add(log)
            
            db.session.commit()
            
            return {
                'is_correct': is_correct,
                'points': points,
                'explanation': quiz.explanation
            }, None
    
    result, error = await asyncio.get_running_loop().run_in_executor(None, _process_answer)
    
    if error:
        await query.answer(f"❌ {error}")
        return
    
    if result['is_correct']:
        msg = f"✅ 回答正确！\n🎁 获得 {result['points']} 积分"
    else:
        msg = "❌ 回答错误"
    
    if result.get('explanation'):
        msg += f"\n\n💡 {result['explanation']}"
    
    await query.answer(msg, show_alert=True)

async def redpacket_claim_callback(update: Update, context):
    """处理红包领取"""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    
    try:
        packet_id = int(query.data.split('_')[2])
    except:
        await query.answer("❌ 无效的红包")
        return
    
    if not global_flask_app:
        return
    
    def _claim_packet():
        with global_flask_app.app_context():
            import random
            
            # 获取红包（加行锁防止并发超领）
            packet = RedPacket.query.with_for_update().get(packet_id)
            if not packet:
                return None, "红包不存在"
            
            if packet.status != 'active':
                return None, "红包已过期"
            
            if packet.remaining_count <= 0:
                return None, "红包已被抢完"
            
            group = BotGroup.query.get(packet.group_id)
            if not group or str(chat.id) != group.chat_id:
                return None, "群组不匹配"
            
            # 检查是否已领取
            existing = RedPacketClaim.query.filter_by(
                packet_id=packet_id,
                user_id=user.id
            ).first()
            
            if existing:
                return None, "您已经领取过了"
            
            # 计算分配积分（随机或平均）
            if packet.packet_type == 'random':
                # 拼手气红包：最后一个人拿剩余的，其他人随机
                if packet.remaining_count == 1:
                    points = packet.remaining_points
                else:
                    # 随机范围：1 到 (剩余积分 / 剩余数量 * 2)
                    max_points = int(packet.remaining_points / packet.remaining_count * 2)
                    points = random.randint(1, max(1, max_points))
            else:
                # 普通红包：平均分配
                points = packet.remaining_points // packet.remaining_count
            
            # 更新红包状态
            packet.remaining_count -= 1
            packet.remaining_points -= points
            
            if packet.remaining_count == 0:
                packet.status = 'claimed'
            
            # 记录领取
            claim = RedPacketClaim(
                packet_id=packet_id,
                user_id=user.id,
                points_received=points
            )
            db.session.add(claim)
            
            # 更新用户积分
            user_points = UserPoints.query.filter_by(
                group_id=group.id,
                user_id=user.id
            ).first()
            
            if not user_points:
                user_points = UserPoints(
                    group_id=group.id,
                    user_id=user.id,
                    points_balance=0
                )
                db.session.add(user_points)
            
            user_points.points_balance += points
            
            # 记录积分日志
            log = PointsLog(
                group_id=group.id,
                user_id=user.id,
                points_change=points,
                reason="领取红包",
                balance_after=user_points.points_balance
            )
            db.session.add(log)
            
            db.session.commit()
            
            return {
                'points': points,
                'remaining': packet.remaining_count
            }, None
    
    result, error = await asyncio.get_running_loop().run_in_executor(None, _claim_packet)
    
    if error:
        await query.answer(f"❌ {error}")
        return
    
    await query.answer(
        f"✅ 领取成功！获得 {result['points']} 积分\n"
        f"剩余 {result['remaining']} 个红包",
        show_alert=True
    )

async def vote_callback(update: Update, context):
    """处理投票回调"""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    
    # Parse callback data: vote_{vote_id}_{option_idx} or vote_result_{vote_id}
    try:
        parts = query.data.split('_')
        if parts[1] == 'result':
            # Show vote results
            vote_id = int(parts[2])
            return await show_vote_results(query, user, chat, vote_id)
        else:
            # Cast vote
            vote_id = int(parts[1])
            option_idx = int(parts[2])
            return await cast_vote(query, user, chat, vote_id, option_idx)
    except (IndexError, ValueError):
        await query.answer("❌ 无效的投票数据")
        return


async def cast_vote(query, user, chat, vote_id, option_idx):
    """处理用户投票"""
    if not global_flask_app:
        return
    
    def _process_vote():
        with global_flask_app.app_context():
            # Get vote
            vote = GroupVote.query.get(vote_id)
            if not vote:
                return None, "投票不存在"
            
            if vote.status != 'active':
                return None, "投票已结束"
            
            # Check if vote ended
            if vote.end_time and get_beijing_now() > vote.end_time:
                vote.status = 'ended'
                db.session.commit()
                return None, "投票已结束"
            
            # Get options
            options = json.loads(vote.options)
            if option_idx < 0 or option_idx >= len(options):
                return None, "无效的选项"
            
            # Check if user already voted
            existing = VoteRecord.query.filter_by(
                vote_id=vote_id,
                user_id=user.id
            ).first()
            
            if existing:
                if not vote.allow_revote:
                    return None, "您已经投过票了"
                # Update existing vote
                existing.choices = json.dumps([option_idx])
                existing.updated_at = get_beijing_now()
            else:
                # Create new vote record
                record = VoteRecord(
                    vote_id=vote_id,
                    user_id=user.id,
                    choices=json.dumps([option_idx])
                )
                db.session.add(record)
            
            db.session.commit()
            return {'option': options[option_idx], 'updated': existing is not None}, None
    
    result, error = await asyncio.get_running_loop().run_in_executor(None, _process_vote)
    
    if error:
        await query.answer(f"❌ {error}")
        return
    
    action = "已改投" if result['updated'] else "投票成功"
    await query.answer(f"✅ {action}：{result['option']}")


async def show_vote_results(query, user, chat, vote_id):
    """显示投票结果"""
    if not global_flask_app:
        return
    
    def _get_results():
        with global_flask_app.app_context():
            vote = GroupVote.query.get(vote_id)
            if not vote:
                return None, "投票不存在"
            
            options = json.loads(vote.options)
            
            # Count votes for each option
            vote_counts = [0] * len(options)
            total_votes = 0
            
            records = VoteRecord.query.filter_by(vote_id=vote_id).all()
            for record in records:
                choices = json.loads(record.choices)
                for choice in choices:
                    if 0 <= choice < len(options):
                        vote_counts[choice] += 1
                        total_votes += 1
            
            return {
                'title': vote.title,
                'options': options,
                'counts': vote_counts,
                'total': total_votes,
                'status': vote.status
            }, None
    
    result, error = await asyncio.get_running_loop().run_in_executor(None, _get_results)
    
    if error:
        await query.answer(f"❌ {error}")
        return
    
    # Build results message
    msg = f"📊 <b>{result['title']}</b>\n\n"
    msg += f"总投票数：{result['total']}\n\n"
    
    for idx, (option, count) in enumerate(zip(result['options'], result['counts'])):
        percentage = (count / result['total'] * 100) if result['total'] > 0 else 0
        bar_length = int(percentage / 5)  # 20 chars max
        bar = "█" * bar_length + "░" * (20 - bar_length)
        msg += f"{option}\n{bar} {count} 票 ({percentage:.1f}%)\n\n"
    
    if result['status'] == 'ended':
        msg += "⏰ 投票已结束"
    
    await query.answer()
    await query.message.reply_text(msg, parse_mode='HTML')

async def display_bottom_buttons(chat_id, context, use_reply_keyboard=False):
    """显示群底部按钮
    
    Args:
        chat_id: 群组ID
        context: 上下文
        use_reply_keyboard: 是否使用回复键盘（菜单键盘），默认False（使用内联键盘）
    
    Returns:
        ReplyKeyboardMarkup 或 InlineKeyboardMarkup
    """
    if not global_flask_app:
        return None
    
    try:
        def _get_buttons():
            with global_flask_app.app_context():
                group = BotGroup.query.filter_by(chat_id=str(chat_id), clone_id=context.application.bot_data.get('clone_id')).first()
                if not group:
                    return []
                
                buttons = GroupBottomButton.query.filter_by(
                    group_id=group.id,
                    is_active=True
                ).order_by(GroupBottomButton.row_position, GroupBottomButton.button_order).all()
                
                return buttons
        
        buttons = await asyncio.get_running_loop().run_in_executor(None, _get_buttons)
        
        if not buttons:
            return None
        
        if use_reply_keyboard:
            # Build reply keyboard (menu keyboard) - only button text, no URLs
            keyboard = []
            current_row = []
            current_row_num = buttons[0].row_position  # Safe because we checked buttons is not empty
            
            # Get the input field placeholder from the first button that has one
            input_placeholder = None
            for button in buttons:
                if button.input_field_placeholder:
                    input_placeholder = button.input_field_placeholder
                    break
            
            for button in buttons:
                # Start a new row if row_position changes
                if button.row_position != current_row_num:
                    if current_row:
                        keyboard.append(current_row)
                    current_row = []
                    current_row_num = button.row_position
                
                # Reply keyboard buttons don't support URLs, just text
                current_row.append(KeyboardButton(button.button_text))
            
            # Add the last row
            if current_row:
                keyboard.append(current_row)
            
            if keyboard:
                return ReplyKeyboardMarkup(
                    keyboard, 
                    resize_keyboard=True,
                    one_time_keyboard=False,
                    input_field_placeholder=input_placeholder
                )
            return None
        else:
            # Build inline keyboard (original behavior)
            keyboard = []
            current_row = []
            current_row_num = buttons[0].row_position  # Safe because we checked buttons is not empty
            
            for button in buttons:
                # Start a new row if row_position changes
                if button.row_position != current_row_num:
                    if current_row:
                        keyboard.append(current_row)
                    current_row = []
                    current_row_num = button.row_position
                
                if button.button_url:
                    current_row.append(InlineKeyboardButton(
                        button.button_text,
                        url=button.button_url
                    ))
                elif button.button_callback:
                    current_row.append(InlineKeyboardButton(
                        button.button_text,
                        callback_data=button.button_callback
                    ))
            
            # Add the last row
            if current_row:
                keyboard.append(current_row)
            
            if keyboard:
                return InlineKeyboardMarkup(keyboard)
            
            return None
        
    except Exception as e:
        print(f"Error displaying bottom buttons: {e}")
        return None

def _is_clone_admin(clone_id: int, user_id: int) -> bool:
    """Check if user_id is an admin (owner or in admin_user_ids) of the given BotClone."""
    with global_flask_app.app_context():
        clone = BotClone.query.get(clone_id)
        if not clone:
            return False
        if clone.owner_user_id and clone.owner_user_id == user_id:
            return True
        try:
            admin_ids = json.loads(clone.admin_user_ids or '[]')
            return user_id in admin_ids
        except (json.JSONDecodeError, TypeError) as e:
            print(f"⚠️ Failed to parse admin_user_ids for clone {clone_id}: {e}")
            return False


async def cmd_start(update: Update, context):
    print(f"✅ /start 命令被触发，用户 ID: {update.effective_user.id}")
    user_id = update.effective_user.id
    tg_user = update.effective_user
    chat = update.effective_chat
    admin_id = safe_int(os.getenv('ADMIN_ID', 0))
    
    # Build a display name: prefer username, fallback to first_name + last_name
    _display_name = tg_user.username or (
        ((tg_user.first_name or '') + (' ' + tg_user.last_name if tg_user.last_name else '')).strip()
    ) or str(user_id)
    
    print(f"📍 /start 调试: chat_type={chat.type}, chat_id={chat.id}, user_id={user_id}")
    
    # /start command should only work in private chat, not in groups
    if chat.type in ['group', 'supergroup']:
        print(f"⚠️ /start 调试: 在群组中使用 /start 命令，忽略")
        return

    # Handle invitation deep link: /start inv_{inviter_id}_{group_db_id}
    start_param = context.args[0] if context.args else ''
    if start_param.startswith('inv_'):
        parts = start_param.split('_')
        if len(parts) == 3:
            try:
                inv_inviter_id = int(parts[1])
                inv_group_db_id = int(parts[2])
            except (ValueError, IndexError):
                inv_inviter_id = inv_group_db_id = None

            if inv_inviter_id and inv_group_db_id and inv_inviter_id != user_id:
                def _record_pending_referral():
                    with global_flask_app.app_context():
                        group = BotGroup.query.get(inv_group_db_id)
                        if not group:
                            return None, None
                        activity = InvitationActivity.query.filter_by(
                            group_id=inv_group_db_id, enabled=True
                        ).first()
                        if not activity:
                            return None, None
                        # Store pending referral (ignore if already exists)
                        existing = PendingReferral.query.filter_by(
                            user_id=user_id, group_id=inv_group_db_id
                        ).first()
                        if not existing:
                            ref = PendingReferral(
                                user_id=user_id,
                                inviter_id=inv_inviter_id,
                                group_id=inv_group_db_id
                            )
                            db.session.add(ref)
                            db.session.commit()
                        return group.title, group.chat_id

                group_title, group_chat_id = await asyncio.get_running_loop().run_in_executor(
                    None, _record_pending_referral
                )

                if group_title:
                    # Try to obtain the group invite link so we can send the user a join button
                    join_button = None
                    try:
                        chat_info = await context.bot.get_chat(int(group_chat_id))
                        join_url = chat_info.invite_link
                        if not join_url and chat_info.username:
                            join_url = f"https://t.me/{chat_info.username}"
                        if join_url:
                            join_button = InlineKeyboardMarkup(
                                [[InlineKeyboardButton(f"🚀 加入 {group_title}", url=join_url)]]
                            )
                    except Exception as e:
                        print(f"⚠️ [/start inv] 获取群组链接失败: {e}", flush=True)

                    await update.message.reply_html(
                        f"👋 欢迎！您通过专属邀请链接到达。\n\n"
                        f"加入 <b>{group_title}</b> 后，邀请人将获得积分奖励！",
                        reply_markup=join_button
                    )
                    return
        # Continue with normal start handling if parsing failed or same user invites themselves


    clone_id = context.application.bot_data.get('clone_id')

    # Determine if the user is an authorized admin (global or clone-specific)
    is_admin_user = (user_id == admin_id)

    if not is_admin_user and clone_id:
        # Check if user is an admin of this clone bot
        is_admin_user = await asyncio.get_running_loop().run_in_executor(
            None, _is_clone_admin, clone_id, user_id
        )

    # Handle private chat only
    if is_admin_user:
        # Create authentication session for admin
        def _create_auth_session():
            with global_flask_app.app_context():
                # Clean up old sessions for this user
                AuthSession.query.filter_by(user_id=user_id).delete()
                
                # Create new auth session
                session_token = generate_session_token()
                verification_code = generate_verification_code()
                expires_at = get_beijing_now() + timedelta(minutes=AUTH_SESSION_EXPIRY_MINUTES)
                
                auth_session = AuthSession(
                    user_id=user_id,
                    user_name=_display_name,
                    session_token=session_token,
                    verification_code=verification_code,
                    is_verified=False,
                    expires_at=expires_at,
                    clone_id=clone_id  # None for main bot admin, clone ID for clone bot admin
                )
                db.session.add(auth_session)
                db.session.commit()
                
                return session_token
        
        # Create session in executor to avoid blocking
        session_token = await asyncio.get_running_loop().run_in_executor(None, _create_auth_session)
        
        domain = os.getenv('RAILWAY_PUBLIC_DOMAIN', '').rstrip('/')
        if domain:
            verify_url = f"https://{domain}/core/auth_verify/{session_token}"
        else:
            verify_url = f"http://localhost:5000/core/auth_verify/{session_token}"
        
        # Create inline keyboard with button
        keyboard = [[InlineKeyboardButton("🔐 点击验证身份", url=verify_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        _welcome_msg = await update.message.reply_html(
            "👋 <b>欢迎，管理员！</b>\n\n"
            "请点击下方按钮进入身份验证页面，\n"
            "将页面上的验证码发送给我以完成登录。",
            reply_markup=reply_markup
        )
        _sched_del(context, _welcome_msg, 300)
    else:
        # Get custom private start message and check for expired memberships
        def _get_private_start_msg_and_check_expiration():
            with global_flask_app.app_context():
                # Try to get configuration from the most recently updated group (bot-level setting)
                bot_clone_id = context.application.bot_data.get('clone_id')
                group = BotGroup.query.filter_by(is_active=True, clone_id=bot_clone_id).order_by(BotGroup.updated_at.desc()).first()
                conf = get_group_conf(group) if group else DEFAULT_SYSTEM.copy()
                
                # Check if start_msg_open is enabled
                start_msg_enabled = conf.get('start_msg_open', False)
                
                # Query StartMessage table for active user messages
                # Search across all active groups for this bot so the message is found
                # regardless of which group was most recently updated
                start_msg_data = None
                if start_msg_enabled:
                    start_msg = (
                        StartMessage.query
                        .join(BotGroup, StartMessage.group_id == BotGroup.id)
                        .filter(
                            BotGroup.clone_id == bot_clone_id,
                            BotGroup.is_active,
                            StartMessage.message_type == 'user',
                            StartMessage.is_active
                        )
                        .order_by(StartMessage.id.desc())
                        .first()
                    )
                    
                    if start_msg:
                        # Return structured data from StartMessage table
                        try:
                            links = json.loads(start_msg.links) if start_msg.links else []
                        except (json.JSONDecodeError, TypeError) as e:
                            print(f"⚠️ [/start] Failed to parse links JSON for StartMessage ID {start_msg.id} in group {start_msg.group_id}: {e}", flush=True)
                            links = []
                        
                        start_msg_data = {
                            'media_type': start_msg.media_type,
                            'media_url': start_msg.media_url,
                            'content': start_msg.content or '',
                            'links': links
                        }
                
                # Fallback to default message if no custom message configured
                if not start_msg_data:
                    private_msg = conf.get('msg_private_start', DEFAULT_SYSTEM['msg_private_start'])
                    start_msg_data = {
                        'media_type': 'text',
                        'media_url': None,
                        'content': private_msg,
                        'links': []
                    }
                
                # Check if this user has any expired memberships in groups
                # Only check groups belonging to the current bot instance
                now = get_beijing_now()
                expired_memberships = GroupUser.query.options(
                    joinedload(GroupUser.group)
                ).join(BotGroup, GroupUser.group_id == BotGroup.id).filter(
                    GroupUser.tg_id == user_id,
                    GroupUser.expiration_date.isnot(None),
                    GroupUser.expiration_date < now,
                    GroupUser.is_banned == False,
                    BotGroup.clone_id == bot_clone_id
                ).all()
                
                # Collect expired memberships to process
                users_to_ban = []
                for group_user in expired_memberships:
                    if group_user.group and group_user.group.is_active:
                        # Store group_user.id for later database update
                        users_to_ban.append({
                            'group_user_id': group_user.id,
                            'tg_id': group_user.tg_id,
                            'group_chat_id': group_user.group.chat_id,
                            'group_id': group_user.group.id,
                            'group_title': group_user.group.title
                        })
                
                # Return data WITHOUT marking as banned - will update after successful API call
                return start_msg_data, users_to_ban
        
        start_msg_data, users_to_ban = await asyncio.get_running_loop().run_in_executor(None, _get_private_start_msg_and_check_expiration)
        
        # Helper function to mark user as banned in database after successful API call
        def _mark_user_banned_in_db(group_user_id):
            """Mark user as banned in database - called after successful API mute"""
            with global_flask_app.app_context():
                try:
                    group_user = GroupUser.query.get(group_user_id)
                    if group_user:
                        group_user.is_banned = True
                        db.session.commit()
                        return True
                except Exception as e:
                    print(f"❌ [/start] Failed to mark user {group_user_id} as banned in DB: {e}", flush=True)
                    db.session.rollback()
                    return False
            return False
        
        # Mute expired users in their groups
        if users_to_ban:
            async def mute_expired_user(user_data):
                """Mute an expired user in a group"""
                try:
                    group_user_id = user_data['group_user_id']
                    tg_id = user_data['tg_id']
                    group_chat_id = user_data['group_chat_id']
                    group_id = user_data['group_id']
                    group_title = user_data['group_title']
                    
                    # Convert chat_id to integer for Telegram API
                    chat_id_int = convert_chat_id_to_int(group_chat_id, group_id, group_title)
                    if chat_id_int is None:
                        print(f"❌ [/start] Failed to convert chat_id for group {group_id} ({group_title}), chat_id={group_chat_id}", flush=True)
                        return
                    
                    try:
                        await context.bot.restrict_chat_member(
                            chat_id=chat_id_int,
                            user_id=tg_id,
                            permissions=get_muted_permissions()
                        )
                        print(f"✅ [/start] Successfully called restrict_chat_member API - Muted expired user {tg_id} in group {group_title} (chat_id={chat_id_int})", flush=True)
                        
                        # ✅ NEW: Only mark as banned in DB after successful API call
                        success = await asyncio.get_running_loop().run_in_executor(None, _mark_user_banned_in_db, group_user_id)
                        if success:
                            print(f"✅ [/start] Marked user {tg_id} as banned in database after successful API call", flush=True)
                        else:
                            print(f"⚠️  [/start] API succeeded but failed to update database for user {tg_id}", flush=True)
                            
                    except Exception as restrict_error:
                        # 详细记录 restrict_chat_member API 调用失败的错误
                        print(f"❌ [/start] restrict_chat_member API failed for user {tg_id} in group {group_title} (chat_id={chat_id_int})", flush=True)
                        print(f"   Error type: {type(restrict_error).__name__}", flush=True)
                        print(f"   Error details: {str(restrict_error)}", flush=True)
                        print(f"   Traceback: {traceback.format_exc()}", flush=True)
                        print(f"   ⚠️  Database NOT updated - user will be retried on next check", flush=True)
                        # Don't re-raise to avoid duplicate logging in outer exception handler
                        return
                except Exception as e:
                    print(f"❌ [/start] Unexpected error muting user {user_data['tg_id']} in group {user_data['group_chat_id']} (chat_id type: {type(user_data['group_chat_id']).__name__})", flush=True)
                    print(f"   Error: {type(e).__name__}: {str(e)}", flush=True)
                    print(f"   Full traceback: {traceback.format_exc()}", flush=True)
            
            # Use semaphore to limit concurrent operations
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_BANS)
            
            async def mute_with_limit(user_data):
                async with semaphore:
                    await mute_expired_user(user_data)
            
            # Mute users in all their expired groups concurrently
            await asyncio.gather(*[mute_with_limit(user_data) for user_data in users_to_ban], return_exceptions=True)
            
            # Notify the user about expired memberships
            expired_groups = [user_data['group_title'] for user_data in users_to_ban]
            notification_msg = f"⚠️ <b>注意</b>\n\n您在以下群组的认证已过期，已被暂时禁言：\n• " + "\n• ".join(expired_groups) + "\n\n请联系管理员续费。"
            # Sanitize the notification message before sending
            sanitized_notification = sanitize_html_for_telegram(notification_msg)
            await update.message.reply_html(sanitized_notification)
        
        # Send start message with media support and inline keyboard
        # Note: All error logging uses print() for server logs only
        # User-facing messages are always generic and safe
        media_type = start_msg_data['media_type']
        content = start_msg_data['content']
        media_url = start_msg_data['media_url']
        links = start_msg_data['links']
        
        # Ensure content has a meaningful value, fallback to default if empty
        if not content:
            content = DEFAULT_SYSTEM['msg_private_start']
        
        # Build inline keyboard from links if any
        reply_markup = None
        if links:
            keyboard = build_inline_keyboard_from_links(links)
            if keyboard:
                reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send message based on media type
        try:
            if media_type == 'image' and media_url:
                await update.message.reply_photo(
                    photo=media_url,
                    caption=content,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            elif media_type == 'video' and media_url:
                await update.message.reply_video(
                    video=media_url,
                    caption=content,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            else:
                # Default to text message
                await update.message.reply_html(content, reply_markup=reply_markup)
        except Exception as e:
            print(f"❌ [/start] Failed to send start message (media_type={media_type}, has_media_url={bool(media_url)}): {e}", flush=True)
            # Fallback to simple text message (content already has fallback value)
            try:
                await update.message.reply_html(content)
            except Exception as fallback_error:
                print(f"❌ [/start] Critical error: Even fallback text message failed: {fallback_error}", flush=True)
                # Last resort: send minimal error message
                try:
                    await update.message.reply_text("欢迎使用本机器人！")
                except Exception as final_error:
                    print(f"❌ [/start] Fatal error: Cannot send any message to user {user_id}: {final_error}", flush=True)


async def on_my_chat_member(update: Update, context):
    """处理机器人被添加到群组事件，仅注册群组信息，不发送通知消息"""
    try:
        chat = update.effective_chat
        new_member = update.my_chat_member.new_chat_member
        old_member = update.my_chat_member.old_chat_member
        status = new_member.status
        old_status = old_member.status if old_member else None
        user = update.effective_user
        
        print(f"📍 on_my_chat_member: chat={chat.title}, type={chat.type}, new_status={status}, old_status={old_status}")
        
        if chat.type in ['group', 'supergroup', 'channel']:
            is_channel = chat.type == 'channel'
            label = '频道' if is_channel else '群组'
            # 处理机器人被添加到群组/频道 (从 left/kicked 变为 member/administrator)
            if status in ['administrator', 'member'] and old_status in ['left', 'kicked', None]:
                # 使用全局 App Context
                with global_flask_app.app_context():
                    g = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                    if not g:
                        # 新群组/频道，创建记录
                        bot_clone_id = context.application.bot_data.get('clone_id')
                        g = BotGroup(chat_id=str(chat.id), title=chat.title, type=chat.type, is_active=True, clone_id=bot_clone_id)
                        g.fields_config = json.dumps(DEFAULT_FIELDS, ensure_ascii=False)
                        db.session.add(g)
                        db.session.commit()
                        print(f"➕ 新{label}注册: {chat.title} (chat_id: {chat.id})")
                    else:
                        # 已存在的群组/频道，确保激活并更新标题
                        g.is_active = True
                        g.title = chat.title
                        g.type = chat.type
                        db.session.commit()
                        print(f"🔄 {label}已重新激活: {chat.title} (chat_id: {chat.id})")
                    
                print(f"✅ 机器人已添加到{label} {chat.title}，{label}已注册")
            
            # 处理机器人被移出群组/频道 (从 member/administrator 变为 left/kicked)
            elif status in ['left', 'kicked'] and old_status in ['administrator', 'member']:
                with global_flask_app.app_context():
                    g = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                    if g:
                        g.is_active = False
                        db.session.commit()
                        print(f"⛔️ 机器人已被移出{label} {chat.title}，{label}已停用")
    except Exception as e:
        import traceback
        print(f"Error in on_my_chat_member: {e}")
        traceback.print_exc()
        try:
            db.session.rollback()
        except Exception:
            pass

# NOTE: cmd_clones lives in app.bot.handlers.commands and is rebound
# at the bottom of this module for run_bot / setup_clone_handlers.

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


async def on_non_text_message(update: Update, context):
    """Handle non-text messages (stickers, photos, videos, documents, audio, voice, animations).
    Applies spam-protection, keyword filter (captions), forced subscription, then sync."""
    if not global_flask_app:
        return
    try:
        chat = update.effective_chat
        if not chat or chat.type not in ['group', 'supergroup']:
            return
        # Re-use check_spam_protection which already handles sticker/forward blocking
        spam_detected = await check_spam_protection(update, context)
        if spam_detected:
            return
        # Caption keyword filter (text path already covers msg.text in on_message)
        keyword_filtered = await check_keyword_filter(update, context)
        if keyword_filtered:
            return
        # Media must not bypass forced channel subscription
        subscription_blocked = await check_forced_subscription(update, context)
        if subscription_blocked:
            return
        # Cross-group media sync (text path already calls this from on_message)
        await handle_sync_group_messages(update, context)
    except Exception as e:
        print(f"Error in on_non_text_message: {e}")


async def on_message(update: Update, context):
    if not global_flask_app: return
    try:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if not msg.text or not chat: return

        # Channel posts are handled by handle_sync_channel_posts (CHANNEL_POSTS filter)
        if chat.type == 'channel':
            return

        txt = msg.text.strip()

        # 🆕 Check pending entry verification answer
        if chat.type in ['group', 'supergroup'] and user:
            pending_key = f"pending_verification:{chat.id}:{user.id}"
            verify_state = context.application.bot_data.get(pending_key)
            if verify_state is not None:
                correct_answer = verify_state.get('answer', '')
                if txt.strip().lower() == correct_answer:
                    # Correct answer — restore permissions and remove pending state
                    context.application.bot_data.pop(pending_key, None)
                    # Cancel timeout kick job
                    current_jobs = context.application.job_queue.get_jobs_by_name(pending_key)
                    for job in current_jobs:
                        job.schedule_removal()
                    try:
                        await context.bot.restrict_chat_member(
                            chat_id=chat.id,
                            user_id=user.id,
                            permissions=get_unrestricted_permissions()
                        )
                        await context.bot.send_message(
                            chat_id=chat.id,
                            text=f"✅ <a href='tg://user?id={user.id}'>{user.first_name}</a> 验证成功，欢迎加入！",
                            parse_mode='HTML'
                        )
                    except Exception as restore_err:
                        print(f"Error restoring verified member permissions: {restore_err}")
                else:
                    # Wrong answer — delete message, do not unblock
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                return

        # 🆕 Check spam protection first (may delete message and return early)
        if chat.type in ['group', 'supergroup']:
            spam_detected = await check_spam_protection(update, context)
            if spam_detected:
                return  # Message was deleted, stop processing
            
            # 🆕 Check keyword filter (may delete message and return early)
            keyword_filtered = await check_keyword_filter(update, context)
            if keyword_filtered:
                return  # Message was deleted, stop processing
            
            # 🆕 Check forced subscription (may delete message and return early)
            subscription_blocked = await check_forced_subscription(update, context)
            if subscription_blocked:
                return  # Message was deleted, stop processing
            
            # 🆕 Check and mute expired users immediately
            await check_and_mute_expired_user(update, context)
            
            # 🆕 Update user last activity time
            await update_user_activity(update, context)
            
            # 🆕 Track message statistics
            await track_message_statistics(update, context)
            
            # 🆕 Track user name changes
            await track_user_name_change(update, context)
            
            # 🆕 Record/update member info
            await update_group_member(update, context)
            
            # 🆕 Sync messages to other groups
            await handle_sync_group_messages(update, context)
        
        # Check if this is a verification code from admin in private chat
        if chat.type == 'private':
            # 🆕 Handle forwarded messages - show detailed user info
            # Use getattr to safely check for forward_from attribute (may not exist in non-forwarded messages)
            try:
                forwarded_user = getattr(msg, 'forward_from', None)
                if forwarded_user:
                    info_lines = ["🔍 转发消息详细信息\n"]
                    info_lines.append("━━━━━━━━━━━━━━━━")
                    info_lines.append(f"📛 用户名: {forwarded_user.first_name or '-'}")
                    if forwarded_user.last_name:
                        info_lines.append(f"   姓氏: {forwarded_user.last_name}")
                    if forwarded_user.username:
                        info_lines.append(f"🔗 Username: @{forwarded_user.username}")
                    info_lines.append(f"🆔 用户ID: <code>{forwarded_user.id}</code>")
                    info_lines.append(f"🤖 机器人: {'是' if forwarded_user.is_bot else '否'}")
                    
                    # Try to get user's group membership info
                    def _get_forwarded_user_info():
                        with global_flask_app.app_context():
                            # Find all groups where this user is a member
                            group_users = GroupUser.query.filter_by(tg_id=forwarded_user.id).all()
                            results = []
                            for gu in group_users:
                                group = BotGroup.query.get(gu.group_id)
                                if group:
                                    user_points = UserPoints.query.filter_by(
                                        group_id=group.id,
                                        user_id=forwarded_user.id
                                    ).first()
                                    
                                    results.append({
                                        'group_name': group.title,
                                        'banned': gu.is_banned,
                                        'expiration': gu.expiration_date,
                                        'points': user_points.points_balance if user_points else 0
                                    })
                            return results
                    
                    user_groups = await asyncio.get_running_loop().run_in_executor(None, _get_forwarded_user_info)
                    
                    if user_groups:
                        info_lines.append(f"\n📊 群组信息 ({len(user_groups)}个群)")
                        info_lines.append("━━━━━━━━━━━━━━━━")
                        for idx, info in enumerate(user_groups[:5], 1):  # Show max 5 groups
                            info_lines.append(f"\n{idx}. {info['group_name']}")
                            info_lines.append(f"   状态: {'🚫 已封禁' if info['banned'] else '✅ 正常'}")
                            if info['expiration']:
                                info_lines.append(f"   到期: {info['expiration'].strftime('%Y-%m-%d %H:%M:%S')}")
                            info_lines.append(f"   积分: {info['points']}")
                        
                        if len(user_groups) > 5:
                            info_lines.append(f"\n... 及其他 {len(user_groups) - 5} 个群组")
                    
                    await msg.reply_text("\n".join(info_lines), parse_mode='HTML')
                    return
            except Exception as e:
                print(f"Error handling forwarded message: {e}")
                # Continue to verification code check even if forward message handling fails
            
            admin_id = safe_int(os.getenv('ADMIN_ID', 0))
            # Also allow clone bot admins to submit verification codes
            clone_id = context.application.bot_data.get('clone_id')
            is_clone_admin = False
            if clone_id and user.id != admin_id and txt.isdigit() and len(txt) == 6:
                is_clone_admin = await asyncio.get_running_loop().run_in_executor(
                    None, _is_clone_admin, clone_id, user.id
                )

            if (user.id == admin_id or is_clone_admin) and txt.isdigit() and len(txt) == 6:
                # Try to verify the code using constant-time comparison
                def _verify_code():
                    with global_flask_app.app_context():
                        # Get all pending sessions for this user
                        pending_sessions = AuthSession.query.filter_by(
                            user_id=user.id,
                            is_verified=False
                        ).all()
                        
                        # Use constant-time comparison to prevent timing attacks
                        for auth_session in pending_sessions:
                            if hmac.compare_digest(auth_session.verification_code, txt):
                                # Check if not expired
                                if get_beijing_now() <= auth_session.expires_at:
                                    auth_session.is_verified = True
                                    db.session.commit()
                                    return True
                                else:
                                    continue
                        return None
                
                result = await asyncio.get_running_loop().run_in_executor(None, _verify_code)
                
                if result is True:
                    _sched_del(context, await msg.reply_html("✅ <b>验证成功！</b>\n\n网页将自动跳转到管理后台。"), 30)
                    return
                elif result is False:
                    _sched_del(context, await msg.reply_html("⚠️ <b>验证码已过期</b>\n\n请重新发送 /start 获取新的验证码。"), 30)
                    return
                else:
                    # result is None - code didn't match any pending session
                    _sched_del(context, await msg.reply_html("❌ <b>验证码无效</b>\n\n请检查验证码是否正确，或重新发送 /start 获取新的验证码。"), 30)
                    return

        # 使用全局 App Context
        with global_flask_app.app_context():
            # 1. 自动点赞
            group = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
            if not group:
                if chat.type in ['group', 'supergroup']:
                    # Auto-register: bot is in this group (receiving messages), register it
                    bot_clone_id = context.application.bot_data.get('clone_id')
                    group = BotGroup(chat_id=str(chat.id), title=chat.title, type=chat.type, is_active=True, clone_id=bot_clone_id)
                    group.fields_config = json.dumps(DEFAULT_FIELDS, ensure_ascii=False)
                    db.session.add(group)
                    db.session.commit()
                    logging.info(f"➕ [on_message] 自动注册群组: {chat.title} (chat_id: {chat.id})")
                else:
                    return
            elif not group.is_active:
                if chat.type in ['group', 'supergroup']:
                    # Re-activate: bot is still in the group (receiving messages)
                    group.is_active = True
                    group.title = chat.title
                    db.session.commit()
                    logging.info(f"🔄 [on_message] 重新激活群组: {chat.title} (chat_id: {chat.id})")
                else:
                    return
            
            conf = get_group_conf(group)
            
            # Award points for messages (if rule exists) - applies to all users
            # Skip if this message is a checkin or signin command to avoid double-counting
            _checkin_cmds = [c.strip() for c in conf.get('checkin_cmd', '打卡').split(',')]
            _is_checkin_msg = conf.get('checkin_open') and txt in _checkin_cmds
            _signin_cmds = [c.strip() for c in conf.get('signin_cmd', '签到').split(',')]
            _is_signin_msg = conf.get('signin_open') and txt in _signin_cmds
            _points_plugin_on = is_plugin_enabled(group.id, 'points')
            if chat.type in ['group', 'supergroup'] and _points_plugin_on and not _is_checkin_msg and not _is_signin_msg:
                message_rule = PointsRule.query.filter_by(
                    group_id=group.id,
                    rule_type='message',
                    is_active=True
                ).first()
                
                if message_rule:
                    user_points = UserPoints.query.filter_by(
                        group_id=group.id,
                        user_id=user.id
                    ).first()
                    
                    if not user_points:
                        user_points = UserPoints(
                            group_id=group.id,
                            user_id=user.id,
                            points_balance=0
                        )
                        db.session.add(user_points)
                    
                    # Enforce daily cap: sum points earned today via message rule.
                    # The DB query is only performed when a cap is configured.
                    earned_today = 0
                    if message_rule.max_daily_points:
                        today_start = get_beijing_today()
                        earned_today = db.session.query(
                            func.coalesce(func.sum(PointsLog.points_change), 0)
                        ).filter(
                            PointsLog.group_id == group.id,
                            PointsLog.user_id == user.id,
                            PointsLog.reason == _REASON_MESSAGE,
                            PointsLog.created_at >= today_start
                        ).scalar() or 0

                    actual_award = clamp_award_to_daily_cap(
                        message_rule.points_amount, earned_today, message_rule.max_daily_points
                    )

                    if actual_award > 0:
                        user_points.points_balance += actual_award
                    
                        # Log the points transaction
                        points_log = PointsLog(
                            group_id=group.id,
                            user_id=user.id,
                            points_change=actual_award,
                            reason=_REASON_MESSAGE,
                            balance_after=user_points.points_balance
                        )
                        db.session.add(points_log)
                        db.session.commit()

            # Award points for forwarded messages (share rule)
            # A forwarded message has a forward_origin attribute set by PTB
            _is_forward = hasattr(msg, 'forward_origin') and msg.forward_origin is not None
            if _points_plugin_on and _is_forward and chat.type in ['group', 'supergroup']:
                share_rule = PointsRule.query.filter_by(
                    group_id=group.id,
                    rule_type='share',
                    is_active=True
                ).first()

                if share_rule:
                    share_user_points = UserPoints.query.filter_by(
                        group_id=group.id,
                        user_id=user.id
                    ).first()

                    if not share_user_points:
                        share_user_points = UserPoints(
                            group_id=group.id,
                            user_id=user.id,
                            points_balance=0
                        )
                        db.session.add(share_user_points)

                    share_earned_today = 0
                    if share_rule.max_daily_points:
                        today_start = get_beijing_today()
                        share_earned_today = db.session.query(
                            func.coalesce(func.sum(PointsLog.points_change), 0)
                        ).filter(
                            PointsLog.group_id == group.id,
                            PointsLog.user_id == user.id,
                            PointsLog.reason == _REASON_SHARE,
                            PointsLog.created_at >= today_start
                        ).scalar() or 0

                    share_award = clamp_award_to_daily_cap(
                        share_rule.points_amount, share_earned_today, share_rule.max_daily_points
                    )

                    if share_award > 0:
                        share_user_points.points_balance += share_award
                        db.session.add(PointsLog(
                            group_id=group.id,
                            user_id=user.id,
                            points_change=share_award,
                            reason=_REASON_SHARE,
                            balance_after=share_user_points.points_balance
                        ))
                        db.session.commit()

            # 🆕 Track messages for active lotteries (optimized for large groups)
            # ✅ Track ALL group members, not just verified users (unverified users can participate)
            if chat.type in ['group', 'supergroup'] and is_plugin_enabled(group.id, 'lottery'):
                # Track for both message_count and message_rank lotteries
                # Support both pending and active status as long as within time window
                # Limit to 10 concurrent lotteries to prevent performance issues in large groups
                active_lotteries = GroupLottery.query.filter_by(
                    group_id=group.id
                ).filter(
                    GroupLottery.status.in_(['pending', 'active']),
                    GroupLottery.lottery_type.in_(['message_count', 'message_rank'])
                ).limit(10).all()
                
                # Track for ALL users (verified and unverified)
                if active_lotteries:
                    logging.debug(f"🎲 [消息计数] 用户 {user.id} 在群组 {group.id} 发送消息，追踪 {len(active_lotteries)} 个活动抽奖")
                    now = get_beijing_now()
                    qualifications_to_notify = []  # lotteries where user just reached min_messages
                    for lottery in active_lotteries:
                        # Only track if lottery is still within its time window
                        if lottery.start_time and lottery.end_time:
                            if lottery.start_time <= now <= lottery.end_time:
                                # Get or create message count record
                                msg_count = LotteryMessageCount.query.filter_by(
                                    lottery_id=lottery.id,
                                    user_id=user.id
                                ).first()
                                
                                if not msg_count:
                                    msg_count = LotteryMessageCount(
                                        lottery_id=lottery.id,
                                        group_id=group.id,
                                        user_id=user.id,
                                        message_count=0
                                    )
                                    db.session.add(msg_count)
                                    logging.debug(f"🎲 [消息计数] 为用户 {user.id} 创建抽奖 '{lottery.lottery_name}' (ID: {lottery.id}) 的消息计数记录")
                                
                                msg_count.message_count += 1
                                msg_count.updated_at = now
                                logging.debug(f"🎲 [消息计数] 用户 {user.id} 抽奖 '{lottery.lottery_name}' 消息计数: {msg_count.message_count}")

                                # Check if user just reached the minimum messages threshold
                                if (lottery.lottery_type == 'message_count' and
                                        lottery.min_messages and
                                        msg_count.message_count == lottery.min_messages):
                                    qualifications_to_notify.append(lottery)
                    
                    # Batch commit all lottery tracking updates
                    try:
                        db.session.commit()
                    except Exception as e:
                        logging.error(f"❌ [消息计数] 提交抽奖追踪数据失败: {e}")
                        db.session.rollback()
                        qualifications_to_notify = []

                    # Send qualification notifications after successful commit
                    for q_lottery in qualifications_to_notify:
                        try:
                            qualified_count = LotteryMessageCount.query.filter_by(
                                lottery_id=q_lottery.id
                            ).filter(
                                LotteryMessageCount.message_count >= q_lottery.min_messages
                            ).count()
                            lines = [f"✅ {user.mention_html()} 已成功参与抽奖「{q_lottery.lottery_name}」！"]
                            lines.append(f"📊 已有 <b>{qualified_count}</b> 人满足参与条件（发言 ≥ {q_lottery.min_messages} 条）")
                            if q_lottery.prize_description:
                                lines.append(f"🎁 奖品：{q_lottery.prize_description}")
                            if q_lottery.end_time:
                                lines.append(f"📅 活动结束时间：{q_lottery.end_time.strftime('%Y-%m-%d %H:%M:%S')}")
                            lines.append("继续发言可提高中奖概率！")
                            await context.bot.send_message(
                                chat_id=chat.id,
                                text="\n".join(lines),
                                parse_mode='HTML'
                            )
                            logging.info(f"🎲 [消息计数] 已通知群组 {chat.id}：用户 {user.id} 参与抽奖 '{q_lottery.lottery_name}' 成功，共 {qualified_count} 人达标")
                        except Exception as notify_err:
                            logging.error(f"❌ [消息计数] 发送参与成功通知失败 (抽奖 '{q_lottery.lottery_name}'): {notify_err}")
            
            # Authenticated-user perks: auto-like + Telegram nickname member tag
            if conf.get('auto_like') or conf.get('auto_member_tag', True):
                db_user = GroupUser.query.filter_by(group_id=group.id, tg_id=user.id).first()
                if db_user:
                    # Resolve the correct bot token: use clone bot token if this is a clone bot
                    _perk_token = None
                    _clone_id = context.application.bot_data.get('clone_id')
                    if _clone_id:
                        _clone = BotClone.query.get(_clone_id)
                        if _clone:
                            _perk_token = _clone.get_bot_token()
                    if conf.get('auto_like'):
                        emoji = normalize_like_emoji(conf.get('like_emoji', DEFAULT_LIKE_EMOJI))
                        # 在线程中执行阻塞请求，避免卡顿
                        asyncio.get_running_loop().run_in_executor(
                            None, do_like, chat.id, msg.message_id, emoji, _perk_token
                        )
                    if conf.get('auto_member_tag', True):
                        # Apply nickname tag once (skipped when already matching applied_telegram_tag)
                        asyncio.get_running_loop().run_in_executor(
                            None,
                            apply_telegram_member_tag_by_ids,
                            group.id,
                            db_user.id,
                            conf,
                            _perk_token,
                            False,
                        )
            
            # 2. 打卡
            if _points_plugin_on and _is_checkin_msg:
                db_user = GroupUser.query.filter_by(group_id=group.id, tg_id=user.id).first()
                if not db_user:
                    msg_text = sanitize_html_for_telegram(conf.get('msg_not_registered', '未认证'))
                    await msg.reply_html(msg_text)
                else:
                     # Check if user is expired and should be banned
                    if db_user.expiration_date and get_beijing_now() > db_user.expiration_date:
                        # Always try to mute, even if already marked as banned (in case API failed before)
                        try:
                            print(f"🔄 [打卡功能] 准备禁言过期用户 {user.id} in group {chat.id}", flush=True)
                            # Mute with comprehensive restrictions
                            await context.bot.restrict_chat_member(
                                chat_id=chat.id,
                                user_id=user.id,
                                permissions=get_muted_permissions()
                            )
                            print(f"✅ [打卡功能] Successfully called restrict_chat_member API - Muted expired user {user.id} in group {chat.id}", flush=True)
                            
                            # ✅ NEW: Only mark as banned in DB after successful API call
                            if not db_user.is_banned:
                                db_user.is_banned = True
                                db.session.commit()
                                print(f"✅ [打卡功能] Marked user {user.id} as banned in database after successful API call", flush=True)
                                
                        except Exception as restrict_error:
                            print(f"❌ [打卡功能] restrict_chat_member API failed for user {user.id} in group {chat.id}", flush=True)
                            print(f"   Error type: {type(restrict_error).__name__}", flush=True)
                            print(f"   Error details: {str(restrict_error)}", flush=True)
                            print(f"   Traceback: {traceback.format_exc()}", flush=True)
                            print(f"   ⚠️  Database NOT updated - user will be retried on next check", flush=True)
                        
                        # 🆕 Send ephemeral message in group (visible only to that user)
                        msg_text = sanitize_html_for_telegram(conf.get('msg_expired_ban', '⛔️ 您的认证已过期，已被暂时禁言。请联系管理员续费。'))
                        try:
                            # Send a reply that will be auto-deleted
                            warning_msg = await msg.reply_html(msg_text)
                            # Auto-delete after 30 seconds
                            context.job_queue.run_once(_auto_delete_msg, 30, data=warning_msg)
                        except Exception as e:
                            print(f"Failed to send expiration notification: {e}")
                        return  # Stop processing, don't allow check-in
                    else:
                        # Check if user has already checked in today
                        today = get_beijing_today()
                        if db_user.checkin_time and db_user.checkin_time >= today:
                            # User already checked in today, send repeat check-in message
                            msg_text = sanitize_html_for_telegram(conf.get('msg_repeat_checkin', '🔄 <b>今天已打卡</b>'))
                            r = await msg.reply_html(msg_text)
                            del_time = safe_int(conf.get('checkin_del_time'), 0)
                            if del_time > 0:
                                context.job_queue.run_once(_auto_delete_msg, del_time, data=r)
                        else:
                            # First check-in today, proceed normally (attendance only, no points)
                            db_user.checkin_time = get_beijing_now()
                            db_user.online = True
                            db.session.commit()
                            
                            msg_text = sanitize_html_for_telegram(conf.get('msg_checkin_success', '打卡成功'))
                            r = await msg.reply_html(msg_text)
                            del_time = safe_int(conf.get('checkin_del_time'), 0)
                            if del_time > 0:
                                context.job_queue.run_once(_auto_delete_msg, del_time, data=r)
                return

            # 2.5 积分签到（独立于认证用户打卡）
            if _points_plugin_on and _is_signin_msg and chat.type in ['group', 'supergroup']:
                signin_rule = PointsRule.query.filter_by(
                    group_id=group.id,
                    rule_type='checkin',
                    is_active=True
                ).first()

                if not signin_rule:
                    return  # No points rule configured, silently ignore

                today = get_beijing_today()

                # Check if already signed in today via PointsLog
                already_signed = PointsLog.query.filter_by(
                    group_id=group.id,
                    user_id=user.id,
                    reason='每日签到'
                ).filter(PointsLog.created_at >= today).first()

                # Fetch (or create) UserPoints record
                user_points = UserPoints.query.filter_by(
                    group_id=group.id,
                    user_id=user.id
                ).first()
                cur_balance = user_points.points_balance if user_points else 0

                def _substitute_signin_tpl(tpl, earned=0, balance=0):
                    tpl = tpl.replace('{tg_id}', str(user.id))
                    tpl = tpl.replace('{积分}', str(earned))
                    tpl = tpl.replace('{余额}', str(balance))
                    return tpl

                if already_signed:
                    raw_tpl = conf.get('msg_repeat_signin', '🔄 <b>今日已签到，当前余额：{余额}</b>')
                    msg_text = sanitize_html_for_telegram(_substitute_signin_tpl(raw_tpl, earned=0, balance=cur_balance))
                else:
                    if not user_points:
                        user_points = UserPoints(
                            group_id=group.id,
                            user_id=user.id,
                            points_balance=0
                        )
                        db.session.add(user_points)

                    earned_points = signin_rule.points_amount
                    user_points.points_balance += earned_points
                    cur_balance = user_points.points_balance

                    points_log = PointsLog(
                        group_id=group.id,
                        user_id=user.id,
                        points_change=earned_points,
                        reason='每日签到',
                        balance_after=cur_balance
                    )
                    db.session.add(points_log)
                    db.session.commit()

                    raw_tpl = conf.get('msg_signin_success', '🎉 <b>签到成功！获得 {积分} 积分，当前余额：{余额}</b>')
                    msg_text = sanitize_html_for_telegram(_substitute_signin_tpl(raw_tpl, earned=earned_points, balance=cur_balance))

                r = await msg.reply_html(msg_text)
                del_time = safe_int(conf.get('signin_del_time'), 0)
                if del_time > 0:
                    context.job_queue.run_once(_auto_delete_msg, del_time, data=r)
                return

            # 2.6 积分兑换命令处理
            if chat.type in ['group', 'supergroup']:
                _exchange_cmd = conf.get('exchange_command_word', '兑换').strip()
                _exchange_list_cmds = {_exchange_cmd, '兑换', '兑换列表', '积分兑换'}
                _is_exchange_list = txt in _exchange_list_cmds
                _is_exchange_item = txt.startswith('兑换 ') and len(txt) > 3
                if (_is_exchange_list or _is_exchange_item) and not conf.get('points_shop_enabled', True):
                    await msg.reply_html("❌ 积分商城已关闭")
                    return
                if conf.get('points_shop_enabled', True) and _is_exchange_list:
                    # Show available exchange items with inline buttons (reuse catalog builder)
                    catalog_text, catalog_buttons = _build_exchange_catalog_sync(group.id)
                    if catalog_buttons:
                        await msg.reply_html(
                            catalog_text,
                            reply_markup=InlineKeyboardMarkup(catalog_buttons)
                        )
                    else:
                        await msg.reply_html(catalog_text)
                    return

                if conf.get('points_shop_enabled', True) and _is_exchange_item:
                    item_id_str = txt[3:].strip()
                    if item_id_str.isdigit():
                        item_id = int(item_id_str)
                        item = PointsExchangeItem.query.filter_by(id=item_id, group_id=group.id, is_active=True).first()
                        if not item:
                            await msg.reply_html("❌ 商品不存在或已下架。")
                            return
                        # Check stock
                        if item.stock is not None and item.stock <= 0:
                            await msg.reply_html("❌ 该商品库存已用完。")
                            return
                        # Check user points
                        user_points = UserPoints.query.filter_by(group_id=group.id, user_id=user.id).first()
                        balance = user_points.points_balance if user_points else 0
                        if balance < item.points_cost:
                            await msg.reply_html(
                                f"❌ 积分不足。兑换 <b>{item.item_name}</b> 需要 <b>{item.points_cost}</b> 积分，"
                                f"您当前积分为 <b>{balance}</b>。"
                            )
                            return
                        # Deduct points
                        if not user_points:
                            user_points = UserPoints(group_id=group.id, user_id=user.id, points_balance=0)
                            db.session.add(user_points)
                        user_points.points_balance -= item.points_cost
                        # Reduce stock and auto-deactivate when sold out
                        item_deactivated = False
                        if item.stock is not None:
                            item.stock -= 1
                            if item.stock <= 0:
                                item.is_active = False
                                item_deactivated = True
                        # Snapshot before commit
                        item_announcement_msg_id = item.announcement_msg_id
                        # Record
                        record = PointsExchangeRecord(
                            group_id=group.id,
                            user_id=user.id,
                            item_id=item.id,
                            points_spent=item.points_cost
                        )
                        db.session.add(record)
                        # Log
                        points_log = PointsLog(
                            group_id=group.id,
                            user_id=user.id,
                            points_change=-item.points_cost,
                            reason=f"兑换商品: {item.item_name}",
                            balance_after=user_points.points_balance
                        )
                        db.session.add(points_log)
                        db.session.commit()
                        # Build redeemer display string
                        redeemer_display = _format_tg_user_display(update.effective_user)
                        # Notify user
                        reply_lines = [
                            f"✅ <b>兑换成功！</b>",
                            f"👤 兑换人：{redeemer_display}",
                            f"🎁 商品：<b>{item.item_name}</b>",
                            f"💰 消耗积分：<b>{item.points_cost}</b>  |  剩余积分：<b>{user_points.points_balance}</b>",
                        ]
                        if item.redemption_info:
                            reply_lines.append(f"\n📋 兑换信息：\n{item.redemption_info}")
                        await msg.reply_html("\n".join(reply_lines))
                        # 如商品售罄，删除单条公告并停用
                        if item_deactivated:
                            _exch_channel = conf.get('exchange_channel_id', '').strip()
                            await _delete_item_announcement_async(
                                _exch_channel, item_announcement_msg_id, context.application
                            )
                        # 兑换成功后自动更新频道商品目录
                        await _update_exchange_catalog_async(group.id, context.application)
                        return

            # 2.65 积分竞拍命令词处理
            if chat.type in ['group', 'supergroup']:
                _auction_cmd = conf.get('auction_command_word', '竞拍').strip()
                if _auction_cmd and txt == _auction_cmd:
                    if not conf.get('points_shop_enabled', True):
                        await msg.reply_html("❌ 积分商城已关闭")
                        return
                    auctions = PointsAuction.query.filter_by(
                        group_id=group.id, status='active'
                    ).order_by(PointsAuction.auction_end).all()
                    if not auctions:
                        await msg.reply_html("📭 当前没有进行中的竞拍")
                    else:
                        lines = ["🏆 <b>当前竞拍列表</b>\n", "━━━━━━━━━━━━━━━━"]
                        for a in auctions:
                            lines.append(f"\n📦 ID: {a.id}")
                            lines.append(f"   物品: {a.item_name}")
                            if a.item_description:
                                lines.append(f"   描述: {a.item_description}")
                            lines.append(f"   起拍价: {a.starting_price} 积分")
                            lines.append(f"   当前价: {a.current_bid} 积分")
                            if a.current_bidder_id:
                                lines.append(f"   领先者: 用户 {a.current_bidder_id}")
                            if a.auction_end:
                                lines.append(f"   结束时间: {a.auction_end.strftime('%Y-%m-%d %H:%M:%S')}")
                            lines.append(f"\n   💡 出价: /bid {a.id} &lt;金额&gt;")
                        await msg.reply_html("\n".join(lines))
                    return


            if chat.type in ['group', 'supergroup'] and user:
                invitation_activity = InvitationActivity.query.filter_by(
                    group_id=group.id, enabled=True
                ).first()
                if invitation_activity and invitation_activity.link_keyword:
                    inv_keywords = [k.strip() for k in invitation_activity.link_keyword.split(',') if k.strip()]
                    if txt in inv_keywords:
                        bot_me = await context.bot.get_me()
                        invite_link = f"https://t.me/{bot_me.username}?start=inv_{user.id}_{group.id}"
                        inv_msg = (
                            f"🔗 <b>您的专属邀请链接</b>\n\n"
                            f"<code>{invite_link}</code>\n\n"
                            f"📌 将此链接分享给好友，当好友通过链接加入群组后，"
                            f"您将获得 <b>{invitation_activity.reward_points} 积分</b> 奖励！"
                        )
                        await msg.reply_html(inv_msg)
                        return

            # 3. 自动回复检查 (Check points-based first, then regular auto-reply)
            if conf.get('auto_reply_open', True):
                # 3.1 先检查积分自动回复 (Check Points-based Auto-Reply first)
                # 🆕 Support multiple keywords (comma-separated)
                points_reply = None
                all_points_replies = PointsAutoReply.query.filter_by(
                    group_id=group.id,
                    is_active=True
                ).all()
                
                for pr in all_points_replies:
                    # Filter out empty keywords after stripping
                    keywords = [k.strip() for k in pr.trigger_keyword.split(',') if k.strip()]
                    if txt in keywords:
                        points_reply = pr
                        break
                
                if points_reply and points_reply.points_cost > 0:
                    # Points-based content found - handle it
                    try:
                        # Check user points
                        user_points = UserPoints.query.filter_by(
                            group_id=group.id,
                            user_id=user.id
                        ).first()
                        
                        if not user_points or user_points.points_balance < points_reply.points_cost:
                            current_balance = user_points.points_balance if user_points else 0
                            await msg.reply_text(
                                f"❌ 积分不足！\n"
                                f"需要: {points_reply.points_cost} 积分\n"
                                f"当前: {current_balance} 积分"
                            )
                        else:
                            try:
                                # Deduct points and log in a transaction
                                user_points.points_balance -= points_reply.points_cost
                                
                                # Log the transaction
                                points_log = PointsLog(
                                    group_id=group.id,
                                    user_id=user.id,
                                    points_change=-points_reply.points_cost,
                                    reason=f"查看内容: {txt}",
                                    balance_after=user_points.points_balance
                                )
                                db.session.add(points_log)
                                db.session.commit()
                                
                                # Send the content only after successful commit
                                content = sanitize_html_for_telegram(points_reply.content or '')
                                media_urls = points_reply.get_media_url_list()
                                
                                if points_reply.media_type in ('image', 'video', 'media') and media_urls:
                                    url_with_types = points_reply.get_media_url_with_types()
                                    await send_multi_media(
                                        context.bot, chat.id, points_reply.media_type, media_urls,
                                        content=content,
                                        media_url_types=[t for _, t in url_with_types] if url_with_types else None
                                    )
                                elif content:
                                    await msg.reply_html(
                                        content,
                                        link_preview_options=LinkPreviewOptions(is_disabled=True)
                                    )
                                
                                # Notify about deduction
                                await msg.reply_text(
                                    f"💎 已扣除 {points_reply.points_cost} 积分\n"
                                    f"剩余: {user_points.points_balance} 积分"
                                )
                            except Exception as e:
                                # Rollback on any database error
                                db.session.rollback()
                                print(f"Points deduction error: {e}")
                                await msg.reply_text("❌ 积分扣除失败，请重试")
                    except Exception as e:
                        print(f"Points-based auto reply error: {e}")
                    # Don't check regular auto-reply if points-based was triggered
                else:
                    # 3.2 No points-based reply found, check regular auto-reply
                    # 🆕 Support multiple keywords (comma-separated)
                    auto_reply = None
                    all_auto_replies = AutoReply.query.filter_by(
                        group_id=group.id,
                        is_active=True
                    ).all()
                    
                    for ar in all_auto_replies:
                        # Filter out empty keywords after stripping
                        keywords = [k.strip() for k in ar.trigger_keyword.split(',') if k.strip()]
                        if txt in keywords:
                            auto_reply = ar
                            break
                    
                    if auto_reply:
                        try:
                            # 构建按钮
                            buttons = []
                            try:
                                links = json.loads(auto_reply.links or '[]')
                                buttons = build_inline_keyboard_from_links(links)
                            except:
                                pass
                            
                            # 🆕 Add bottom buttons from group settings
                            bottom_buttons_markup = await display_bottom_buttons(chat.id, context)
                            if bottom_buttons_markup:
                                # Merge bottom buttons with auto-reply buttons
                                buttons.extend(bottom_buttons_markup.inline_keyboard)
                            
                            reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
                            
                            # 发送回复
                            sent_reply = None
                            content = sanitize_html_for_telegram(auto_reply.content or '')
                            media_urls = auto_reply.get_media_url_list()
                            
                            if auto_reply.media_type in ('image', 'video', 'media') and media_urls:
                                url_with_types = auto_reply.get_media_url_with_types()
                                sent_reply = await send_multi_media(
                                    context.bot, chat.id, auto_reply.media_type, media_urls,
                                    content=content, reply_markup=reply_markup,
                                    media_url_types=[t for _, t in url_with_types] if url_with_types else None
                                )
                            elif content:
                                sent_reply = await msg.reply_html(
                                    content,
                                    reply_markup=reply_markup,
                                    link_preview_options=LinkPreviewOptions(is_disabled=True)
                                )
                            
                            # 自动删除回复
                            if sent_reply and auto_reply.delete_after > 0:
                                context.job_queue.run_once(_auto_delete_msg, auto_reply.delete_after, data=sent_reply)
                        except Exception as e:
                            print(f"Auto reply error: {e}")
                # Continue to check for query functionality
            
            # 3.3 检查群底按钮触发关键词
            try:
                # 查找有触发关键词的按钮
                buttons_with_keywords = GroupBottomButton.query.filter_by(
                    group_id=group.id,
                    is_active=True
                ).filter(GroupBottomButton.trigger_keyword.isnot(None)).all()
                
                for btn in buttons_with_keywords:
                    # Filter out empty keywords after stripping
                    keywords = [k.strip() for k in btn.trigger_keyword.split(',') if k.strip()]
                    if txt in keywords:
                        # Build inline keyboard with this button (only if it has URL or callback)
                        keyboard = []
                        if btn.button_url:
                            keyboard.append([InlineKeyboardButton(
                                btn.button_text,
                                url=btn.button_url
                            )])
                        elif btn.button_callback:
                            keyboard.append([InlineKeyboardButton(
                                btn.button_text,
                                callback_data=btn.button_callback
                            )])
                        
                        # Only send if button has valid URL or callback
                        if keyboard:
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            await msg.reply_text(
                                "📋 群组按钮：",
                                reply_markup=reply_markup
                            )
                            break  # Only show first matched button
            except Exception as e:
                print(f"Button keyword trigger error: {e}")
            
            # 4. 查询功能
            query_cmds = [c.strip() for c in conf.get('query_cmd', '查询').split(',')]
            is_search = False
            kw = None
            
            if conf.get('query_open') and txt in query_cmds:
                is_search = True
            elif conf.get('query_filter_open'):
                for cmd in query_cmds:
                    if txt.startswith(cmd + " "):
                        kw = txt[len(cmd):].strip()
                        is_search = True
                        break
                # Treat as placeholder query keyword (short text that's not a command)
                # BUT exclude pure digit messages to avoid interfering with normal chat
                if not is_search and 0 < len(txt) < 15 and not txt.startswith('/') and not txt.isdigit():
                    kw = txt
                    is_search = True
            
            if is_search:
                fields = get_group_fields(group)
                # 关键：在这里调用查询，上下文已在上方 with 块中建立
                text_resp, markup, users = await do_query_page(chat.id, group.id, conf, fields, kw, 1)
                
                if users or (not kw and not users):
                    if not text_resp: text_resp = "😢 暂无数据"
                    sent = await msg.reply_html(text_resp, reply_markup=markup, link_preview_options=LinkPreviewOptions(is_disabled=True))
                    del_time = safe_int(conf.get('query_del_time'), 60)
                    if del_time > 0:
                        context.job_queue.run_once(_auto_delete_msg, del_time, data=sent)
                return

    except Exception as e:
        print(f"Msg Error: {e}")

# --- 分页逻辑 ---
async def do_query_page(chat_id, group_id, conf, fields, kw=None, page=1):
    # ⚡️ 修复：移除 create_app()，假定外部已建立 Context，或者在这里使用全局 app
    # 为了兼容 pagination_callback 和 on_message，这里做个判断
    # 如果已经在 Context 中（如 on_message 调用），此代码块复用 Context 还是会正常工作？
    # Flask SQLAlchemy 的 Context 是 Thread Local 的。
    # 因为我们在 async 函数中，建议显式使用 global_flask_app
    
    if not global_flask_app: return None, None, None

    # 使用 run_in_executor 避免阻塞 Async Loop
    def _sync_query():
        nonlocal page
        with global_flask_app.app_context():
            today = get_beijing_today()
            # Always filter by today's check-in, whether it's a keyword search or not
            # Requirement: "所有的查询只显示已经今日打卡的认证用户" (ALL queries should only show users who checked in today)
            base = GroupUser.query.filter(
                GroupUser.group_id == group_id,
                GroupUser.online == True,
                GroupUser.checkin_time >= today
            )
            
            if kw:
                base = base.filter(GroupUser.profile_data.contains(kw))
                header = conf.get('msg_filter_header', '🔍 <b>筛选结果：</b>')
            else:
                header = conf.get('msg_query_header', '🔍 <b>今日在线：</b>')
                
            # Requirement 4: Sort by check-in time (earliest first)
            # Note: We're filtering by checkin_time >= today, so NULL values are already excluded
            users = base.order_by(GroupUser.checkin_time.asc()).all()
            if not users: return None, None, None
            
            page_size = safe_int(conf.get('page_size'), 10)
            total_pages = math.ceil(len(users) / page_size) or 1
            if page > total_pages: page = total_pages
            if page < 1: page = 1
            
            start = (page - 1) * page_size
            current_users = users[start:start+page_size]
            
            tpl = conf.get('template', '{tg_id}')
            f_map = {f['key']: f['label'] for f in fields}
            lines = []
            for idx, u in enumerate(current_users):
                try:
                    d = json.loads(u.profile_data or '{}')
                    l = tpl.replace("{onlineEmoji}", conf.get('online_emoji',''))
                    for k, lbl in f_map.items(): l = l.replace(f"{{{lbl}}}", str(d.get(k,'')))
                    l = l.replace("{序号}", str(start + idx + 1))
                    l = l.replace("{tg_id}", str(u.tg_id))
                    _member_tags_text = format_member_tags(getattr(u, 'member_tags', None))
                    l = l.replace("{成员标签}", _member_tags_text).replace("{标签}", _member_tags_text)
                    lines.append(re.sub(r'\{.*?\}', '', l))
                except: continue
                
            # Add page number display at the end of text
            text = header + "\n\n" + "\n".join(lines)
            text = text + f"\n\n📄 第 {page}/{total_pages} 页"
            
            # Sanitize HTML before sending to Telegram
            text = sanitize_html_for_telegram(text)
            
            buttons = []
            nav_row = []
            safe_kw = kw if kw else "None"
            if page > 1: nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"pg|{page-1}|{safe_kw}"))
            if page < total_pages: nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"pg|{page+1}|{safe_kw}"))
            if nav_row: buttons.append(nav_row)
            
            custom_btns = conf.get('custom_buttons', '')
            if custom_btns:
                try:
                    btn_list = json.loads(custom_btns)
                    row = []
                    for btn in btn_list:
                        row.append(InlineKeyboardButton(btn['text'], url=btn['url']))
                        if len(row) == 2:
                            buttons.append(row)
                            row = []
                    if row: buttons.append(row)
                except: pass
                
            return text, InlineKeyboardMarkup(buttons), users

    # 在 Executor 中运行同步 DB 操作
    return await asyncio.get_running_loop().run_in_executor(None, _sync_query)

async def exchange_item_callback(update: Update, context):
    """处理兑换商品内联按钮点击（来自频道商品目录）"""
    query = update.callback_query
    user = update.effective_user

    # callback_data 格式: exchange_redeem_{item_id}_{group_id}
    try:
        parts = query.data.split('_')
        item_id = int(parts[2])
        group_id = int(parts[3])
    except (IndexError, ValueError):
        await query.answer("❌ 无效的兑换请求", show_alert=True)
        return

    if not global_flask_app:
        await query.answer("❌ 系统错误，请稍后重试", show_alert=True)
        return

    def _do_exchange():
        with global_flask_app.app_context():
            grp = BotGroup.query.get(group_id)
            if grp and not get_group_conf(grp).get('points_shop_enabled', True):
                return None, "❌ 积分商城已关闭"

            item = PointsExchangeItem.query.filter_by(
                id=item_id, group_id=group_id, is_active=True
            ).first()
            if not item:
                return None, "❌ 商品不存在或已下架"

            if item.stock is not None and item.stock <= 0:
                return None, "❌ 该商品库存已用完"

            user_pts = UserPoints.query.filter_by(
                group_id=group_id, user_id=user.id
            ).first()
            balance = user_pts.points_balance if user_pts else 0

            if balance < item.points_cost:
                return None, (
                    f"❌ 积分不足。\n"
                    f"需要 {item.points_cost} 积分，当前余额 {balance} 积分"
                )

            if not user_pts:
                user_pts = UserPoints(
                    group_id=group_id, user_id=user.id, points_balance=0
                )
                db.session.add(user_pts)

            user_pts.points_balance -= item.points_cost
            item_deactivated = False
            if item.stock is not None:
                item.stock -= 1
                if item.stock <= 0:
                    item.is_active = False
                    item_deactivated = True

            # Snapshot announcement info and channel before commit
            announcement_msg_id = item.announcement_msg_id
            channel_id = get_group_conf(grp).get('exchange_channel_id', '').strip() if grp else ''

            db.session.add(PointsExchangeRecord(
                group_id=group_id,
                user_id=user.id,
                item_id=item.id,
                points_spent=item.points_cost
            ))
            db.session.add(PointsLog(
                group_id=group_id,
                user_id=user.id,
                points_change=-item.points_cost,
                reason=f"兑换商品: {item.item_name}",
                balance_after=user_pts.points_balance
            ))
            db.session.commit()

            return {
                'item_name': item.item_name,
                'points_cost': item.points_cost,
                'balance': user_pts.points_balance,
                'redemption_info': item.redemption_info or '',
                'item_deactivated': item_deactivated,
                'announcement_msg_id': announcement_msg_id,
                'channel_id': channel_id,
            }, None

    result, error = await asyncio.get_running_loop().run_in_executor(None, _do_exchange)

    if error:
        await query.answer(error, show_alert=True)
        return

    alert = (
        f"✅ 兑换成功！\n"
        f"👤 兑换人：{_format_tg_user_display(user)}\n"
        f"🎁 商品：{result['item_name']}\n"
        f"💰 消耗积分：{result['points_cost']}  |  剩余积分：{result['balance']}"
    )
    if result['redemption_info']:
        alert += f"\n\n📋 兑换信息：\n{result['redemption_info']}"

    await query.answer(alert, show_alert=True)

    # If item sold out, delete its individual announcement and update catalog
    if result.get('item_deactivated'):
        await _delete_item_announcement_async(
            result['channel_id'], result['announcement_msg_id'], context.application
        )
    await _update_exchange_catalog_async(group_id, context.application)


async def pagination_callback(update: Update, context):
    """统一的回调处理器 - 根据callback data路由到不同的处理函数"""
    query = update.callback_query
    
    if query.data == "noop": 
        return await query.answer()
    
    # 路由到不同的处理器
    if query.data.startswith('quiz_answer_'):
        return await quiz_answer_callback(update, context)
    elif query.data.startswith('redpacket_claim_'):
        return await redpacket_claim_callback(update, context)
    elif query.data.startswith('vote_'):
        return await vote_callback(update, context)
    elif query.data.startswith('check_sub_'):
        return await handle_subscription_check_callback(update, context)
    elif query.data.startswith('exchange_redeem_'):
        return await exchange_item_callback(update, context)
    
    # 默认处理分页查询
    try:
        parts = query.data.split('|')
        page = int(parts[1])
        kw = parts[2] if parts[2] != "None" else None
        
        chat = update.effective_chat
        
        # ⚡️ 修复：使用全局 Flask App
        if not global_flask_app: return await query.answer("System Starting...")

        # 上面 lambda 写法太绕，直接用同步函数包装即可：
        def _get_group_info():
            with global_flask_app.app_context():
                g = BotGroup.query.filter_by(chat_id=str(chat.id), clone_id=context.application.bot_data.get('clone_id')).first()
                if not g: return None, None, None
                return g.id, get_group_conf(g), get_group_fields(g)
        
        res = await asyncio.get_running_loop().run_in_executor(None, _get_group_info)
        if not res or not res[0]: return await query.answer("Expired")
        gid, conf, fields = res

        text, markup, _ = await do_query_page(chat.id, gid, conf, fields, kw, page)
        if text:
            await query.edit_message_text(text=text, parse_mode='HTML', reply_markup=markup, link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as e: 
        print(f"Page Error: {e}")
    await query.answer()


# ---------------------------------------------------------------------------
# Backward-compatible re-exports
# ---------------------------------------------------------------------------
# Functions that have been extracted to dedicated modules are re-exported
# here so that existing call-sites throughout this file continue to work
# without modification during the transition period.
#
# Once all call-sites have been updated to import from their canonical
# location, these one-liners can be deleted.

# -- app.utils ---------------------------------------------------------------
from app.utils import (  # noqa: E402, F401
    get_beijing_now as _util_get_beijing_now,
    get_beijing_today as _util_get_beijing_today,
    get_muted_permissions as _util_get_muted_permissions,
    get_unrestricted_permissions as _util_get_unrestricted_permissions,
    is_user_admin_in_group as _util_is_user_admin_in_group,
    is_user_chat_owner as _util_is_user_chat_owner,
    convert_chat_id_to_int as _util_convert_chat_id_to_int,
    log_admin_action as _util_log_admin_action,
    safe_int as _util_safe_int,
    parse_telegram_message_link as _util_parse_telegram_message_link,
    build_inline_keyboard_from_links as _util_build_inline_keyboard_from_links,
)

# -- app.bot.handlers.commands -----------------------------------------------
# Canonical command handlers. Mid-file bodies were removed; these names are
# bound here so run_bot / setup_clone_handlers register the extracted versions.
from app.bot.handlers.commands import (  # noqa: E402
    cmd_kick,
    cmd_ban,
    cmd_unban,
    cmd_mute,
    cmd_unmute,
    cmd_pin,
    cmd_unpin,
    cmd_warn,
    cmd_userinfo,
    cmd_rank,
    cmd_invite_rank,
    cmd_active,
    cmd_clones,
)

# -- app.services ------------------------------------------------------------
from app.services.points_service import (  # noqa: E402, F401
    award_points,
    deduct_points,
    apply_points_change,
    InsufficientPointsError,
    get_or_create_user_points,
    record_points_transaction,
)
from app.services.lottery_service import (  # noqa: E402, F401
    pick_winners_random,
    pick_winner_weighted,
    pick_winners_top_n,
    run_lottery_draw,
    validate_lottery_draw,
)

# -- app.plugins --------------------------------------------------------------
from app.plugins import is_plugin_enabled, is_plugin_enabled_for_chat  # noqa: E402

# -- 小号 (userbot) admin pages/APIs: registers routes on core_bp --------------
import app.userbot.web  # noqa: E402,F401
