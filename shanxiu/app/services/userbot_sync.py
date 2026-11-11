"""Feed messages heard by the userbot (小号) into the sync pipeline; the MAIN bot sends.

Threading: the Telethon client runs on its own asyncio loop (``app.userbot.manager``).
It converts each Telethon message into a plain :class:`UbMessage` (no Telethon objects),
picks matching rules (:func:`plans_for_item`, pure) and submits :func:`process_item` to the
PTB bot loop, where album buffering (shared helpers in ``routes``) and sending happen.

Delivery per target:
* no name prefix → ``copy_message`` / ``copy_messages`` (main bot must be in the source chat);
* name prefix, ``forward`` style → ``forward_message(s)``, falls back to the newline style;
* name prefix, ``newline`` style → text: ``send_message`` (prefix + HTML, no source access
  needed); media: ``copy_message`` with a new caption; albums: name line, then ``copy_messages``;
* if copying fails (main bot not in source / protected content) → the userbot downloads the
  media and the main bot re-uploads it (text is simply re-sent);
* inline buttons are extracted from Telethon and re-attached on copy/re-upload (URL buttons
  work; callback_data buttons will hit *our* bot — Telegram cannot proxy another bot's callbacks).
"""

from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Iterable, Optional

from app.services.sync_service import (
    CAPTION_LIMIT,
    build_group_sender_prefix,
    combine_prefix_html,
    keyword_blocks_sync,
    normalize_prefix_style,
    prefix_fits,
)

TEXT_LIMIT = 4096
ALBUM_KINDS = ('photo', 'video', 'document', 'audio')
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # Bot API upload limit

DownloadFn = Callable[[int, int], Awaitable[Optional[tuple]]]


def buttons_to_markup(rows: Optional[list]):
    """Serializeable button rows → PTB InlineKeyboardMarkup (or None)."""
    if not rows:
        return None
    from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    kb = []
    for row in rows:
        out = []
        for b in row or ():
            if not isinstance(b, dict):
                continue
            label = b.get('text') or ''
            if not label:
                continue
            if b.get('url'):
                out.append(InlineKeyboardButton(label, url=b['url']))
            elif b.get('web_app'):
                out.append(InlineKeyboardButton(label, web_app=WebAppInfo(url=b['web_app'])))
            elif b.get('callback_data') is not None:
                out.append(InlineKeyboardButton(label, callback_data=b['callback_data']))
            elif 'switch_inline_query' in b:
                out.append(InlineKeyboardButton(label, switch_inline_query=b.get('switch_inline_query') or ''))
            elif 'switch_inline_query_current_chat' in b:
                out.append(InlineKeyboardButton(
                    label, switch_inline_query_current_chat=b.get('switch_inline_query_current_chat') or ''))
            elif b.get('copy_text'):
                out.append(InlineKeyboardButton(label, copy_text=CopyTextButton(b['copy_text'])))
        if out:
            kb.append(out)
    return InlineKeyboardMarkup(kb) if kb else None


async def _reattach_buttons(bot, target, items: list, sent):
    """After album send/copy, put inline keyboards back (media groups drop them)."""
    if not items:
        return
    if not isinstance(sent, (list, tuple)):
        sent = [sent] if sent is not None else []
    n = min(len(items), len(sent))
    for i in range(n):
        markup = buttons_to_markup(getattr(items[i], 'buttons', None))
        if not markup:
            continue
        mid = _mid(sent[i])
        if mid is None:
            continue
        try:
            await bot.edit_message_reply_markup(chat_id=target, message_id=mid, reply_markup=markup)
        except Exception as e:
            print(f"[userbot-sync] reattach buttons failed msg={mid}: {type(e).__name__}", flush=True)




@dataclass
class UbMessage:
    chat_id: int
    message_id: int
    chat_title: str = ''
    grouped_id: Optional[str] = None
    text: str = ''          # raw text or caption
    html: str = ''          # Telegram-HTML of text/caption
    media_kind: Optional[str] = None  # photo|video|animation|audio|voice|video_note|sticker|document|other
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    sender_id: Optional[int] = None
    sender_name: str = ''
    sender_username: Optional[str] = None
    sender_is_bot: bool = False
    is_forward: bool = False
    # [[ {'text','url'|'callback_data'|'web_app'|…}, …], …] — serializable inline keyboard
    buttons: Optional[list] = None
    # Sent by our main bot / a clone bot. Synced like any bot, unless it is a sync output.
    from_own_bot: bool = False


