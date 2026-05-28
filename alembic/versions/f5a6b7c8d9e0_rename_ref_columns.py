"""rename _ref projection columns to _reference for clarity

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-05-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, old_column, new_column)
_RENAMES: list[tuple[str, str, str]] = [
    ("encounter_projection",          "subject_ref",   "subject_reference"),
    ("observation_projection",        "subject_ref",   "subject_reference"),
    ("observation_projection",        "encounter_ref", "encounter_reference"),
    ("condition_projection",          "subject_ref",   "subject_reference"),
    ("condition_projection",          "encounter_ref", "encounter_reference"),
    ("allergyintolerance_projection", "patient_ref",   "patient_reference"),
    ("medicationrequest_projection",  "subject_ref",   "subject_reference"),
    ("medicationrequest_projection",  "requester_ref", "requester_reference"),
    ("diagnosticreport_projection",   "subject_ref",   "subject_reference"),
    ("diagnosticreport_projection",   "encounter_ref", "encounter_reference"),
    ("procedure_projection",          "subject_ref",   "subject_reference"),
    ("procedure_projection",          "encounter_ref", "encounter_reference"),
]


def upgrade() -> None:
    for table, old_col, new_col in _RENAMES:
        op.alter_column(table, old_col, new_column_name=new_col)


def downgrade() -> None:
    for table, old_col, new_col in reversed(_RENAMES):
        op.alter_column(table, new_col, new_column_name=old_col)
