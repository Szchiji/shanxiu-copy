"""Telegram bot tokens must never reach logs / stdout."""

import io
import logging
import sys

import pytest

from app.log_redaction import (
    BARE_REDACTED,
    REDACTED,
    RedactingStream,
    TokenRedactingFilter,
    install_log_redaction,
    redact_secrets,
)

# Synthetic token-shaped string (not a real credential)
FAKE = "1234567890:AAFakeFakeFakeFakeFakeFakeFakeFake_-12"
URL = f"https://api.telegram.org/bot{FAKE}/getMe"


class TestRedactSecrets:
    def test_url_token_redacted(self):
        out = redact_secrets(f'HTTP Request: POST {URL} "HTTP/1.1 200 OK"')
        assert FAKE not in out
        assert f"https://api.telegram.org/{REDACTED}/getMe" in out

    def test_requests_style_error(self):
        msg = f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded with url: /bot{FAKE}/getChat?chat_id=1"
        out = redact_secrets(msg)
        assert FAKE not in out and "/bot<redacted>/getChat" in out

    def test_bare_token_redacted(self):
        out = redact_secrets(f"token={FAKE} end")
        assert FAKE not in out and BARE_REDACTED in out

    def test_non_token_text_untouched(self):
        for s in ("bot started", "chat -1001234567890", "robot 12:34", "", None, 42):
            assert redact_secrets(s) == s


class TestFilter:
    def _record(self, msg, args=(), exc_info=None):
        return logging.LogRecord("x", logging.INFO, __file__, 1, msg, args, exc_info)

    def test_message_and_args(self):
        r = self._record("HTTP Request: %s %s", ("POST", URL))
        assert TokenRedactingFilter().filter(r) is True
        assert FAKE not in r.getMessage()
        assert REDACTED in r.getMessage()

    def test_exception_text(self):
        try:
            raise RuntimeError(f"boom {URL}")
        except RuntimeError:
            r = self._record("failed", exc_info=sys.exc_info())
        TokenRedactingFilter().filter(r)
        assert FAKE not in (r.exc_text or "")
        formatted = logging.Formatter().format(r)
        assert FAKE not in formatted and "boom" in formatted

    def test_handler_output_scrubbed(self):
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.addFilter(TokenRedactingFilter())
        lg = logging.getLogger("test.redaction.handler")
        lg.propagate = False
        lg.addHandler(h)
        try:
            lg.warning("telegram error at %s", URL)
        finally:
            lg.removeHandler(h)
        assert FAKE not in buf.getvalue() and REDACTED in buf.getvalue()


class TestRedactingStream:
    def test_print_scrubbed(self):
        buf = io.StringIO()
        s = RedactingStream(buf)
        print(f"❌ 请求异常: {URL}", file=s)
        assert FAKE not in buf.getvalue() and REDACTED in buf.getvalue()


class TestInstall:
    def test_levels_and_filters(self):
        root = logging.getLogger()
        h = logging.StreamHandler(io.StringIO())
        root.addHandler(h)
        try:
            logging.getLogger("httpx").setLevel(logging.INFO)
            install_log_redaction(wrap_std_streams=False)
            install_log_redaction(wrap_std_streams=False)  # idempotent
            assert logging.getLogger("httpx").level == logging.WARNING
            assert logging.getLogger("httpcore").level == logging.WARNING
            assert sum(isinstance(f, TokenRedactingFilter) for f in h.filters) == 1
            # httpx INFO request lines are dropped entirely
            assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
        finally:
            root.removeHandler(h)

    def test_create_app_installs(self, flask_app):
        assert logging.getLogger("httpx").level == logging.WARNING

    def test_entrypoints_wired(self):
        from pathlib import Path
        assert "install_log_redaction()" in Path("run.py").read_text()
        assert "install_log_redaction" in Path("app/__init__.py").read_text()
        assert "install_log_redaction" in Path("app/bot_clone_manager.py").read_text()
        routes = Path("app/modules/core/routes.py").read_text()
        run_bot = routes.split("async def run_bot")[1][:600]
        assert "install_log_redaction" in run_bot
