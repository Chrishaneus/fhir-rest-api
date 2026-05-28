"""Alembic migration environment.

DATABASE_URL is read from the environment (same variable as the app uses), so
there is no need to hardcode a URL in alembic.ini. The fallback matches the
app's default: a local SQLite file `fhir.db`.

Usage::

    # apply pending migrations against the configured database
    DATABASE_URL=... alembic upgrade head

    # generate a new autogenerate migration
    DATABASE_URL=sqlite:///./fhir.db alembic revision --autogenerate -m "add foo"
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ---------------------------------------------------------------------------
# Alembic config object
# ---------------------------------------------------------------------------

config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Override sqlalchemy.url from the DATABASE_URL environment variable so we
# never need to hard-code credentials in alembic.ini.
# ---------------------------------------------------------------------------

from app.config import DATABASE_URL  # noqa: E402

config.set_main_option("sqlalchemy.url", DATABASE_URL)

# ---------------------------------------------------------------------------
# Import all ORM models so their tables are registered in Base.metadata before
# autogenerate compares the schema.
# ---------------------------------------------------------------------------

from app.db import auth_models, models, projection_models  # noqa: E402, F401
from app.db.base import Base  # noqa: E402

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Migration runners
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Emit DROP before CREATE for columns that changed type.
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