# ---------------------------------------------------------------------------
# Pure selection logic
# ---------------------------------------------------------------------------

def parse_sender_filter(text: Optional[str]) -> tuple[set, set]:
    """'@abc_bot, 12345\n@Other' → ({12345}, {'abc_bot', 'other'})."""
    ids, names = set(), set()
    for tok in re.split(r'[\s,，;；]+', text or ''):
        tok = tok.strip()
        if not tok:
            continue
        if re.fullmatch(r'-?\d+', tok):
            ids.add(int(tok))
        else:
            names.add(tok.lstrip('@').lower())
    return ids, names


def sender_matches(mode: Optional[str], sender_filter: Optional[str], item: UbMessage) -> bool:
    """bots (default) | selected | users (humans only) | all."""
    mode = (mode or 'bots').lower()
    if mode == 'all':
        return True
    if mode == 'users':
        return not item.sender_is_bot
    if not item.sender_is_bot:
        return False
    if mode == 'selected':
        ids, names = parse_sender_filter(sender_filter)
        if not ids and not names:
            return False
        return (item.sender_id in ids) or ((item.sender_username or '').lower() in names)
    return True  # 'bots'


def parse_targets(raw: Any) -> list:
    if isinstance(raw, (list, tuple)):
        items = raw
    else:
        try:
            items = json.loads(raw or '[]')
        except (TypeError, ValueError):
            items = re.split(r'[\s,，]+', str(raw or ''))
        if not isinstance(items, list):
            items = [items]
    out = []
    for t in items:
        s = str(t).strip()
        if re.fullmatch(r'-?\d+', s) and s not in out:
            out.append(s)
    return out


def build_prefix(item: UbMessage, include: bool, *, plain: bool = False, mention_only: bool = False) -> str:
    """Sender name line. Users → ``tg://user?id=`` link; channels / anonymous admins
    (negative ids) → ``t.me/<username>`` link or plain name (tg://user with a chat id is
    invalid and made Telegram reject the whole message). *plain* = no link at all."""
    if not include:
        return ''
    import html as _html
    name = (item.sender_name or item.sender_username or '').strip() or '未知'
    if plain:
        return f"{_html.escape(name)}\n"
    sid = item.sender_id
    if item.sender_username and not mention_only:
        # t.me/<username> is always clickable for every viewer; tg://user?id= only works
        # when the main bot already "knows" the user (else Telegram drops / rejects it).
        return f'<a href="https://t.me/{_html.escape(item.sender_username)}">{_html.escape(name)}</a>\n'
    if isinstance(sid, int) and sid > 0:
        user = SimpleNamespace(id=sid, first_name=name, last_name='')
        return build_group_sender_prefix(user, True)
    if item.sender_username:
        return f'<a href="https://t.me/{_html.escape(item.sender_username)}">{_html.escape(name)}</a>\n'
    return f"{_html.escape(name)}\n"


def _is_entity_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return 'entit' in s or "can't parse" in s or 'parse entities' in s


def plans_for_item(rules: Iterable[dict], item: UbMessage, *, own_bot_ids: Iterable[int] = (),
                   main_synced_pairs: Iterable[tuple] = ()) -> list:
    """Return per-target send plans for *item* (deduped by target).

    * messages from our own main/clone bots ARE relayed like other bots' messages; loops are
      prevented precisely in :func:`process_item` (skip only sync / forward outputs).
      ``own_bot_ids`` is kept for API compatibility and only marks ``item.from_own_bot``;
    * in 'all' / 'users' mode, non-bot messages are skipped for targets the main bot already
      syncs itself from the same source (the Bot API delivers those to us directly).
    """
    if item.sender_id is not None and item.sender_id in set(own_bot_ids or ()):
        item.from_own_bot = True
    synced = {(str(s), str(t)) for s, t in (main_synced_pairs or ())}
    src = str(item.chat_id)
    plans, seen = [], set()
    for rule in rules or ():
        if not rule.get('enabled', True) or str(rule.get('source_chat_id')) != src:
            continue
        if not sender_matches(rule.get('sender_mode'), rule.get('sender_filter'), item):
            continue
        if item.media_kind and not rule.get('sync_media', True):
            continue
        for target in parse_targets(rule.get('target_chat_ids')):
            if target == src or target in seen:
                continue
            if not item.sender_is_bot and (src, target) in synced:
                continue
            seen.add(target)
            plans.append({
                'rule_id': rule.get('id'),
                'target_chat_id': target,
                'include_sender_prefix': bool(rule.get('include_sender_prefix')),
                'sender_prefix': build_prefix(item, bool(rule.get('include_sender_prefix'))),
                'prefix_style': normalize_prefix_style(rule.get('sender_prefix_style')),
                'filter_keywords': rule.get('filter_keywords') or '[]',
                'source_group_id': rule.get('source_group_id'),
            })
    return plans


