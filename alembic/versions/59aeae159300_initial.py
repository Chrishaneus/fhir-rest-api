"""initial migration for resource_versions table

Revision ID: 59aeae159300
Revises:
Create Date: 2026-05-27 00:15:30.825469

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "59aeae159300"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resource_versions",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("version_id", sa.String(length=64), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_type",
            "resource_id",
            "version_id",
            name="uq_resource_versions_type_id_version",
        ),
    )
    op.create_index(
        op.f("ix_resource_versions_last_updated"), "resource_versions", ["last_updated"], unique=False
    )
    op.create_index(
        op.f("ix_resource_versions_resource_id"), "resource_versions", ["resource_id"], unique=False
    )
    op.create_index(
        op.f("ix_resource_versions_resource_type"), "resource_versions", ["resource_type"], unique=False
    )
    op.create_index(
        "ix_resource_versions_type_id",
        "resource_versions",
        ["resource_type", "resource_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_resource_versions_type_id", table_name="resource_versions")
    op.drop_index(op.f("ix_resource_versions_resource_type"), table_name="resource_versions")
    op.drop_index(op.f("ix_resource_versions_resource_id"), table_name="resource_versions")
    op.drop_index(op.f("ix_resource_versions_last_updated"), table_name="resource_versions")
    op.drop_table("resource_versions")
