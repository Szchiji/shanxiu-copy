"""
tests/unit/test_keyword_filter_service.py
-----------------------------------------
Unit tests for whitelist / blacklist keyword filter evaluation.
"""

import pytest
from app.services.keyword_filter_service import (
    text_matches_rule,
    select_filter_to_enforce,
)


class TestTextMatchesRule:
    def test_contains(self):
        assert text_matches_rule('hello world', 'WORLD', 'contains')
        assert not text_matches_rule('hello', 'world', 'contains')

    def test_exact(self):
        assert text_matches_rule('  Hello  ', 'hello', 'exact')
        assert not text_matches_rule('hello world', 'hello', 'exact')

    def test_regex(self):
        assert text_matches_rule('abc123', r'\d+', 'regex')
        assert not text_matches_rule('abcdef', r'\d+', 'regex')

    def test_invalid_regex(self):
        assert not text_matches_rule('abc', '[', 'regex')


class TestSelectFilterToEnforce:
    def test_no_rules(self):
        assert select_filter_to_enforce('hello', []) is None

    def test_blacklist_only_match(self):
        rules = [
            {'keyword': 'spam', 'filter_type': 'blacklist', 'match_type': 'contains', 'action': 'delete'},
        ]
        hit = select_filter_to_enforce('buy spam now', rules)
        assert hit is rules[0]

    def test_blacklist_only_no_match(self):
        rules = [
            {'keyword': 'spam', 'filter_type': 'blacklist', 'match_type': 'contains', 'action': 'delete'},
        ]
        assert select_filter_to_enforce('hello', rules) is None

    def test_whitelist_gate_blocks_non_match(self):
        rules = [
            {'keyword': 'ok', 'filter_type': 'whitelist', 'match_type': 'contains', 'action': 'delete'},
        ]
        hit = select_filter_to_enforce('not allowed', rules)
        assert hit is rules[0]

    def test_whitelist_gate_allows_match(self):
        rules = [
            {'keyword': 'ok', 'filter_type': 'whitelist', 'match_type': 'contains', 'action': 'delete'},
        ]
        assert select_filter_to_enforce('this is ok', rules) is None

    def test_whitelist_then_blacklist(self):
        """Whitelist must match first; matching blacklist still applies."""
        rules = [
            {'keyword': 'ok', 'filter_type': 'whitelist', 'match_type': 'contains', 'action': 'warn'},
            {'keyword': 'bad', 'filter_type': 'blacklist', 'match_type': 'contains', 'action': 'mute'},
        ]
        # Passes whitelist, hits blacklist
        hit = select_filter_to_enforce('ok but bad', rules)
        assert hit is rules[1]
        # Fails whitelist gate (uses first whitelist action)
        hit2 = select_filter_to_enforce('only bad', rules)
        assert hit2 is rules[0]

    def test_object_style_rules(self):
        class Rule:
            def __init__(self, keyword, filter_type, match_type='contains', action='delete'):
                self.keyword = keyword
                self.filter_type = filter_type
                self.match_type = match_type
                self.action = action

        wl = Rule('pass', 'whitelist')
        assert select_filter_to_enforce('nope', [wl]) is wl
        assert select_filter_to_enforce('please pass', [wl]) is None
