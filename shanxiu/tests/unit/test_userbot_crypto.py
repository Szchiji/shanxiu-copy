"""Userbot secret storage: encrypted at rest, no plaintext fallback, redaction."""

from app.log_redaction import redact_secrets
from app.userbot.crypto import decrypt_secret, encrypt_secret, mask_phone, normalize_phone

import pytest

FAKE_SESSION = '1' + 'BAAx' * 88 + '='


def test_roundtrip_and_not_plaintext():
    enc = encrypt_secret(FAKE_SESSION, secret_key='k1')
    assert enc and FAKE_SESSION not in enc and 'BAAx' not in enc
    assert decrypt_secret(enc, secret_key='k1') == FAKE_SESSION


def test_wrong_key_or_plaintext_returns_none():
    enc = encrypt_secret('0123456789abcdef0123456789abcdef', secret_key='k1')
    assert decrypt_secret(enc, secret_key='k2') is None
    assert decrypt_secret('plain-text-value', secret_key='k1') is None
    assert encrypt_secret('') is None and decrypt_secret(None) is None


def test_mask_and_normalize_phone():
    assert normalize_phone(' +86 138-1234-5678 ') == '+8613812345678'
    with pytest.raises(ValueError):
        normalize_phone('123')
    m = mask_phone('+8613812345678')
    assert m.endswith('5678') and '1381234' not in m


def test_session_string_redacted_in_logs():
    out = redact_secrets(f'session={FAKE_SESSION} ok')
    assert FAKE_SESSION not in out and '<redacted-session>' in out
    assert redact_secrets('message 12345 normal') == 'message 12345 normal'
