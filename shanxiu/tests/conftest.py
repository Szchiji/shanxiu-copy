"""
tests/conftest.py
-----------------
Shared pytest fixtures for both unit and integration tests.
"""

import pytest


# ---------------------------------------------------------------------------
# Unit-test fixtures (no database, no network)
# ---------------------------------------------------------------------------

# No shared state needed at this level; individual test modules define their
# own fixtures as required.


# ---------------------------------------------------------------------------
# Integration-test fixtures (SQLite in-memory database)
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def flask_app():
    """Create a test Flask application backed by an in-memory SQLite database."""
    import os
    os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
    os.environ.setdefault('SECRET_KEY', 'test-secret-key')
    os.environ.setdefault('TG_BOT_TOKEN', 'test-token')

    from app import create_app, db as _db
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        _db.create_all()
        yield app
        _db.drop_all()


@pytest.fixture()
def app_ctx(flask_app):
    """Push an application context for the duration of a single test."""
    with flask_app.app_context():
        yield flask_app


@pytest.fixture()
def client(flask_app):
    """A Flask test client (not authenticated)."""
    return flask_app.test_client()


@pytest.fixture()
def db(app_ctx):
    """Database session that rolls back after each test."""
    from app import db as _db
    yield _db
    _db.session.rollback()
