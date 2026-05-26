import atexit
import os

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

from app.db.base import init_db, reset_db
from app.hooks import hooks
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Create all tables once for the entire test session."""
    init_db()


@pytest.fixture(autouse=True)
def _clean_database():
    """Delete all rows and clear hooks before every test."""
    reset_db()
    hooks.clear()
    yield
    hooks.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
