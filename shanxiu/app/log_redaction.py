"""Keep Telegram bot tokens out of logs / stdout.

Telegram Bot API URLs embed the token (``https://api.telegram.org/bot<id>:<secret>/...``).
``httpx`` logs every request URL at INFO, and ``requests``/``httpx`` exception messages
often include the URL too. This module:

* lowers noisy HTTP client loggers (``httpx``, ``httpcore``) to WARNING;
* attaches :class:`TokenRedactingFilter` to every logging handler so any record
  (message, args, exception traceback, stack info) is scrubbed before output;
* wraps ``sys.stdout`` / ``sys.stderr`` so ``print()`` and uncaught tracebacks are
  scrubbed as well.

``install_log_redaction()`` is idempotent and safe to call from every entrypoint.
"""

from __future__ import annotations

import io
import logging
import re
import sys
from typing import Any

# bot<numeric id>:<secret>. Real secrets are 35 chars; require >=30 to avoid false hits.
_TOKEN_RE = re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{30,}")
# Bare tokens (e.g. printed without the "bot" prefix): <id>:<secret>
_BARE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])\d{6,}:AA[A-Za-z0-9_-]{30,}")

# Telethon StringSession: "1" + urlsafe-base64(dc, ip, port, 256-byte auth key) ≈ 353 chars.
_SESSION_RE = re.compile(r"(?<![A-Za-z0-9_\-])1[A-Za-z0-9_\-]{300,}={0,2}")
SESSION_REDACTED = "<redacted-session>"

REDACTED = "bot<redacted>"
BARE_REDACTED = "<redacted-token>"

NOISY_HTTP_LOGGERS = ("httpx", "httpcore", "telethon")


def redact_secrets(value: Any) -> Any:
    """Return *value* with any Telegram bot token replaced. Non-str values pass through."""
    if not isinstance(value, str) or not value:
        return value
    if len(value) >= 300:
        value = _SESSION_RE.sub(SESSION_REDACTED, value)
    if "bot" not in value and ":AA" not in value:
        return value
    value = _TOKEN_RE.sub(REDACTED, value)
    return _BARE_TOKEN_RE.sub(BARE_REDACTED, value)


class TokenRedactingFilter(logging.Filter):
    """Scrub tokens from a LogRecord (message+args, exception text, stack info)."""

    _formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        redacted = redact_secrets(message)
        if redacted != message or record.args:
            record.msg = redacted
            record.args = None
        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = self._formatter.formatException(record.exc_info)
            except Exception:
                record.exc_text = None
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_secrets(record.stack_info)
        return True


class RedactingStream(io.TextIOBase):
    """Text stream proxy that scrubs tokens from everything written."""

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def write(self, s):  # type: ignore[override]
        self._wrapped.write(redact_secrets(s))
        return len(s) if isinstance(s, str) else 0

    def flush(self):
        try:
            self._wrapped.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return self._wrapped.isatty()
        except Exception:
            return False

    def fileno(self):
        return self._wrapped.fileno()

    @property
    def encoding(self):  # type: ignore[override]
        return getattr(self._wrapped, "encoding", "utf-8")

    @property
    def wrapped(self):
        return self._wrapped

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


_FILTER = TokenRedactingFilter()


def _attach_filter(handler: logging.Handler) -> None:
    if not any(isinstance(f, TokenRedactingFilter) for f in handler.filters):
        handler.addFilter(_FILTER)
    stream = getattr(handler, "stream", None)
    if stream is not None and not isinstance(stream, RedactingStream) and stream in (
        sys.__stdout__, sys.__stderr__
    ):
        try:
            handler.setStream(RedactingStream(stream))
        except Exception:
            pass


def install_log_redaction(wrap_std_streams: bool = True) -> None:
    """Apply logger levels + redaction filter (+ stdout/stderr wrapping). Idempotent."""
    for name in NOISY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    root = logging.getLogger()
    for handler in list(root.handlers):
        _attach_filter(handler)
    # Handlers attached directly to named loggers (werkzeug, PTB, our modules, ...)
    for logger in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger):
            for handler in list(logger.handlers):
                _attach_filter(handler)

    if wrap_std_streams:
        if not isinstance(sys.stdout, RedactingStream) and sys.stdout is not None:
            sys.stdout = RedactingStream(sys.stdout)
        if not isinstance(sys.stderr, RedactingStream) and sys.stderr is not None:
            sys.stderr = RedactingStream(sys.stderr)
