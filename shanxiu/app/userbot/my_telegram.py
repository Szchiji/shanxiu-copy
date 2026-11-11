"""Obtain api_id / api_hash from https://my.telegram.org (official web flow).

Flow: send_password(phone) → random_hash; Telegram sends a login code to the account's
Telegram app; login(code) sets the ``stel_token`` cookie; GET /apps → parse existing app,
or POST /apps/create (using the page's hidden ``hash``) and parse again.

my.telegram.org is notoriously flaky (returns a bare ``ERROR`` for new/limited accounts,
rate limits, layout changes). Every failure raises :class:`MyTelegramError` with a zh-CN
message so the UI can offer manual entry. Phone numbers / codes are never logged.
"""

from __future__ import annotations

import html as html_module
import re
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

BASE_URL = 'https://my.telegram.org'
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/126.0 Safari/537.36'
)
MANUAL_HINT = '你也可以手动打开 https://my.telegram.org → API development tools 获取 api_id / api_hash 后在下方手动填写。'


class MyTelegramError(Exception):
    """User-facing (zh-CN) error from the my.telegram.org flow."""


# ---------------------------------------------------------------------------
# Pure parsers (unit-tested with HTML fixtures)
# ---------------------------------------------------------------------------

_HEX32 = re.compile(r'\b([0-9a-f]{32})\b')


def parse_send_password_response(text: str) -> str:
    """Return random_hash from the /auth/send_password response or raise MyTelegramError."""
    body = (text or '').strip()
    if body.startswith('{'):
        import json
        try:
            data = json.loads(body)
        except ValueError:
            data = {}
        rh = data.get('random_hash') if isinstance(data, dict) else None
        if rh:
            return str(rh)
    low = body.lower()
    if 'too many tries' in low or 'too many' in low:
        raise MyTelegramError('my.telegram.org 提示尝试次数过多，请过几个小时再试。' + MANUAL_HINT)
    if 'invalid phone' in low or 'phone_number_invalid' in low:
        raise MyTelegramError('手机号无效，请带国家区号，例如 +8613812345678。')
    raise MyTelegramError('my.telegram.org 没有返回验证码请求结果（可能被限制或页面变化）。' + MANUAL_HINT)


def parse_login_response(text: str) -> None:
    body = (text or '').strip().lower()
    if body == 'true':
        return
    if 'invalid confirmation code' in body or 'invalid' in body:
        raise MyTelegramError('验证码错误，请检查 Telegram 应用里「Telegram」官方账号发来的验证码（不是短信）。')
    if 'too many' in body:
        raise MyTelegramError('尝试次数过多，请稍后再试。' + MANUAL_HINT)
    raise MyTelegramError('登录 my.telegram.org 失败。' + MANUAL_HINT)


@dataclass
class AppsPage:
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    create_hash: Optional[str] = None  # hidden form hash when no app exists yet
    logged_out: bool = False
    raw_title: str = field(default='', repr=False)


def parse_apps_page(page_html: str) -> AppsPage:
    """Parse GET /apps. Handles both 'App configuration' (existing app) and the
    'Create new application' form."""
    text = page_html or ''
    res = AppsPage()
    m_title = re.search(r'<title>(.*?)</title>', text, re.S | re.I)
    res.raw_title = html_module.unescape(m_title.group(1).strip()) if m_title else ''

    # Existing app: <label for="app_id">App api_id:</label> ... <strong>123456</strong>
    m_id = re.search(r'for="app_id"[^>]*>.*?<strong>\s*(\d{3,12})\s*</strong>', text, re.S | re.I)
    if not m_id:
        m_id = re.search(r'api_id\s*:?\s*</label>.*?(\d{3,12})', text, re.S | re.I)
    m_hash_block = re.search(r'for="app_hash"[^>]*>(.*?)</div>\s*</div>', text, re.S | re.I)
    api_hash = None
    if m_hash_block:
        h = _HEX32.search(m_hash_block.group(1))
        api_hash = h.group(1) if h else None
    if api_hash is None:
        m2 = re.search(r'api_hash\s*:?\s*</label>.*?([0-9a-f]{32})', text, re.S | re.I)
        api_hash = m2.group(1) if m2 else None
    if m_id and api_hash:
        res.api_id = int(m_id.group(1))
        res.api_hash = api_hash
        return res

    m_form_hash = re.search(r'name="hash"\s+value="([0-9a-fA-F]+)"', text) or re.search(
        r'value="([0-9a-fA-F]+)"\s+name="hash"', text)
    if m_form_hash:
        res.create_hash = m_form_hash.group(1)
    if not res.create_hash and ('/auth' in text and 'login' in text.lower() and 'app_title' not in text):
        res.logged_out = True
    return res


_PLATFORMS = ('desktop', 'web')  # exact radio values of the web form; 'web' as the retry


def generate_app_fields(attempt: int = 0) -> dict:
    """Form fields that pass my.telegram.org's (undocumented) validation.

    * title: letters + digits only, 5–64 chars (no spaces/symbols);
    * short name: lowercase letters + digits, starts with a letter, 5–32 chars, random so it
      is unique per account (a taken/invalid short name is a classic cause of bare ``ERROR``);
    * url / description: empty (both optional in the web form).
    """
    letters = string.ascii_lowercase
    suffix = secrets.choice(letters) + ''.join(secrets.choice(letters + string.digits) for _ in range(7))
    return {
        'app_title': f'Sync{suffix.capitalize()}',
        'app_shortname': f'sync{suffix}',
        'app_url': '',
        'app_platform': _PLATFORMS[min(attempt, len(_PLATFORMS) - 1)],
        'app_desc': '',
    }


