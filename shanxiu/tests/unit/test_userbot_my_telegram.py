"""my.telegram.org api_id/api_hash flow: HTML parsing (fixtures) + error handling."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.userbot import my_telegram as mt

FIX = Path(__file__).parent / 'fixtures'


def _fx(name):
    return (FIX / name).read_text(encoding='utf-8')


def test_parse_existing_app():
    page = mt.parse_apps_page(_fx('my_telegram_apps_existing.html'))
    assert page.api_id == 1234567
    assert page.api_hash == '0123456789abcdef0123456789abcdef'


def test_parse_create_form():
    page = mt.parse_apps_page(_fx('my_telegram_apps_create.html'))
    assert page.api_id is None and page.api_hash is None
    assert page.create_hash == 'a1b2c3d4e5f60718'


def test_parse_send_password():
    assert mt.parse_send_password_response('{"random_hash":"abc123"}') == 'abc123'
    with pytest.raises(mt.MyTelegramError, match='尝试次数过多'):
        mt.parse_send_password_response('Sorry, too many tries. Please try again later.')
    with pytest.raises(mt.MyTelegramError, match='手动'):
        mt.parse_send_password_response('<html>changed</html>')


def test_parse_login():
    mt.parse_login_response('true')
    with pytest.raises(mt.MyTelegramError, match='验证码错误'):
        mt.parse_login_response('Invalid confirmation code!')


def test_generate_app_fields_valid():
    import re
    for attempt in range(3):
        f = mt.generate_app_fields(attempt)
        assert re.fullmatch(r'[a-z][a-z0-9]{4,31}', f['app_shortname'])
        assert re.fullmatch(r'[A-Za-z0-9]{5,64}', f['app_title'])
        assert f['app_url'] == '' and f['app_desc'] == ''
        assert f['app_platform'] in ('android', 'ios', 'wp', 'bb', 'desktop', 'web', 'ubp', 'other')
    assert mt.generate_app_fields(0)['app_platform'] == 'desktop'
    assert mt.generate_app_fields(1)['app_platform'] == 'web'
    assert mt.generate_app_fields(0)['app_shortname'] != mt.generate_app_fields(0)['app_shortname']


class _FakeHttp:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.headers = {}

    def _next(self, method, url, data=None, headers=None):
        self.calls.append((method, url.replace(mt.BASE_URL, ''), dict(data or {}), dict(headers or {})))
        return SimpleNamespace(text=self.script.pop(0), status_code=200)

    def post(self, url, data=None, timeout=None, headers=None):
        return self._next('POST', url, data, headers)

    def get(self, url, timeout=None, headers=None):
        return self._next('GET', url, None, headers)


def _flow(script):
    http = _FakeHttp(script)
    flow = mt.MyTelegramFlow('+1', http=http)
    flow.retry_pause = 0
    return flow, http


def test_full_flow_creates_app_when_missing():
    http = _FakeHttp([
        '{"random_hash":"rh1"}', 'true',
        _fx('my_telegram_apps_create.html'), '',
        _fx('my_telegram_apps_existing.html'),
    ])
    flow = mt.MyTelegramFlow('+8613800000000', http=http)
    flow.send_password()
    flow.login('ABCdef')
    assert flow.fetch_or_create_app() == (1234567, '0123456789abcdef0123456789abcdef')
    create = [c for c in http.calls if c[1] == '/apps/create'][0]
    assert create[2]['hash'] == 'a1b2c3d4e5f60718'
    assert http.calls[1][2] == {'phone': '+8613800000000', 'random_hash': 'rh1', 'password': 'ABCdef'}


def test_existing_app_skips_create():
    http = _FakeHttp([_fx('my_telegram_apps_existing.html')])
    flow = mt.MyTelegramFlow('+1', http=http)
    assert flow.fetch_or_create_app()[0] == 1234567
    assert all(c[1] != '/apps/create' for c in http.calls)


def test_create_request_mimics_web_form():
    flow, http = _flow([_fx('my_telegram_apps_create.html'), '', _fx('my_telegram_apps_existing.html')])
    flow.fetch_or_create_app()
    method, path, data, headers = [c for c in http.calls if c[1] == '/apps/create'][0]
    assert headers['X-Requested-With'] == 'XMLHttpRequest'
    assert headers['Referer'] == 'https://my.telegram.org/apps'
    assert headers['Content-Type'].startswith('application/x-www-form-urlencoded')
    assert set(data) == {'hash', 'app_title', 'app_shortname', 'app_url', 'app_platform', 'app_desc'}
    assert http.headers['Origin'] == 'https://my.telegram.org' and 'Mozilla' in http.headers['User-Agent']


def test_error_but_app_actually_created():
    flow, http = _flow([_fx('my_telegram_apps_create.html'), 'ERROR', _fx('my_telegram_apps_existing.html')])
    assert flow.fetch_or_create_app() == (1234567, '0123456789abcdef0123456789abcdef')


def test_error_retries_with_fresh_hash_new_name_and_web_platform():
    second_form = _fx('my_telegram_apps_create.html').replace('a1b2c3d4e5f60718', 'ffff0000eeee1111')
    flow, http = _flow([_fx('my_telegram_apps_create.html'), 'ERROR', second_form, '',
                        _fx('my_telegram_apps_existing.html')])
    assert flow.fetch_or_create_app()[0] == 1234567
    creates = [c for c in http.calls if c[1] == '/apps/create']
    assert len(creates) == 2
    assert creates[0][2]['hash'] == 'a1b2c3d4e5f60718' and creates[1][2]['hash'] == 'ffff0000eeee1111'
    assert creates[0][2]['app_shortname'] != creates[1][2]['app_shortname']
    assert [c[2]['app_platform'] for c in creates] == ['desktop', 'web']


def test_persistent_error_explains_ip_and_manual():
    form = _fx('my_telegram_apps_create.html')
    flow, http = _flow([form, 'ERROR', form, 'ERROR', form])
    with pytest.raises(mt.MyTelegramError) as ei:
        flow.fetch_or_create_app()
    msg = str(ei.value)
    assert 'ERROR' in msg and 'IP' in msg and '手动填写' in msg
    assert len([c for c in http.calls if c[1] == '/apps/create']) == 2


def test_specific_validation_message_surfaced():
    form = _fx('my_telegram_apps_create.html')
    flow, _ = _flow([form, 'Incorrect app name!', form])
    with pytest.raises(mt.MyTelegramError, match='Incorrect app name'):
        flow.fetch_or_create_app()


def test_log_snippet_masks_numbers():
    assert mt._short('call +441234567890 now') == 'call <num> now'


def test_network_error_is_friendly():
    import requests

    class Boom(_FakeHttp):
        def post(self, *a, **k):
            raise requests.ConnectionError('x')

    flow = mt.MyTelegramFlow('+1', http=Boom([]))
    with pytest.raises(mt.MyTelegramError, match='无法连接'):
        flow.send_password()
