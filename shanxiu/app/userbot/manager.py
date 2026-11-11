"""Userbot (小号) runtime + multi-step login, on a dedicated asyncio loop thread.

* Flask request threads call the blocking helpers (``login_send_code`` …); they submit
  coroutines to the userbot loop with ``run_coroutine_threadsafe`` and wait for the result.
* Login state (Telethon client + phone_code_hash) lives in memory keyed by a random flow
  id that is stored in the admin's Flask session; flows expire after 15 minutes.
* The runtime client is listen-only: it never sends, joins, reads/acks, or sets online.
* Auth failures mark the account ``invalid`` (shown in the UI) and never touch the main bot.

Secrets (phone, code, password, session string, api_hash) are never logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from app.userbot.crypto import decrypt_secret, encrypt_secret, mask_phone

logger = logging.getLogger(__name__)

FLOW_TTL = 15 * 60
RULES_TTL = 15.0
DEVICE = dict(device_model='Shanxiu Sync', system_version='Linux', app_version='1.0', lang_code='zh',
              system_lang_code='zh')

_ERROR_MESSAGES = {
    'PhoneNumberInvalidError': '手机号无效，请带国家区号，例如 +8613812345678。',
    'PhoneNumberBannedError': '该手机号已被 Telegram 封禁。',
    'PhoneNumberUnoccupiedError': '该手机号还没有注册 Telegram。',
    'PhoneCodeInvalidError': '验证码错误。',
    'PhoneCodeEmptyError': '请输入验证码。',
    'PhoneCodeExpiredError': '验证码已过期，请重新获取。',
    'PasswordHashInvalidError': '两步验证密码错误。',
    'ApiIdInvalidError': 'api_id / api_hash 无效，请重新获取或检查手动填写的值。',
    'ApiIdPublishedFloodError': '这个 api_id 已被滥用封锁，请换一个。',
    'PhoneNumberFloodError': '验证码请求次数过多，请稍后再试。',
    'SendCodeUnavailableError': '验证码发送次数已用完，请稍后再试。',
    'PhonePasswordFloodError': '密码尝试次数过多，请稍后再试。',
    'AuthKeyDuplicatedError': '会话在两个地方同时使用，被 Telegram 作废了，请重新登录。',
    'AuthKeyUnregisteredError': '会话已失效（可能在 Telegram「设备」里被终止），请重新登录。',
    'SessionRevokedError': '会话已被终止，请重新登录。',
    'SessionExpiredError': '会话已过期，请重新登录。',
    'UserDeactivatedError': '账号已被注销。',
    'UserDeactivatedBanError': '账号已被 Telegram 封禁。',
}
_INVALIDATING = {'AuthKeyDuplicatedError', 'AuthKeyUnregisteredError', 'SessionRevokedError',
                 'SessionExpiredError', 'UserDeactivatedError', 'UserDeactivatedBanError', 'AuthKeyInvalidError'}


class UserbotError(Exception):
    """zh-CN user-facing error."""


def describe_error(e: BaseException) -> str:
    name = type(e).__name__
    if name == 'FloodWaitError':
        return f'操作太频繁，Telegram 要求等待 {getattr(e, "seconds", "?")} 秒后再试。'
    if name in _ERROR_MESSAGES:
        return _ERROR_MESSAGES[name]
    if isinstance(e, UserbotError):
        return str(e)
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return '连接 Telegram 超时，请重试。'
    return f'{name}: {str(e)[:200]}'


# ---------------------------------------------------------------------------
# Telethon message → UbMessage (pure; tested with fakes)
# ---------------------------------------------------------------------------

_ENTITY_MAP = {
    'bold': 'bold', 'italic': 'italic', 'underline': 'underline', 'strike': 'strikethrough',
    'spoiler': 'spoiler', 'code': 'code', 'pre': 'pre', 'texturl': 'text_link',
    'mentionname': 'text_mention', 'inputmentionname': 'text_mention', 'url': 'url',
    'email': 'email', 'customemoji': 'custom_emoji',
}


def telethon_entities_to_dicts(entities) -> list:
    out = []
    for e in entities or ():
        name = type(e).__name__.replace('InputMessageEntity', 'input').replace('MessageEntity', '').lower()
        etype = _ENTITY_MAP.get(name)
        if not etype:
            continue
        d = {'type': etype, 'offset': getattr(e, 'offset', None), 'length': getattr(e, 'length', None)}
        if etype == 'pre':
            d['language'] = getattr(e, 'language', None) or None
        elif etype == 'text_link':
            d['url'] = getattr(e, 'url', '')
        elif etype == 'text_mention':
            uid = getattr(e, 'user_id', None)
            d['user'] = {'id': uid if isinstance(uid, int) else getattr(uid, 'user_id', None)}
        elif etype == 'custom_emoji':
            d['custom_emoji_id'] = getattr(e, 'document_id', None)
        out.append(d)
    return out


def media_kind_of(msg) -> Optional[str]:
    if getattr(msg, 'sticker', None):
        return 'sticker'
    if getattr(msg, 'gif', None):
        return 'animation'
    if getattr(msg, 'video_note', None):
        return 'video_note'
    if getattr(msg, 'voice', None):
        return 'voice'
    if getattr(msg, 'video', None):
        return 'video'
    if getattr(msg, 'audio', None):
        return 'audio'
    if getattr(msg, 'photo', None):
        return 'photo'
    if getattr(msg, 'document', None):
        return 'document'
    if any(getattr(msg, a, None) for a in ('poll', 'geo', 'contact', 'venue', 'dice', 'game', 'invoice')):
        return 'other'
    return None



def telethon_reply_markup_to_rows(reply_markup) -> Optional[list]:
    """ReplyInlineMarkup → [[button_dict, …], …] for the PTB send path.

    Supported: url / web_app / callback_data / switch_inline_query /
    switch_inline_query_current_chat / copy_text. Buy/Game/UrlAuth/etc. skipped.
    Works with Telethon 1.45 KeyboardInlineButton(.type) and legacy KeyboardButton*.
    """
    rows_in = getattr(reply_markup, 'rows', None) if reply_markup is not None else None
    if not rows_in:
        return None
    out = []
    for row in rows_in:
        buttons = getattr(row, 'buttons', None) or ()
        out_row = []
        for btn in buttons:
            text = getattr(btn, 'text', None) or ''
            if not text:
                continue
            mapped = _map_telethon_button(btn, text)
            if mapped:
                out_row.append(mapped)
        if out_row:
            out.append(out_row)
    return out or None


def _map_telethon_button(btn, text: str) -> Optional[dict]:
    typ = getattr(btn, 'type', None)
    if typ is not None:
        name = type(typ).__name__
        if name == 'InlineButtonTypeUrl':
            return {'text': text, 'url': typ.url}
        if name == 'InlineButtonTypeWebView':
            return {'text': text, 'web_app': typ.url}
        if name == 'InlineButtonTypeCallback':
            data = typ.data
            if isinstance(data, bytes):
                try:
                    data = data.decode('utf-8')
                except UnicodeDecodeError:
                    data = data.hex()
            raw = data if isinstance(data, (bytes, bytearray)) else str(data).encode('utf-8')
            if len(raw) > 64:
                return None
            return {'text': text, 'callback_data': data if isinstance(data, str) else data.decode('latin-1')}
        if name == 'InlineButtonTypeSwitchInline':
            if getattr(typ, 'same_peer', None):
                return {'text': text, 'switch_inline_query_current_chat': typ.query or ''}
            return {'text': text, 'switch_inline_query': typ.query or ''}
        if name == 'InlineButtonTypeCopy':
            return {'text': text, 'copy_text': typ.copy_text}
        return None
    # Legacy KeyboardButton* (pre-1.45 / custom fakes)
    url = getattr(btn, 'url', None)
    if url:
        return {'text': text, 'url': url}
    data = getattr(btn, 'data', None)
    if data is not None:
        if isinstance(data, bytes):
            try:
                data = data.decode('utf-8')
            except UnicodeDecodeError:
                data = data.hex()
        raw = str(data).encode('utf-8')
        if len(raw) > 64:
            return None
        return {'text': text, 'callback_data': str(data)}
    if getattr(btn, 'query', None) is not None and type(btn).__name__ == 'KeyboardButtonSwitchInline':
        if getattr(btn, 'same_peer', False):
            return {'text': text, 'switch_inline_query_current_chat': btn.query or ''}
        return {'text': text, 'switch_inline_query': btn.query or ''}
    return None


def to_ub_message(msg, chat_id: int, sender, chat_title: str = ''):
    from app.services.sync_service import entities_to_html
    from app.services.userbot_sync import UbMessage
    text = getattr(msg, 'message', None) or ''
    f = getattr(msg, 'file', None)
    kind = media_kind_of(msg)
    sender_id = sender_name = sender_username = None
    is_bot = False
    if sender is not None:
        if hasattr(sender, 'first_name') or hasattr(sender, 'bot'):
            sender_id = getattr(sender, 'id', None)
            sender_name = ' '.join(x for x in (getattr(sender, 'first_name', None), getattr(sender, 'last_name', None)) if x)
            is_bot = bool(getattr(sender, 'bot', False))
        else:  # Channel / Chat posting as sender_chat
            raw = getattr(sender, 'id', None)
            sender_id = int(f'-100{raw}') if isinstance(raw, int) and raw > 0 else raw
            sender_name = getattr(sender, 'title', None) or ''
        sender_username = getattr(sender, 'username', None)
    gid = getattr(msg, 'grouped_id', None)
    return UbMessage(
        chat_id=int(chat_id), message_id=int(msg.id), chat_title=chat_title or '',
        grouped_id=str(gid) if gid else None,
        text=text, html=entities_to_html(text, telethon_entities_to_dicts(getattr(msg, 'entities', None))),
        media_kind=kind,
        file_name=getattr(f, 'name', None) if (f is not None and kind) else None,
        file_size=getattr(f, 'size', None) if (f is not None and kind) else None,
        sender_id=sender_id, sender_name=sender_name or '', sender_username=sender_username,
        sender_is_bot=is_bot, is_forward=bool(getattr(msg, 'fwd_from', None)),
        buttons=telethon_reply_markup_to_rows(getattr(msg, 'reply_markup', None)),
    )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class UserbotManager:
    def __init__(self):
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self.flask_app = None
        self.client = None
        self.runtime_task = None
        self._stopping = False
        self.state = 'stopped'  # stopped | starting | active | invalid | error
        self.last_error: Optional[str] = None
        self._login_flows: dict = {}
        self._mt_flows: dict = {}
        self._msg_cache: OrderedDict = OrderedDict()
        from app.services.userbot_sync import RecentSeen
        self._seen = RecentSeen()
        self._rules_cache: tuple = (0.0, [], frozenset(), frozenset())
        self.stats = {'relayed': 0, 'last_relay_at': None}

    # -- loop plumbing ----------------------------------------------------
    def ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self.loop and self._thread and self._thread.is_alive():
                return self.loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _runner():
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()

            self._thread = threading.Thread(target=_runner, name='userbot-loop', daemon=True)
            self._thread.start()
            ready.wait(5)
            self.loop = loop
            return loop

    def run(self, coro, timeout: float = 60.0):
        loop = self.ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return fut.result(timeout)
        except TimeoutError:
            fut.cancel()
            raise UserbotError('操作超时，请重试。') from None

    # -- my.telegram.org (sync, request thread) ----------------------------
    def _gc_flows(self):
        now = time.monotonic()
        for store in (self._mt_flows, self._login_flows):
            for k in [k for k, v in store.items() if now - v.get('created', now) > FLOW_TTL]:
                flow = store.pop(k, None)
                client = (flow or {}).get('client')
                if client is not None and self.loop:
                    asyncio.run_coroutine_threadsafe(self._safe_disconnect(client), self.loop)

    def mt_send_code(self, phone: str) -> str:
        from app.userbot.my_telegram import MyTelegramFlow
        with self._lock:
            self._gc_flows()
        flow = MyTelegramFlow(phone)
        flow.send_password()
        fid = secrets.token_urlsafe(16)
        with self._lock:
            self._mt_flows[fid] = {'flow': flow, 'created': time.monotonic(), 'phone': phone}
        return fid

    def mt_verify(self, fid: str, code: str) -> tuple:
        with self._lock:
            entry = self._mt_flows.get(fid)
        if not entry:
            raise UserbotError('获取 API 的流程已过期，请重新发送验证码。')
        flow = entry['flow']
        flow.login(code)
        api_id, api_hash = flow.fetch_or_create_app()
        with self._lock:
            self._mt_flows.pop(fid, None)
        return api_id, api_hash, entry['phone']

    # -- Telethon login (request thread → userbot loop) ---------------------
    @staticmethod
    async def _safe_disconnect(client):
        try:
            await client.disconnect()
        except Exception:
            pass

    async def _a_send_code(self, api_id: int, api_hash: str, phone: str):
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(), int(api_id), api_hash, receive_updates=False, **DEVICE)
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
            return client, sent.phone_code_hash
        except BaseException:
            await self._safe_disconnect(client)
            raise

    def login_send_code(self, api_id: int, api_hash: str, phone: str) -> str:
        with self._lock:
            self._gc_flows()
        try:
            client, pch = self.run(self._a_send_code(api_id, api_hash, phone), timeout=60)
        except UserbotError:
            raise
        except Exception as e:
            raise UserbotError(describe_error(e)) from None
        fid = secrets.token_urlsafe(16)
        with self._lock:
            self._login_flows[fid] = {'client': client, 'phone': phone, 'hash': pch,
                                      'api_id': int(api_id), 'api_hash': api_hash, 'created': time.monotonic()}
        return fid

    async def _a_finish(self, flow: dict) -> dict:
        client = flow['client']
        me = await client.get_me()
        session_str = client.session.save()
        await self._safe_disconnect(client)
        return {'session': session_str, 'id': me.id, 'username': getattr(me, 'username', None),
                'name': ' '.join(x for x in (me.first_name, getattr(me, 'last_name', None)) if x)}

    async def _a_sign_in_code(self, flow: dict, code: str):
        from telethon.errors import SessionPasswordNeededError
        try:
            await flow['client'].sign_in(phone=flow['phone'], code=code.strip(), phone_code_hash=flow['hash'])
        except SessionPasswordNeededError:
            return None
        return await self._a_finish(flow)

    async def _a_sign_in_password(self, flow: dict, password: str):
        await flow['client'].sign_in(password=password)
        return await self._a_finish(flow)

    def _flow(self, fid: str) -> dict:
        with self._lock:
            flow = self._login_flows.get(fid)
        if not flow:
            raise UserbotError('登录流程已过期，请重新发送验证码。')
        return flow

    def login_verify_code(self, fid: str, code: str) -> Optional[dict]:
        """Returns account info dict on success, or None when a 2FA password is required."""
        flow = self._flow(fid)
        try:
            info = self.run(self._a_sign_in_code(flow, code), timeout=60)
        except Exception as e:
            raise UserbotError(describe_error(e)) from None
        if info:
            self._complete_login(fid, flow, info)
        return info

    def login_verify_password(self, fid: str, password: str) -> dict:
        flow = self._flow(fid)
        try:
            info = self.run(self._a_sign_in_password(flow, password), timeout=60)
        except Exception as e:
            raise UserbotError(describe_error(e)) from None
        self._complete_login(fid, flow, info)
        return info

    def _complete_login(self, fid: str, flow: dict, info: dict):
        with self._lock:
            self._login_flows.pop(fid, None)
        from app import db
        from app.models import UserbotAccount
        with self.flask_app.app_context():
            acct = UserbotAccount.query.first() or UserbotAccount()
            acct.api_id = flow['api_id']
            acct.api_hash_enc = encrypt_secret(flow['api_hash'])
            acct.session_enc = encrypt_secret(info['session'])
            acct.phone_masked = mask_phone(flow['phone'])
            acct.tg_user_id = info['id']
            acct.username = info.get('username')
            acct.display_name = info.get('name')
            acct.status = 'active'
            acct.last_error = None
            db.session.add(acct)
            db.session.commit()
        print(f"[userbot] login ok user_id={info['id']}", flush=True)
        self.restart()

    # -- runtime -----------------------------------------------------------
    def _set_account_status(self, status: str, error: Optional[str] = None):
        self.state = status
        self.last_error = error
        if not self.flask_app:
            return
        try:
            from app import db
            from app.models import UserbotAccount
            with self.flask_app.app_context():
                acct = UserbotAccount.query.first()
                if acct:
                    acct.status = status if status in ('active', 'invalid') else acct.status
                    acct.last_error = error
                    db.session.commit()
        except Exception as e:
            print(f"[userbot] status write failed: {type(e).__name__}", flush=True)

    def _load_credentials(self):
        from app.models import UserbotAccount
        with self.flask_app.app_context():
            acct = UserbotAccount.query.first()
            if not acct or not acct.session_enc or not acct.api_id:
                return None
            return acct.api_id, decrypt_secret(acct.api_hash_enc), decrypt_secret(acct.session_enc)

    async def _runtime(self):
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession
        backoff = 5
        while not self._stopping:
            creds = self._load_credentials()
            if not creds:
                self.state = 'stopped'
                return
            api_id, api_hash, session_str = creds
            if not api_hash or not session_str:
                self._set_account_status('invalid', '无法解密已保存的会话（SECRET_KEY 可能变了），请重新登录小号。')
                return
            self.state = 'starting'
            try:
                session = StringSession(session_str)
            except Exception:
                self._set_account_status('invalid', '已保存的会话格式无效，请重新登录小号。')
                return
            client = TelegramClient(session, int(api_id), api_hash, receive_updates=True,
                                    catch_up=False, flood_sleep_threshold=120, **DEVICE)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    self._set_account_status('invalid', '会话已失效，请重新登录小号。')
                    return
                me = await client.get_me()
                client.add_event_handler(self._on_new_message, events.NewMessage(incoming=True))
                self.client = client
                self._set_account_status('active', None)
                print(f"[userbot] listening as user_id={me.id}", flush=True)
                backoff = 5
                await client.run_until_disconnected()
                if self._stopping:
                    return
                print("[userbot] disconnected, reconnecting…", flush=True)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                name = type(e).__name__
                if name in _INVALIDATING:
                    self._set_account_status('invalid', describe_error(e))
                    print(f"[userbot] session invalid: {name}", flush=True)
                    return
                wait = int(getattr(e, 'seconds', 0) or backoff)
                self.state = 'error'
                self.last_error = describe_error(e)
                print(f"[userbot] runtime error {name}; retry in {wait}s", flush=True)
                await asyncio.sleep(min(wait, 3600))
                backoff = min(backoff * 2, 300)
            finally:
                self.client = None
                await self._safe_disconnect(client)

    def start(self, flask_app=None, delay: float = 0.0):
        if flask_app is not None:
            self.flask_app = flask_app
        if not self.flask_app:
            return
        loop = self.ensure_loop()
        self._stopping = False

        async def _delayed():
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._runtime()
            except asyncio.CancelledError:
                pass
            except Exception as e:  # never let the userbot crash the process
                self.state = 'error'
                self.last_error = describe_error(e)
                print(f"[userbot] runtime crashed: {type(e).__name__}", flush=True)

        def _spawn():
            self.runtime_task = loop.create_task(_delayed())
        loop.call_soon_threadsafe(_spawn)

    async def _a_stop(self):
        self._stopping = True
        client = self.client
        if client is not None:
            await self._safe_disconnect(client)
        task = self.runtime_task
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), 5)
            except BaseException:
                pass
        self.runtime_task = None
        self.state = 'stopped'

    def stop(self, timeout: float = 8.0):
        if not self.loop:
            return
        try:
            self.run(self._a_stop(), timeout=timeout)
        except Exception:
            pass

    def restart(self):
        self.stop()
        self.start()

    async def _a_logout(self):
        await self._a_stop()
        creds = self._load_credentials()
        if not creds or not creds[1] or not creds[2]:
            return
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(creds[2]), int(creds[0]), creds[1], receive_updates=False, **DEVICE)
        try:
            await client.connect()
            if await client.is_user_authorized():
                await client.log_out()  # terminates this session on Telegram's side
        except Exception as e:
            print(f"[userbot] log_out failed: {type(e).__name__}", flush=True)
        finally:
            await self._safe_disconnect(client)

    def logout(self, forget_api: bool = False):
        try:
            self.run(self._a_logout(), timeout=40)
        except Exception as e:
            print(f"[userbot] logout error: {type(e).__name__}", flush=True)
        from app import db
        from app.models import UserbotAccount
        with self.flask_app.app_context():
            acct = UserbotAccount.query.first()
            if acct:
                if forget_api:
                    db.session.delete(acct)
                else:
                    acct.session_enc = None
                    acct.tg_user_id = None
                    acct.username = None
                    acct.display_name = None
                    acct.status = 'none'
                    acct.last_error = None
                db.session.commit()
        self.state = 'stopped'
        self.last_error = None

    async def _a_dialogs(self):
        client = self.client
        if client is None:
            raise UserbotError('小号未在线，无法读取群组列表。')
        out = []
        async for d in client.iter_dialogs(limit=400):
            if d.is_group or d.is_channel:
                ent = d.entity
                out.append({
                    'id': str(d.id),
                    'title': d.name or '',
                    'type': 'channel' if (d.is_channel and not d.is_group) else 'group',
                    'username': getattr(ent, 'username', None),
                })
        return out

    def list_dialogs(self) -> list:
        try:
            return self.run(self._a_dialogs(), timeout=60)
        except UserbotError:
            raise
        except Exception as e:
            raise UserbotError(describe_error(e)) from None

    # -- message intake ----------------------------------------------------
    def invalidate_rules(self):
        self._rules_cache = (0.0, [], frozenset(), frozenset())

    def _own_bot_ids(self) -> set:
        ids = set()
        tok = os.getenv('TG_BOT_TOKEN') or os.getenv('BOT_TOKEN') or ''
        if ':' in tok and tok.split(':', 1)[0].isdigit():
            ids.add(int(tok.split(':', 1)[0]))
        try:
            from app.models import BotClone
            for c in BotClone.query.all():
                t = c.get_bot_token() or ''
                if ':' in t and t.split(':', 1)[0].isdigit():
                    ids.add(int(t.split(':', 1)[0]))
        except Exception:
            pass
        return ids

    def get_rules(self):
        ts, rules, own, synced = self._rules_cache
        if time.monotonic() - ts < RULES_TTL:
            return rules, own, synced
        from app.models import BotGroup, SyncGroupMessages, UserbotSyncRule
        with self.flask_app.app_context():
            groups = {g.chat_id: g.id for g in BotGroup.query.filter(BotGroup.clone_id.is_(None)).all()}
            rules = []
            for r in UserbotSyncRule.query.filter_by(enabled=True).all():
                rules.append({
                    'id': r.id, 'enabled': True, 'source_chat_id': r.source_chat_id,
                    'sender_mode': r.sender_mode, 'sender_filter': r.sender_filter,
                    'target_chat_ids': r.target_chat_ids, 'include_sender_prefix': r.include_sender_prefix,
                    'sender_prefix_style': r.sender_prefix_style, 'sync_media': r.sync_media,
                    'filter_keywords': r.filter_keywords, 'source_group_id': groups.get(str(r.source_chat_id)),
                })
            gid_to_chat = {v: k for k, v in groups.items()}
            synced = frozenset(
                (gid_to_chat[s.source_group_id], str(s.target_group_id))
                for s in SyncGroupMessages.query.filter_by(enabled=True).all()
                if s.source_group_id in gid_to_chat
            )
            own = frozenset(self._own_bot_ids())
        summary = '; '.join(
            f"rule#{r['id']} src={r['source_chat_id']} mode={r['sender_mode'] or 'bots'} "
            f"prefix={'on' if r['include_sender_prefix'] else 'off'}/{r['sender_prefix_style'] or 'newline'}"
            for r in rules)
        if summary != getattr(self, '_last_rules_summary', None):
            self._last_rules_summary = summary
            print(f"[userbot] rules: {summary or 'none'}", flush=True)
        self._rules_cache = (time.monotonic(), rules, own, synced)
        return rules, own, synced

    def _bot_targets(self):
        from app.modules.core import routes
        app = routes.global_ptb_app
        return (app.bot if app else None), routes.global_bot_loop, (routes.global_flask_app or self.flask_app)

    async def _on_new_message(self, event):
        try:
            msg = event.message
            if getattr(msg, 'out', False) or not (event.is_group or event.is_channel):
                return
            chat_id = event.chat_id
            rules, own, synced = self.get_rules()
            if not any(str(r['source_chat_id']) == str(chat_id) for r in rules):
                return
            if not self._seen.check_and_add((chat_id, msg.id)):
                return
            sender = await event.get_sender()
            chat = await event.get_chat()
            item = to_ub_message(msg, chat_id, sender, getattr(chat, 'title', '') or '')
            from app.services.userbot_sync import plans_for_item, process_item
            plans = plans_for_item(rules, item, own_bot_ids=own, main_synced_pairs=synced)
            if not plans:
                return
            self._msg_cache[(chat_id, msg.id)] = msg
            while len(self._msg_cache) > 500:
                self._msg_cache.popitem(last=False)
            bot, bot_loop, flask_app = self._bot_targets()
            if bot is None or bot_loop is None:
                print("[userbot] main bot not ready; message dropped", flush=True)
                return
            asyncio.run_coroutine_threadsafe(
                process_item(bot, flask_app, item, plans, self.download_for_bot), bot_loop)
            self.stats['relayed'] += 1
            self.stats['last_relay_at'] = time.time()
        except Exception as e:
            print(f"[userbot] intake error: {type(e).__name__}: {str(e)[:200]}", flush=True)

    async def _a_download(self, chat_id: int, message_id: int):
        from app.services.userbot_sync import MAX_UPLOAD_BYTES
        client = self.client
        if client is None:
            return None
        msg = self._msg_cache.get((chat_id, message_id))
        if msg is None:
            msg = await client.get_messages(chat_id, ids=message_id)
        if msg is None or not getattr(msg, 'media', None):
            return None
        f = getattr(msg, 'file', None)
        if f is not None and (getattr(f, 'size', 0) or 0) > MAX_UPLOAD_BYTES:
            print(f"[userbot] media too large to re-upload chat={chat_id} msg={message_id}", flush=True)
            return None
        data = await client.download_media(msg, file=bytes)
        if not data:
            return None
        name = getattr(f, 'name', None) or f"{media_kind_of(msg) or 'file'}{getattr(f, 'ext', '') or ''}"
        return data, name

    async def download_for_bot(self, chat_id: int, message_id: int):
        """Awaitable from the BOT loop; runs the download on the userbot loop."""
        if not self.loop:
            return None
        fut = asyncio.run_coroutine_threadsafe(self._a_download(chat_id, message_id), self.loop)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(fut), 180)
        except Exception as e:
            print(f"[userbot] download failed: {type(e).__name__}", flush=True)
            return None

    # -- status ------------------------------------------------------------
    def status(self) -> dict:
        from app.models import UserbotAccount
        acct = UserbotAccount.query.first()
        return {
            'has_api': bool(acct and acct.api_id and acct.api_hash_enc),
            'api_id': acct.api_id if acct else None,
            'logged_in': bool(acct and acct.session_enc),
            'account_status': acct.status if acct else 'none',
            'runtime_state': self.state,
            'last_error': self.last_error or (acct.last_error if acct else None),
            'user_id': acct.tg_user_id if acct else None,
            'username': acct.username if acct else None,
            'display_name': acct.display_name if acct else None,
            'phone_masked': acct.phone_masked if acct else None,
            'relayed': self.stats['relayed'],
        }


userbot_manager = UserbotManager()


def start_userbot_from_env(flask_app):
    """Called from run.py. Safe when nothing is saved (no-op) or Telethon is missing."""
    if os.getenv('USERBOT_ENABLED', '1').lower() in ('0', 'false', 'no', 'off'):
        print("[userbot] disabled by USERBOT_ENABLED", flush=True)
        return
    try:
        from app import is_main_instance
        if not is_main_instance():
            return
        from app.models import UserbotAccount
        with flask_app.app_context():
            acct = UserbotAccount.query.first()
            has_session = bool(acct and acct.session_enc)
        userbot_manager.flask_app = flask_app
        if not has_session:
            print("[userbot] no saved session — not started", flush=True)
            return
        # Delay so a previous container (rolling deploy) has stopped: the same session used
        # from two IPs at once gets AUTH_KEY_DUPLICATED and is revoked by Telegram.
        delay = float(os.getenv('USERBOT_START_DELAY_SECONDS', '20'))
        print(f"[userbot] saved session found — starting in {delay:.0f}s", flush=True)
        userbot_manager.start(flask_app, delay=delay)
    except Exception as e:
        print(f"[userbot] start skipped: {type(e).__name__}: {str(e)[:200]}", flush=True)
