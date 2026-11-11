"""Encrypt userbot secrets (StringSession, api_hash) at rest.

Key = SHA-256(SECRET_KEY + purpose salt) → Fernet. Unlike bot-token decryption there is
NO plaintext fallback: an undecryptable value returns ``None`` (e.g. SECRET_KEY changed),
and the caller must treat the session as invalid.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

_SALT = b':shanxiu_userbot_secret_v1'


def _fernet(secret_key: Optional[str] = None) -> Fernet:
    secret = (secret_key if secret_key is not None else os.getenv('SECRET_KEY', 'default_secret_key')).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret + _SALT).digest()))


def encrypt_secret(value: Optional[str], secret_key: Optional[str] = None) -> Optional[str]:
    if not value:
        return None
    return _fernet(secret_key).encrypt(value.encode()).decode()


def decrypt_secret(value: Optional[str], secret_key: Optional[str] = None) -> Optional[str]:
    if not value:
        return None
    try:
        return _fernet(secret_key).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None


def mask_phone(phone: Optional[str]) -> str:
    """'+8613812345678' → '+86*******5678' (only for display; never log the full number)."""
    digits = ''.join(ch for ch in (phone or '') if ch.isdigit())
    if len(digits) <= 4:
        return '*' * len(digits)
    head = digits[:2]
    return f"+{head}{'*' * (len(digits) - 6)}{digits[-4:]}" if len(digits) > 6 else f"+{'*' * (len(digits) - 4)}{digits[-4:]}"


def mask_secret(value: Optional[str], keep: int = 4) -> str:
    if not value:
        return ''
    return f"{value[:keep]}…{'*' * 6}"


def normalize_phone(phone: Optional[str]) -> str:
    """Keep digits; prepend '+'. Raises ValueError for obviously invalid input."""
    raw = (phone or '').strip()
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if len(digits) < 7 or len(digits) > 15:
        raise ValueError('手机号格式不正确，请带国家区号，例如 +8613812345678')
    return '+' + digits
