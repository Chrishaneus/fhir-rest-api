import atexit
import os
import secrets

import fakeredis

# DATABASE_URL must be set before any app module is imported, because
# app.config reads it at import time and app.db.base creates the engine
# immediately from that value.
#
# If DATABASE_URL is already in the environment (e.g. CI pointing at a
# dedicated test database) we use it as-is. Otherwise we spin up a
# temporary Postgres container. If Docker is unavailable we fall back to
# an in-process SQLite database.
if "DATABASE_URL" not in os.environ:
    if os.getenv("USE_POSTGRES", "").lower() in ("1", "true", "yes"):
        from testcontainers.postgres import PostgresContainer

        _pg = PostgresContainer("postgres:16-alpine", driver="psycopg")
        _pg.start()
        os.environ["DATABASE_URL"] = _pg.get_connection_url()
        atexit.register(_pg.stop)
    else:
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest
from fastapi.testclient import TestClient

import app.auth.rate_limit as rate_limit
from app.db.base import init_db, reset_db
from app.hooks import hooks
from app.main import app

# Capture the hook registrations that modules set up at import time.
# Tests may add extra hooks; we restore from this snapshot around each test
# so production hooks stay active while test-added ones are cleaned up.
_BASELINE_HOOKS = hooks.snapshot()


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Create all tables once for the entire test session (SQLite in-memory)."""
    from app.db.base import Base, engine
    init_db()  # registers models into Base.metadata
    Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def _clean_database():
    """Delete all rows, restore baseline hooks, and reset rate-limit state before every test."""
    rate_limit._client = fakeredis.FakeRedis(decode_responses=True)
    reset_db()
    hooks.restore(_BASELINE_HOOKS)
    yield
    hooks.restore(_BASELINE_HOOKS)


@pytest.fixture(scope="session")
def jwt_secret() -> str:
    """A single cryptographically strong JWT secret for the whole test session."""
    return secrets.token_hex(32)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
