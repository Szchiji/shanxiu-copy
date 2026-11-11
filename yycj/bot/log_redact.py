"""Mask Telegram bot tokens in every log line (any logger, any handler, tracebacks included)."""
from __future__ import annotations

import logging
import os
import re

_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])(bot)?\d{6,12}:[A-Za-z0-9_-]{30,}")
_installed = False


def _secrets() -> list[str]:
    raw = (os.environ.get("BOT_TOKEN") or "").strip()
    return [raw] if len(raw) >= 20 else []


def redact(text: str) -> str:
    if not text:
        return text
    s = str(text)
    for secret in _secrets():
        if secret in s:
            s = s.replace(secret, "***")
    return _TOKEN_RE.sub(lambda m: "bot***" if m.group(1) else "***", s)


def install() -> None:
    """Idempotent. Redacts at record creation and again at format time."""
    global _installed
    if _installed:
        return
    _installed = True

    old_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        try:
            msg = record.getMessage()
            clean = redact(msg)
            if clean != msg:
                record.msg = clean
                record.args = None
        except Exception:
            pass
        return record

    logging.setLogRecordFactory(factory)

    orig_format = logging.Formatter.format

    def safe_format(self, record):
        return redact(orig_format(self, record))

    logging.Formatter.format = safe_format  # type: ignore[assignment]

    for name in ("httpx", "httpcore", "hpack", "urllib3", "aiohttp.client"):
        logging.getLogger(name).setLevel(logging.WARNING)
