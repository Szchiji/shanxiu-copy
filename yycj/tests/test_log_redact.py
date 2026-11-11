import io
import logging

from bot import log_redact

FAKE = "987654321:AAFakeFakeFakeFakeFakeFakeFakeFake_-12"


def _capture():
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.INFO)
    return buf, h


def test_masks_message_args_and_traceback():
    log_redact.install()
    buf, h = _capture()
    try:
        lg = logging.getLogger("some.lib")
        lg.info("GET https://api.telegram.org/bot%s/getMe", FAKE)
        lg.warning(f"plain bot{FAKE} and bare {FAKE}")
        try:
            raise RuntimeError(f"boom https://api.telegram.org/file/bot{FAKE}/x.jpg")
        except RuntimeError:
            lg.exception("failed")
        out = buf.getvalue()
    finally:
        logging.getLogger().removeHandler(h)
    assert FAKE not in out
    assert "bot***" in out
    assert "987654321:" not in out


def test_env_token_literal_masked(monkeypatch):
    secret = "42:literal-env-token-value-xyz"
    monkeypatch.setenv("BOT_TOKEN", secret)
    assert secret not in log_redact.redact(f"x {secret} y")


def test_noisy_loggers_quiet():
    log_redact.install()
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_harmless_text_untouched():
    assert log_redact.redact("user 12345 posted at 10:30") == "user 12345 posted at 10:30"
