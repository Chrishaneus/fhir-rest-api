"""SQLAlchemy ORM models for FHIR resource versions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# BIGINT on Postgres so we can grow past the 32-bit INTEGER ceiling (~2.1B rows);
# SQLite only auto-increments the literal INTEGER PRIMARY KEY, so use the
# Integer variant there. At 1M versions/day, BIGINT lasts ~25 million years.
_PK_TYPE = BigInteger().with_variant(Integer(), "sqlite")

# JSONB on Postgres so we can use GIN + `@>` containment for indexed search.
# SQLite has no JSONB type; fall back to JSON (text), which keeps the unit tests
# fast but does mean the Python fallback path in FHIRStore handles SQLite search.
_CONTENT_TYPE = JSONB().with_variant(JSON(), "sqlite")


class ResourceVersionRecord(Base):
    """One row per FHIR resource version (including deletion tombstones)."""

    __tablename__ = "resource_versions"

    id: Mapped[int] = mapped_column(_PK_TYPE, primary_key=True, autoincrement=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content: Mapped[dict[str, Any] | None] = mapped_column(_CONTENT_TYPE, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "resource_type",
            "resource_id",
            "version_id",
            name="uq_resource_versions_type_id_version",
        ),
        Index("ix_resource_versions_type_id", "resource_type", "resource_id"),
        Index("ix_resource_versions_type_last_updated", "resource_type", "last_updated"),
        # GIN indexes for JSONB containment search (Postgres only; SQLite falls
        # back to a plain B-tree index which is unused but harmless in tests).
        Index(
            "ix_resource_versions_content_gin",
            "content",
            postgresql_using="gin",
        ),
        Index(
            "ix_resource_versions_content_path_ops",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "jsonb_path_ops"},
        ),
    )
