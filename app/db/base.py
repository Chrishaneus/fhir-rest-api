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
    """Import all ORM models so Base.metadata is fully populated.

    Schema is managed exclusively via Alembic migrations (`alembic upgrade
    head`). This function no longer calls create_all; it exists only to
    trigger the model imports so the metadata is ready before any query runs.
    """
    from app.db import auth_models, models, projection_models  # noqa: F401


def reset_db() -> None:
    """Delete all rows from every table. Fast alternative to drop/recreate.

    Relies on init_db() having been called at session start to create the
    schema. Deletes in reverse FK order so constraints are never violated.
    """
    from app.db import (  # noqa: F401 - register tables
        auth_models,
        models,
        projection_models,
    )

    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
