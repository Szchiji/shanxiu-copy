"""Sanitize user-supplied custom listing fields (extras) at the API boundary."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

MAX_KEYS = 24
MAX_KEY_LEN = 16
MAX_VAL_LEN = 500
_BAD_KEY = re.compile(r"[<>\"'`&\x00-\x1f\x7f]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_user_extras(extras: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Drop internal ("_") keys, keys with markup characters, nested values; cap sizes."""
    out: Dict[str, str] = {}
    if not isinstance(extras, dict):
        return out
    for k, v in extras.items():
        key = str(k or "").strip()
        if not key or key.startswith("_") or len(key) > MAX_KEY_LEN or _BAD_KEY.search(key):
            continue
        if v is None or isinstance(v, (dict, list)):
            continue
        val = _CTRL.sub("", str(v)).strip()[:MAX_VAL_LEN]
        if not val:
            continue
        out[key] = val
        if len(out) >= MAX_KEYS:
            break
    return out
