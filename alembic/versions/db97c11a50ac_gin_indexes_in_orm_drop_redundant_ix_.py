"""gin_indexes_in_orm_drop_redundant_ix_users_username

Revision ID: db97c11a50ac
Revises: 5684b5d9f9f0
Create Date: 2026-05-30 00:17:04.428288

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "db97c11a50ac"
down_revision: str | Sequence[str] | None = "5684b5d9f9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 5684b5d9f9f0 already drops this index, so on a new database it is gone by now and a plain
    # drop fails. Keep the drop for databases migrated before 5684b5d9f9f0 dropped it.
    op.drop_index(op.f("ix_users_username"), table_name="users", if_exists=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Nothing to do: downgrading 5684b5d9f9f0 recreates the index, and creating it here too would
    # make that step fail.
    pass
