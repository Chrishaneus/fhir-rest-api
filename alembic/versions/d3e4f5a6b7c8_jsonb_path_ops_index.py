"""add jsonb_path_ops GIN index for recursive reference search

Revision ID: d3e4f5a6b7c8
Revises: 2a3b4c5d6e7f
Create Date: 2026-05-28 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | Sequence[str] | None = "2a3b4c5d6e7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.create_index(
        "ix_resource_versions_content_path_ops",
        "resource_versions",
        ["content"],
        postgresql_using="gin",
        postgresql_ops={"content": "jsonb_path_ops"},
    )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.drop_index(
        "ix_resource_versions_content_path_ops",
        table_name="resource_versions",
    )