class RecentSeen:
    """Small TTL set of (chat_id, message_id) so a message is relayed at most once."""

    def __init__(self, ttl: float = 900.0, max_items: int = 5000):
        self.ttl, self.max_items = ttl, max_items
        self._d: OrderedDict = OrderedDict()

    def check_and_add(self, key) -> bool:
        """True if *key* is new (and records it)."""
        now = time.monotonic()
        while self._d:
            k, ts = next(iter(self._d.items()))
            if now - ts > self.ttl or len(self._d) > self.max_items:
                self._d.popitem(last=False)
            else:
                break
        if key in self._d:
            return False
        self._d[key] = now
        return True


# ---------------------------------------------------------------------------
# Delivery (runs on the PTB bot loop)
# ---------------------------------------------------------------------------

def _mid(result) -> Optional[int]:
    if result is None:
        return None
    if isinstance(result, (list, tuple)):
        return _mid(result[0]) if result else None
    return getattr(result, 'message_id', None)


async def _send_header(bot, target, prefix):
    from telegram import LinkPreviewOptions
    return await bot.send_message(chat_id=target, text=(prefix or '').rstrip('\n') or '未知',
                                  parse_mode='HTML', link_preview_options=LinkPreviewOptions(is_disabled=True))


async def _send_text(bot, target, item: UbMessage, prefix: str):
    from telegram import LinkPreviewOptions
    body = item.html or ''
    if prefix and not prefix_fits(prefix, item.text, TEXT_LIMIT):
        await _send_header(bot, target, prefix)
        prefix = ''
    text = combine_prefix_html(prefix, body) if prefix else body
    markup = buttons_to_markup(item.buttons)
    return await bot.send_message(chat_id=target, text=text or '(空消息)', parse_mode='HTML',
                                  link_preview_options=LinkPreviewOptions(is_disabled=False),
                                  reply_markup=markup)


async def _reupload_single(bot, target, item: UbMessage, prefix: str, download: Optional[DownloadFn]):
    if not item.media_kind:
        return await _send_text(bot, target, item, prefix)
    if download is None:
        raise RuntimeError('main bot cannot copy and no userbot download available')
    got = await download(item.chat_id, item.message_id)
    if not got:
        raise RuntimeError('userbot media download failed or file too large')
    data, filename = got
    from telegram import InputFile
    header_sep = bool(prefix) and not prefix_fits(prefix, item.text, CAPTION_LIMIT)
    if header_sep:
        await _send_header(bot, target, prefix)
        prefix = ''
    caption = (combine_prefix_html(prefix, item.html) if prefix else item.html) or None
    markup = buttons_to_markup(item.buttons)
    kw = {'chat_id': target}
    if markup is not None:
        kw['reply_markup'] = markup
    cap_kw = {'caption': caption, 'parse_mode': 'HTML' if caption else None}
    f = InputFile(data, filename=filename or item.file_name or 'file')
    kind = item.media_kind
    if kind == 'photo':
        return await bot.send_photo(photo=f, **kw, **cap_kw)
    if kind == 'video':
        return await bot.send_video(video=f, **kw, **cap_kw)
    if kind == 'animation':
        return await bot.send_animation(animation=f, **kw, **cap_kw)
    if kind == 'audio':
        return await bot.send_audio(audio=f, **kw, **cap_kw)
    if kind == 'voice':
        return await bot.send_voice(voice=f, **kw, **cap_kw)
    if kind == 'video_note':
        # video_note has no caption/buttons in Bot API — send note then optional markup msg skip
        return await bot.send_video_note(video_note=f, chat_id=target)
    if kind == 'sticker':
        return await bot.send_sticker(sticker=f, **kw)
    return await bot.send_document(document=f, **kw, **cap_kw)


