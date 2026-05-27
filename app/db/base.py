"""SQLAlchemy engine, session, and declarative base."""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import DATABASE_URL

_engine_kwargs: dict[str, Any] = {}
_connect_args: dict[str, Any] = {}

if DATABASE_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False
    if ":memory:" in DATABASE_URL:
        _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, connect_args=_connect_args, **_engine_kwargs)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create all tables. Safe to call at startup and in tests."""
    from app.db import auth_models, models, projection_models  # noqa: F401 - register tables

    Base.metadata.create_all(bind=engine)
    _ensure_projection_indexes()
    _create_postgres_indexes()


def reset_db() -> None:
    """Delete all rows from every table. Fast alternative to drop/recreate.

    Relies on init_db() having been called at session start to create the
    schema. Deletes in reverse FK order so constraints are never violated.
    """
    from app.db import auth_models, models, projection_models  # noqa: F401 - register tables

    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


def _ensure_projection_indexes() -> None:
    """Create any projection indexes missing from an already-existing table.

    ``Base.metadata.create_all`` adds the indexes declared in
    ``__table_args__`` only when it creates the table itself. If a deployment
    is upgrading from an older codebase that had the table but a smaller set
    of indexes, the new indexes would otherwise never be created. We re-issue
    ``CREATE INDEX IF NOT EXISTS`` for every index on every ``*_index`` table
    so adding a new index becomes a code-only change.
    """
    from app.db import projection_models  # noqa: F401 - register models

    with engine.begin() as conn:
        for table in Base.metadata.tables.values():
            if not table.name.endswith("_index"):
                continue
            for index in table.indexes:
                index.create(conn, checkfirst=True)


def _create_postgres_indexes() -> None:
    """Create Postgres-only indexes that SQLAlchemy's DDL can't express portably."""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text

    with engine.begin() as conn:
        # GIN index with jsonb_path_ops supports `content @> '...'::jsonb` lookups
        # in O(log n) and is more compact than the default jsonb_ops class.
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_resource_versions_content_gin "
                "ON resource_versions USING GIN (content jsonb_path_ops)"
            )
        )
