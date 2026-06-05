"""projection tables

Revision ID: b1c2d3e4f5a6
Revises: 59aeae159300
Create Date: 2026-05-27 00:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "59aeae159300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- patient_projection ---------------------------------------------------
    op.create_table(
        "patient_projection",
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(64), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("family", sa.String(255), nullable=True),
        sa.Column("given", sa.String(255), nullable=True),
        sa.Column("gender", sa.String(16), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("resource_id"),
    )
    op.create_index("ix_patient_projection_family", "patient_projection", ["family"])
    op.create_index("ix_patient_projection_given", "patient_projection", ["given"])
    op.create_index("ix_patient_projection_gender", "patient_projection", ["gender"])
    op.create_index("ix_patient_projection_birth_date", "patient_projection", ["birth_date"])
    op.create_index("ix_patient_projection_active", "patient_projection", ["active"])
    op.create_index("ix_patient_projection_last_updated", "patient_projection", ["last_updated"])

    # --- practitioner_projection ----------------------------------------------
    op.create_table(
        "practitioner_projection",
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(64), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("family", sa.String(255), nullable=True),
        sa.Column("given", sa.String(255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("resource_id"),
    )
    op.create_index("ix_practitioner_projection_family", "practitioner_projection", ["family"])
    op.create_index("ix_practitioner_projection_given", "practitioner_projection", ["given"])
    op.create_index("ix_practitioner_projection_active", "practitioner_projection", ["active"])
    op.create_index(
        "ix_practitioner_projection_last_updated", "practitioner_projection", ["last_updated"]
    )

    # --- organization_projection ----------------------------------------------
    op.create_table(
        "organization_projection",
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(64), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("type_code", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("resource_id"),
    )
    op.create_index("ix_organization_projection_name", "organization_projection", ["name"])
    op.create_index("ix_organization_projection_type", "organization_projection", ["type_code"])
    op.create_index("ix_organization_projection_active", "organization_projection", ["active"])
    op.create_index(
        "ix_organization_projection_last_updated", "organization_projection", ["last_updated"]
    )

    # --- encounter_projection -------------------------------------------------
    op.create_table(
        "encounter_projection",
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(64), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject_ref", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("class_code", sa.String(32), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("resource_id"),
    )
    op.create_index("ix_encounter_projection_subject", "encounter_projection", ["subject_ref"])
    op.create_index("ix_encounter_projection_status", "encounter_projection", ["status"])
    op.create_index("ix_encounter_projection_class", "encounter_projection", ["class_code"])
    op.create_index(
        "ix_encounter_projection_period_start", "encounter_projection", ["period_start"]
    )
    op.create_index("ix_encounter_projection_period_end", "encounter_projection", ["period_end"])
    op.create_index(
        "ix_encounter_projection_last_updated", "encounter_projection", ["last_updated"]
    )

    # --- observation_projection -----------------------------------------------
    op.create_table(
        "observation_projection",
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(64), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject_ref", sa.String(128), nullable=True),
        sa.Column("encounter_ref", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("code_system", sa.String(255), nullable=True),
        sa.Column("code_code", sa.String(64), nullable=True),
        sa.Column("category_code", sa.String(64), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("value_quantity_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("value_quantity_unit", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("resource_id"),
    )
    op.create_index(
        "ix_observation_projection_subject_effective",
        "observation_projection",
        ["subject_ref", "effective_at"],
    )
    op.create_index(
        "ix_observation_projection_subject_code",
        "observation_projection",
        ["subject_ref", "code_code"],
    )
    op.create_index("ix_observation_projection_code", "observation_projection", ["code_code"])
    op.create_index(
        "ix_observation_projection_category", "observation_projection", ["category_code"]
    )
    op.create_index(
        "ix_observation_projection_encounter", "observation_projection", ["encounter_ref"]
    )
    op.create_index("ix_observation_projection_status", "observation_projection", ["status"])
    op.create_index(
        "ix_observation_projection_effective_at", "observation_projection", ["effective_at"]
    )
    op.create_index(
        "ix_observation_projection_value_quantity",
        "observation_projection",
        ["value_quantity_value"],
    )
    op.create_index(
        "ix_observation_projection_last_updated", "observation_projection", ["last_updated"]
    )

    # --- condition_projection -------------------------------------------------
    op.create_table(
        "condition_projection",
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(64), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject_ref", sa.String(128), nullable=True),
        sa.Column("encounter_ref", sa.String(128), nullable=True),
        sa.Column("clinical_status", sa.String(32), nullable=True),
        sa.Column("code_code", sa.String(64), nullable=True),
        sa.Column("recorded_date", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("resource_id"),
    )
    op.create_index("ix_condition_projection_subject", "condition_projection", ["subject_ref"])
    op.create_index("ix_condition_projection_encounter", "condition_projection", ["encounter_ref"])
    op.create_index("ix_condition_projection_code", "condition_projection", ["code_code"])
    op.create_index(
        "ix_condition_projection_clinical_status", "condition_projection", ["clinical_status"]
    )
    op.create_index(
        "ix_condition_projection_recorded_date", "condition_projection", ["recorded_date"]
    )
    op.create_index(
        "ix_condition_projection_last_updated", "condition_projection", ["last_updated"]
    )

    # --- allergyintolerance_projection ----------------------------------------
    op.create_table(
        "allergyintolerance_projection",
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(64), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("patient_ref", sa.String(128), nullable=True),
        sa.Column("code_code", sa.String(64), nullable=True),
        sa.Column("clinical_status", sa.String(32), nullable=True),
        sa.Column("criticality", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("resource_id"),
    )
    op.create_index(
        "ix_allergyintolerance_projection_patient", "allergyintolerance_projection", ["patient_ref"]
    )
    op.create_index(
        "ix_allergyintolerance_projection_code", "allergyintolerance_projection", ["code_code"]
    )
    op.create_index(
        "ix_allergyintolerance_projection_clinical_status",
        "allergyintolerance_projection",
        ["clinical_status"],
    )
    op.create_index(
        "ix_allergyintolerance_projection_criticality",
        "allergyintolerance_projection",
        ["criticality"],
    )
    op.create_index(
        "ix_allergyintolerance_projection_last_updated",
        "allergyintolerance_projection",
        ["last_updated"],
    )

    # --- medicationrequest_projection -----------------------------------------
    op.create_table(
        "medicationrequest_projection",
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(64), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject_ref", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("intent", sa.String(32), nullable=True),
        sa.Column("authored_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requester_ref", sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint("resource_id"),
    )
    op.create_index(
        "ix_medicationrequest_projection_subject", "medicationrequest_projection", ["subject_ref"]
    )
    op.create_index(
        "ix_medicationrequest_projection_status", "medicationrequest_projection", ["status"]
    )
    op.create_index(
        "ix_medicationrequest_projection_intent", "medicationrequest_projection", ["intent"]
    )
    op.create_index(
        "ix_medicationrequest_projection_requester",
        "medicationrequest_projection",
        ["requester_ref"],
    )
    op.create_index(
        "ix_medicationrequest_projection_authored_on",
        "medicationrequest_projection",
        ["authored_on"],
    )
    op.create_index(
        "ix_medicationrequest_projection_last_updated",
        "medicationrequest_projection",
        ["last_updated"],
    )

    # --- diagnosticreport_projection ------------------------------------------
    op.create_table(
        "diagnosticreport_projection",
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(64), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject_ref", sa.String(128), nullable=True),
        sa.Column("encounter_ref", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("code_code", sa.String(64), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("resource_id"),
    )
    op.create_index(
        "ix_diagnosticreport_projection_subject", "diagnosticreport_projection", ["subject_ref"]
    )
    op.create_index(
        "ix_diagnosticreport_projection_encounter", "diagnosticreport_projection", ["encounter_ref"]
    )
    op.create_index(
        "ix_diagnosticreport_projection_code", "diagnosticreport_projection", ["code_code"]
    )
    op.create_index(
        "ix_diagnosticreport_projection_status", "diagnosticreport_projection", ["status"]
    )
    op.create_index(
        "ix_diagnosticreport_projection_effective_at",
        "diagnosticreport_projection",
        ["effective_at"],
    )
    op.create_index(
        "ix_diagnosticreport_projection_last_updated",
        "diagnosticreport_projection",
        ["last_updated"],
    )

    # --- procedure_projection -------------------------------------------------
    op.create_table(
        "procedure_projection",
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(64), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject_ref", sa.String(128), nullable=True),
        sa.Column("encounter_ref", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("code_code", sa.String(64), nullable=True),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("resource_id"),
    )
    op.create_index("ix_procedure_projection_subject", "procedure_projection", ["subject_ref"])
    op.create_index("ix_procedure_projection_encounter", "procedure_projection", ["encounter_ref"])
    op.create_index("ix_procedure_projection_code", "procedure_projection", ["code_code"])
    op.create_index("ix_procedure_projection_status", "procedure_projection", ["status"])
    op.create_index(
        "ix_procedure_projection_performed_at", "procedure_projection", ["performed_at"]
    )
    op.create_index(
        "ix_procedure_projection_last_updated", "procedure_projection", ["last_updated"]
    )


def downgrade() -> None:
    op.drop_index("ix_procedure_projection_last_updated", table_name="procedure_projection")
    op.drop_index("ix_procedure_projection_performed_at", table_name="procedure_projection")
    op.drop_index("ix_procedure_projection_status", table_name="procedure_projection")
    op.drop_index("ix_procedure_projection_code", table_name="procedure_projection")
    op.drop_index("ix_procedure_projection_encounter", table_name="procedure_projection")
    op.drop_index("ix_procedure_projection_subject", table_name="procedure_projection")
    op.drop_table("procedure_projection")

    op.drop_index(
        "ix_diagnosticreport_projection_last_updated", table_name="diagnosticreport_projection"
    )
    op.drop_index(
        "ix_diagnosticreport_projection_effective_at", table_name="diagnosticreport_projection"
    )
    op.drop_index("ix_diagnosticreport_projection_status", table_name="diagnosticreport_projection")
    op.drop_index("ix_diagnosticreport_projection_code", table_name="diagnosticreport_projection")
    op.drop_index(
        "ix_diagnosticreport_projection_encounter", table_name="diagnosticreport_projection"
    )
    op.drop_index(
        "ix_diagnosticreport_projection_subject", table_name="diagnosticreport_projection"
    )
    op.drop_table("diagnosticreport_projection")

    op.drop_index(
        "ix_medicationrequest_projection_last_updated", table_name="medicationrequest_projection"
    )
    op.drop_index(
        "ix_medicationrequest_projection_authored_on", table_name="medicationrequest_projection"
    )
    op.drop_index(
        "ix_medicationrequest_projection_requester", table_name="medicationrequest_projection"
    )
    op.drop_index(
        "ix_medicationrequest_projection_intent", table_name="medicationrequest_projection"
    )
    op.drop_index(
        "ix_medicationrequest_projection_status", table_name="medicationrequest_projection"
    )
    op.drop_index(
        "ix_medicationrequest_projection_subject", table_name="medicationrequest_projection"
    )
    op.drop_table("medicationrequest_projection")

    op.drop_index(
        "ix_allergyintolerance_projection_last_updated", table_name="allergyintolerance_projection"
    )
    op.drop_index(
        "ix_allergyintolerance_projection_criticality", table_name="allergyintolerance_projection"
    )
    op.drop_index(
        "ix_allergyintolerance_projection_clinical_status",
        table_name="allergyintolerance_projection",
    )
    op.drop_index(
        "ix_allergyintolerance_projection_code", table_name="allergyintolerance_projection"
    )
    op.drop_index(
        "ix_allergyintolerance_projection_patient", table_name="allergyintolerance_projection"
    )
    op.drop_table("allergyintolerance_projection")

    op.drop_index("ix_condition_projection_last_updated", table_name="condition_projection")
    op.drop_index("ix_condition_projection_recorded_date", table_name="condition_projection")
    op.drop_index("ix_condition_projection_clinical_status", table_name="condition_projection")
    op.drop_index("ix_condition_projection_code", table_name="condition_projection")
    op.drop_index("ix_condition_projection_encounter", table_name="condition_projection")
    op.drop_index("ix_condition_projection_subject", table_name="condition_projection")
    op.drop_table("condition_projection")

    op.drop_index("ix_observation_projection_last_updated", table_name="observation_projection")
    op.drop_index("ix_observation_projection_value_quantity", table_name="observation_projection")
    op.drop_index("ix_observation_projection_effective_at", table_name="observation_projection")
    op.drop_index("ix_observation_projection_status", table_name="observation_projection")
    op.drop_index("ix_observation_projection_encounter", table_name="observation_projection")
    op.drop_index("ix_observation_projection_category", table_name="observation_projection")
    op.drop_index("ix_observation_projection_code", table_name="observation_projection")
    op.drop_index("ix_observation_projection_subject_code", table_name="observation_projection")
    op.drop_index(
        "ix_observation_projection_subject_effective", table_name="observation_projection"
    )
    op.drop_table("observation_projection")

    op.drop_index("ix_encounter_projection_last_updated", table_name="encounter_projection")
    op.drop_index("ix_encounter_projection_period_end", table_name="encounter_projection")
    op.drop_index("ix_encounter_projection_period_start", table_name="encounter_projection")
    op.drop_index("ix_encounter_projection_class", table_name="encounter_projection")
    op.drop_index("ix_encounter_projection_status", table_name="encounter_projection")
    op.drop_index("ix_encounter_projection_subject", table_name="encounter_projection")
    op.drop_table("encounter_projection")

    op.drop_index("ix_organization_projection_last_updated", table_name="organization_projection")
    op.drop_index("ix_organization_projection_active", table_name="organization_projection")
    op.drop_index("ix_organization_projection_type", table_name="organization_projection")
    op.drop_index("ix_organization_projection_name", table_name="organization_projection")
    op.drop_table("organization_projection")

    op.drop_index("ix_practitioner_projection_last_updated", table_name="practitioner_projection")
    op.drop_index("ix_practitioner_projection_active", table_name="practitioner_projection")
    op.drop_index("ix_practitioner_projection_given", table_name="practitioner_projection")
    op.drop_index("ix_practitioner_projection_family", table_name="practitioner_projection")
    op.drop_table("practitioner_projection")

    op.drop_index("ix_patient_projection_last_updated", table_name="patient_projection")
    op.drop_index("ix_patient_projection_active", table_name="patient_projection")
    op.drop_index("ix_patient_projection_birth_date", table_name="patient_projection")
    op.drop_index("ix_patient_projection_gender", table_name="patient_projection")
    op.drop_index("ix_patient_projection_given", table_name="patient_projection")
    op.drop_index("ix_patient_projection_family", table_name="patient_projection")
    op.drop_table("patient_projection")