async def deliver_single(bot, item: UbMessage, plan: dict, download: Optional[DownloadFn] = None):
    """Returns (target_message_id, method)."""
    from app.services.sync_loop_guard import track_bot
    bot = track_bot(bot)  # record outputs → precise anti-loop
    target = plan['target_chat_id']
    prefix = plan.get('sender_prefix') or ''
    style = plan.get('prefix_style') or 'newline'
    src = item.chat_id
    markup = buttons_to_markup(item.buttons)

    if prefix and style == 'forward':
        try:
            # forward_message keeps original buttons; cannot attach a new markup
            return _mid(await bot.forward_message(chat_id=target, from_chat_id=src, message_id=item.message_id)), 'forward'
        except Exception as e:
            print(f"[userbot-sync] forward failed → newline style: {type(e).__name__}", flush=True)

    if prefix and not item.media_kind:
        return _mid(await _send_text(bot, target, item, prefix)), 'send_message'

    # No media and no prefix → still may need buttons (copy drops markup unless re-passed)
    if not item.media_kind and not prefix:
        try:
            kw = dict(chat_id=target, from_chat_id=src, message_id=item.message_id)
            if markup is not None:
                kw['reply_markup'] = markup
            return _mid(await bot.copy_message(**kw)), 'copy'
        except Exception as e:
            print(f"[userbot-sync] copy failed ({type(e).__name__}: {e}) → re-upload via userbot", flush=True)
            return _mid(await _send_text(bot, target, item, '')), 'send_message'

    try:
        if not prefix:
            kw = dict(chat_id=target, from_chat_id=src, message_id=item.message_id)
            if markup is not None:
                kw['reply_markup'] = markup
            return _mid(await bot.copy_message(**kw)), 'copy'
        if prefix_fits(prefix, item.text, CAPTION_LIMIT) and item.media_kind not in ('sticker', 'video_note'):
            caption = combine_prefix_html(prefix, item.html)
            kw = dict(chat_id=target, from_chat_id=src, message_id=item.message_id,
                      caption=caption, parse_mode='HTML')
            if markup is not None:
                kw['reply_markup'] = markup
            return _mid(await bot.copy_message(**kw)), 'copy'
        await _send_header(bot, target, prefix)
        prefix = ''
        kw = dict(chat_id=target, from_chat_id=src, message_id=item.message_id)
        if markup is not None:
            kw['reply_markup'] = markup
        return _mid(await bot.copy_message(**kw)), 'copy+header'
    except Exception as e:
        print(f"[userbot-sync] copy failed ({type(e).__name__}: {e}) → re-upload via userbot", flush=True)
    return _mid(await _reupload_single(bot, target, item, prefix, download)), 'reupload'


async def _reupload_album(bot, target, items: list, prefix: str, download: Optional[DownloadFn]):
    from telegram import InputFile, InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo
    if download is None:
        raise RuntimeError('main bot cannot copy and no userbot download available')
    cap_idx = next((i for i, m in enumerate(items) if m.text), 0)
    if prefix and not prefix_fits(prefix, items[cap_idx].text, CAPTION_LIMIT):
        await _send_header(bot, target, prefix)
        prefix = ''
    media = []
    for i, m in enumerate(items):
        if m.media_kind not in ALBUM_KINDS:
            continue
        got = await download(m.chat_id, m.message_id)
        if not got:
            continue
        data, filename = got
        cap = m.html or ''
        if i == cap_idx and prefix:
            cap = combine_prefix_html(prefix, cap)
        cap = cap or None
        f = InputFile(data, filename=filename or m.file_name or 'file')
        cls = {'photo': InputMediaPhoto, 'video': InputMediaVideo,
               'document': InputMediaDocument, 'audio': InputMediaAudio}[m.media_kind]
        media.append(cls(media=f, caption=cap, parse_mode='HTML' if cap else None))
    if not media:
        raise RuntimeError('album re-upload: nothing downloadable')
    return await bot.send_media_group(chat_id=target, media=media)


