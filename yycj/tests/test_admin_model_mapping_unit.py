"""Legacy-table compatibility: channel review references must not be mapped onto posts."""
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from bot.models import Post, Review
from bot.db import _ALTER_STATEMENTS


def test_post_select_does_not_require_review_channel_columns():
    sql = str(select(Post).compile(dialect=postgresql.dialect()))
    assert 'posts.post_chat' not in sql and 'posts.post_mid' not in sql
    assert 'post_chat' not in Post.__table__.c


def test_review_channel_columns_match_existing_additive_migrations():
    assert {'post_chat', 'post_mid'} <= set(Review.__table__.c.keys())
    review = Review()
    assert review.post_chat is None and review.post_mid is None
    for name in ('post_chat', 'post_mid'):
        assert any('ALTER TABLE reviews ADD COLUMN IF NOT EXISTS '+name in stmt for stmt in _ALTER_STATEMENTS)
    # No DROP / data rewriting required: wrongly-added columns in newer empty DBs are harmless.
    assert not any('DROP' in stmt for stmt in _ALTER_STATEMENTS)
