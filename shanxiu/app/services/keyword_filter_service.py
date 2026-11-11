"""
app/services/keyword_filter_service.py
--------------------------------------
Pure helpers for keyword blacklist / whitelist evaluation.

Order of enforcement (documented choice):
1. If any active whitelist rules exist, the message must match at least one
   whitelist rule (or the caller must exempt the sender). Non-matching
   messages are filtered using the first whitelist rule's ``action``.
2. Blacklist rules are then applied: any matching blacklist keyword triggers
   that rule's action.

When no whitelist rules are configured, behavior matches the historical
blacklist-only path.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional, Sequence


def text_matches_rule(text: str, keyword: str, match_type: str) -> bool:
    """Return True if *text* matches *keyword* under *match_type*."""
    if text is None or keyword is None:
        return False
    mt = (match_type or 'contains').lower()
    if mt == 'exact':
        return text.strip().lower() == keyword.lower()
    if mt == 'contains':
        return keyword.lower() in text.lower()
    if mt == 'regex':
        try:
            return re.search(keyword, text, re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def _rule_type(rule: Any) -> str:
    if isinstance(rule, Mapping):
        return (rule.get('filter_type') or 'blacklist').lower()
    return (getattr(rule, 'filter_type', None) or 'blacklist').lower()


def _rule_keyword(rule: Any) -> str:
    if isinstance(rule, Mapping):
        return rule.get('keyword') or ''
    return getattr(rule, 'keyword', None) or ''


def _rule_match_type(rule: Any) -> str:
    if isinstance(rule, Mapping):
        return rule.get('match_type') or 'contains'
    return getattr(rule, 'match_type', None) or 'contains'


def select_filter_to_enforce(
    text: str,
    rules: Sequence[Any],
) -> Optional[Any]:
    """Pick the keyword filter rule that should be enforced for *text*.

    Returns the rule object/dict to enforce, or ``None`` if the message
    should pass. See module docstring for whitelist-then-blacklist order.
    """
    if not rules:
        return None

    whitelist = [r for r in rules if _rule_type(r) == 'whitelist']
    blacklist = [r for r in rules if _rule_type(r) == 'blacklist']

    if whitelist:
        matched_whitelist = False
        for rule in whitelist:
            if text_matches_rule(text, _rule_keyword(rule), _rule_match_type(rule)):
                matched_whitelist = True
                break
        if not matched_whitelist:
            # Gate failed: enforce using the first whitelist rule's action.
            return whitelist[0]

    for rule in blacklist:
        if text_matches_rule(text, _rule_keyword(rule), _rule_match_type(rule)):
            return rule

    return None