async def deliver_album(bot, items: list, plan: dict, download: Optional[DownloadFn] = None):
    """Send buffered album *items* to one target as ONE media group. Returns (first_id, method)."""
    from app.services.sync_loop_guard import track_bot
    bot = track_bot(bot)  # record outputs → precise anti-loop
    items = sorted(items, key=lambda m: m.message_id)
    target = plan['target_chat_id']
    prefix = plan.get('sender_prefix') or ''
    style = plan.get('prefix_style') or 'newline'
    src = items[0].chat_id
    ids = [m.message_id for m in items]

    if prefix and style == 'forward':
        try:
            sent = await bot.forward_messages(chat_id=target, from_chat_id=src, message_ids=ids)
            # forward keeps original markups when possible
            return _mid(sent), 'forward_messages'
        except Exception as e:
            print(f"[userbot-sync] album forward failed → newline style: {type(e).__name__}", flush=True)

    header_sent = False
    try:
        if prefix:
            await _send_header(bot, target, prefix)
            header_sent = True
        sent = await bot.copy_messages(chat_id=target, from_chat_id=src, message_ids=ids)
        await _reattach_buttons(bot, target, items, sent)
        return _mid(sent), ('header+copy_messages' if prefix else 'copy_messages')
    except Exception as e:
        print(f"[userbot-sync] album copy failed ({type(e).__name__}: {e}) → re-upload via userbot", flush=True)
    sent = await _reupload_album(bot, target, items, '' if header_sent else prefix, download)
    await _reattach_buttons(bot, target, items, sent)
    return _mid(sent), 'reupload_album'


async def _deliver_entity_fallback(bot, item: UbMessage, plan: dict, download, first_err):
    """Telegram rejected formatting (Entity_text_invalid …). Degrade step by step so we
    learn which part was bad and keep as much as possible:
    1) plain name + original formatted body (the name link was the problem);
    2) plain name + plain body (the body entities were the problem)."""
    import html as _html
    print(f"[userbot-sync] entity error ({first_err}) → retry plain name", flush=True)
    has_prefix = bool(plan.get('sender_prefix'))
    plan1 = {**plan, 'prefix_style': 'newline', 'sender_prefix': build_prefix(item, has_prefix, plain=True)}
    try:
        mid, method = await deliver_single(bot, item, plan1, download)
        return mid, method + '+plain_name'
    except Exception as e:
        if not _is_entity_error(e):
            raise
        print(f"[userbot-sync] still entity error ({e}) → retry plain body", flush=True)
    safe_item = UbMessage(**{**item.__dict__, 'html': _html.escape(item.text or '')})
    mid, method = await deliver_single(bot, safe_item, plan1, download)
    return mid, method + '+plain_all'


def _log(flask_app, item: UbMessage, plan: dict, *, status: str, message_type: str,
         target_message_id=None, error: Optional[str] = None, preview: Optional[str] = None):
    try:
        from app import db
        from app.models import SyncMessageLog
        from app.utils import get_beijing_now
        with flask_app.app_context():
            db.session.add(SyncMessageLog(
                source_group_id=plan.get('source_group_id'),
                target_group_id=str(plan['target_chat_id']),
                source_message_id=item.message_id,
                target_message_id=target_message_id,
                user_id=item.sender_id,
                username=item.sender_username or (item.sender_name or None),
                message_type=message_type,
                content_preview=(preview if preview is not None else (item.text or '')[:100]) or None,
                status=status,
                error_message=(error or None) and str(error)[:500],
                synced_at=get_beijing_now(),
                via='userbot',
            ))
            db.session.commit()
    except Exception as e:  # logging must never break delivery
        print(f"[userbot-sync] log write failed: {type(e).__name__}", flush=True)


