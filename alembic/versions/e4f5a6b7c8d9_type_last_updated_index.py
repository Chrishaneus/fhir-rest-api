"""add composite (resource_type, last_updated) index for _lastUpdated search

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-05-28 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: str | Sequence[str] | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_resource_versions_type_last_updated",
        "resource_versions",
        ["resource_type", "last_updated"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_resource_versions_type_last_updated",
        table_name="resource_versions",
    )