def _short(text: Optional[str], n: int = 60) -> str:
    """Safe-to-log snippet of a my.telegram.org response (they never echo phone/code)."""
    s = re.sub(r'\s+', ' ', (text or '')).strip()
    s = re.sub(r'\+?\d{7,}', '<num>', s)
    return s[:n]


IP_HINT = ('my.telegram.org 拒绝创建应用（返回 ERROR）。请求格式已按官网表单发送并重试过，'
           '多个账号都这样时通常是 Telegram 不接受来自服务器机房 IP 的创建请求'
           '（也常见于新号，或登录 IP 与手机号国家不一致）。'
           '建议用自己的手机或电脑浏览器（不开 VPN）打开 https://my.telegram.org → API development tools '
           '创建应用，再把 api_id / api_hash 填到下方「手动填写」。')


# ---------------------------------------------------------------------------
# HTTP flow (one instance per admin login attempt, kept in memory between requests)
# ---------------------------------------------------------------------------

class MyTelegramFlow:
    def __init__(self, phone: str, http: Optional[requests.Session] = None, timeout: float = 20.0):
        self.phone = phone
        self.http = http or requests.Session()
        # Mirror what the site's jQuery $.ajax calls send.
        self.http.headers.update({
            'User-Agent': USER_AGENT,
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': BASE_URL,
        })
        self.timeout = timeout
        self.random_hash: Optional[str] = None
        self.created_at = time.monotonic()
        self.retry_pause = 1.5

    @staticmethod
    def _ajax_headers(referer_path: str) -> dict:
        return {
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Referer': BASE_URL + referer_path,
        }

    def _post(self, path: str, data: dict, referer_path: str = '/auth') -> requests.Response:
        try:
            return self.http.post(BASE_URL + path, data=data, timeout=self.timeout,
                                  headers=self._ajax_headers(referer_path))
        except requests.RequestException:
            raise MyTelegramError('无法连接 my.telegram.org（网络超时或被拒绝）。' + MANUAL_HINT) from None

    def _get(self, path: str, referer_path: str = '/') -> requests.Response:
        try:
            return self.http.get(BASE_URL + path, timeout=self.timeout, headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Referer': BASE_URL + referer_path,
            })
        except requests.RequestException:
            raise MyTelegramError('无法连接 my.telegram.org（网络超时或被拒绝）。' + MANUAL_HINT) from None

    def send_password(self) -> None:
        r = self._post('/auth/send_password', {'phone': self.phone})
        print(f"[my.telegram.org] send_password status={getattr(r, 'status_code', '?')} "
              f"ok={'random_hash' in (r.text or '')}", flush=True)
        self.random_hash = parse_send_password_response(r.text)

    def login(self, code: str) -> None:
        if not self.random_hash:
            raise MyTelegramError('请先获取验证码。')
        r = self._post('/auth/login', {'phone': self.phone, 'random_hash': self.random_hash,
                                        'password': (code or '').strip()})
        print(f"[my.telegram.org] login status={getattr(r, 'status_code', '?')} body={_short(r.text, 30)!r}",
              flush=True)
        parse_login_response(r.text)

    def _apps(self) -> AppsPage:
        r = self._get('/apps')
        page = parse_apps_page(r.text)
        print(f"[my.telegram.org] GET /apps status={getattr(r, 'status_code', '?')} title={page.raw_title!r} "
              f"has_app={bool(page.api_id and page.api_hash)} has_form={bool(page.create_hash)}", flush=True)
        return page

    def fetch_or_create_app(self, max_attempts: int = 2) -> tuple[int, str]:
        page = self._apps()
        if page.api_id and page.api_hash:
            return page.api_id, page.api_hash
        if page.logged_out or not page.create_hash:
            raise MyTelegramError('已登录 my.telegram.org，但无法读取应用页面（可能页面已变化）。' + MANUAL_HINT)
        last_body = ''
        for attempt in range(max_attempts):
            fields = generate_app_fields(attempt)
            fields['hash'] = page.create_hash  # fresh hash from the latest /apps page
            r = self._post('/apps/create', fields, referer_path='/apps')
            body = (r.text or '').strip()
            last_body = body
            print(f"[my.telegram.org] POST /apps/create attempt={attempt + 1} "
                  f"status={getattr(r, 'status_code', '?')} platform={fields['app_platform']} "
                  f"body={_short(body)!r}", flush=True)
            # Always re-read /apps: the app may exist even when the response says ERROR.
            page = self._apps()
            if page.api_id and page.api_hash:
                return page.api_id, page.api_hash
            if not page.create_hash:
                break
            if body and 'error' not in body.lower():
                # A specific validation message (e.g. "Incorrect app name!") — surface it.
                raise MyTelegramError(f'my.telegram.org 返回：{_short(body, 120)}。' + MANUAL_HINT)
            time.sleep(self.retry_pause)
        if not last_body or 'error' in last_body.lower():
            raise MyTelegramError(IP_HINT)
        raise MyTelegramError('应用已提交，但没能读取到 api_id / api_hash。' + MANUAL_HINT)