async def deliver_to_plans(bot, flask_app, item: UbMessage, plans: list, download: Optional[DownloadFn] = None):
    for plan in plans:
        if keyword_blocks_sync(item.text, plan.get('filter_keywords')):
            _log(flask_app, item, plan, status='filtered', message_type=item.media_kind or 'text')
            continue
        try:
            try:
                mid, method = await deliver_single(bot, item, plan, download)
            except Exception as e:
                if not _is_entity_error(e):
                    raise
                mid, method = await _deliver_entity_fallback(bot, item, plan, download, e)
            print(f"[userbot-sync] chat={item.chat_id} msg={item.message_id} -> {plan['target_chat_id']} "
                  f"via {method} prefix={'on' if plan.get('sender_prefix') else 'off'}", flush=True)
            _log(flask_app, item, plan, status='success', message_type=item.media_kind or 'text',
                 target_message_id=mid)
        except Exception as e:
            print(f"[userbot-sync] send to {plan['target_chat_id']} failed: {type(e).__name__}: {e}", flush=True)
            _log(flask_app, item, plan, status='failed', message_type=item.media_kind or 'text', error=str(e))


# Album buffer (shared debounce helpers from routes; kind='userbot').
USERBOT_MEDIA_GROUP_BUFFERS: dict = {}


def _album_key(item: UbMessage):
    return ('userbot', str(item.chat_id), str(item.grouped_id))


async def flush_userbot_album(buffer_key):
    from app.modules.core import routes
    buf = USERBOT_MEDIA_GROUP_BUFFERS.pop(buffer_key, None)
    if not buf:
        return
    routes._note_album_flushed('userbot', buffer_key, buf)
    items = sorted(buf.get('messages') or [], key=lambda m: m.message_id)
    if not items:
        return
    bot, flask_app, download = buf['bot'], buf['flask_app'], buf.get('download')
    caption = next((m.text for m in items if m.text), '')
    head = items[0]
    for plan in buf.get('plans') or []:
        if keyword_blocks_sync(caption, plan.get('filter_keywords')):
            _log(flask_app, head, plan, status='filtered', message_type='media_group', preview=caption[:100])
            continue
        try:
            try:
                mid, method = await deliver_album(bot, items, plan, download)
            except Exception as e:
                if not (_is_entity_error(e) and plan.get('sender_prefix')):
                    raise
                print(f"[userbot-sync] album entity error ({e}) → retry plain name", flush=True)
                plan = {**plan, 'prefix_style': 'newline',
                        'sender_prefix': build_prefix(head, True, plain=True)}
                mid, method = await deliver_album(bot, items, plan, download)
                method += '+plain_name'
            print(f"{routes._album_log_prefix('userbot', buffer_key)} -> {plan['target_chat_id']} "
                  f"items={len(items)} via {method} prefix={'on' if plan.get('sender_prefix') else 'off'}",
                  flush=True)
            _log(flask_app, head, plan, status='success', message_type='media_group',
                 target_message_id=mid, preview=caption[:100])
        except Exception as e:
            print(f"[userbot-sync] album to {plan['target_chat_id']} failed: {type(e).__name__}: {e}", flush=True)
            _log(flask_app, head, plan, status='failed', message_type='media_group', error=str(e),
                 preview=caption[:100])


async def process_item(bot, flask_app, item: UbMessage, plans: list, download: Optional[DownloadFn] = None):
    """Entry point on the bot loop."""
    if not plans:
        return
    if item.from_own_bot:
        # Precise loop guard: our own bot's message is skipped only if WE produced it via
        # sync / forward (registered per target chat). The userbot can hear it before the
        # send response is parsed, so re-check after a short delay.
        from app.services.sync_loop_guard import wait_is_sync_output
        if await wait_is_sync_output(item.chat_id, item.message_id, flask_app):
            print(f"[userbot-sync] skip sync output chat={item.chat_id} msg={item.message_id} (loop guard)",
                  flush=True)
            return
    if item.grouped_id and item.media_kind in ALBUM_KINDS:
        from app.modules.core import routes
        key = _album_key(item)
        routes._media_group_buffer_add(USERBOT_MEDIA_GROUP_BUFFERS, key, item, kind='userbot', init={
            'bot': bot, 'flask_app': flask_app, 'plans': plans, 'download': download,
        })
        routes._schedule_media_group_flush(USERBOT_MEDIA_GROUP_BUFFERS, key, flush_userbot_album,
                                           routes._SYNC_MEDIA_GROUP_FLUSH_DELAY)
        return
    await deliver_to_plans(bot, flask_app, item, plans, download)
