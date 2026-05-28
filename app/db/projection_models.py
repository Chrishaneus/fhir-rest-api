"""SQLAlchemy ORM models for per-resource-type projection tables.

A projection table holds **one row per current resource** (not per version),
with the small set of columns that we want to be able to filter and sort by
in SQL directly. The full FHIR JSON still lives in `resource_versions`;
projections only carry the search keys plus a back-pointer to the version row.

Schema conventions for every projection
---------------------------------------

* `resource_id` -- primary key (resource type is implicit in the table).
* `version_id`  -- the version of `resource_versions` this projection row
  reflects. Lets the search path join to the exact version that was current
  when the projection was last touched.
* `last_updated` -- mirrors `resource_versions.last_updated` so sorting
  and `_lastUpdated` range queries stay in the projection.
* All other columns are search keys, indexed individually unless they are
  already covered by a composite index.

Multi-valued fields (e.g. Patient.name, Patient.identifier) are projected as
the **first** entry only for now. Full multi-row child projections are a
follow-up; the README documents the limitation.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BaseProjection(Base):
    """Abstract base for every projection table.

    Declares the three columns every projection has (`resource_id`,
    `version_id`, `last_updated`) so the schema definition stays DRY *and*
    so static type-checkers can see those columns when we hold a
    `type[BaseProjection]` in :attr:`app.projections.base.Projection.table`.

    `__abstract__ = True` tells SQLAlchemy not to materialize a table for
    this class itself; only its concrete subclasses get tables.
    """

    __abstract__ = True

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# --- Patient ---------------------------------------------------------------


class PatientProjectionSchema(BaseProjection):
    """ORM model for patient_projection table."""

    __tablename__ = "patient_projection"

    family: Mapped[str | None] = mapped_column(String(255))
    given: Mapped[str | None] = mapped_column(String(255))
    gender: Mapped[str | None] = mapped_column(String(16))
    birth_date: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        Index("ix_patient_projection_family", "family"),
        Index("ix_patient_projection_given", "given"),
        Index("ix_patient_projection_gender", "gender"),
        Index("ix_patient_projection_birth_date", "birth_date"),
        Index("ix_patient_projection_active", "active"),
        Index("ix_patient_projection_last_updated", "last_updated"),
    )


# --- Practitioner ----------------------------------------------------------


class PractitionerProjectionSchema(BaseProjection):
    __tablename__ = "practitioner_projection"

    family: Mapped[str | None] = mapped_column(String(255))
    given: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        Index("ix_practitioner_projection_family", "family"),
        Index("ix_practitioner_projection_given", "given"),
        Index("ix_practitioner_projection_active", "active"),
        Index("ix_practitioner_projection_last_updated", "last_updated"),
    )


# --- Organization ----------------------------------------------------------


class OrganizationProjectionSchema(BaseProjection):
    __tablename__ = "organization_projection"

    name: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool | None] = mapped_column(Boolean)
    type_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_organization_projection_name", "name"),
        Index("ix_organization_projection_type", "type_code"),
        Index("ix_organization_projection_active", "active"),
        Index("ix_organization_projection_last_updated", "last_updated"),
    )


# --- Encounter -------------------------------------------------------------


class EncounterProjectionSchema(BaseProjection):
    __tablename__ = "encounter_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    class_code: Mapped[str | None] = mapped_column(String(32))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_encounter_projection_subject", "subject_reference"),
        Index("ix_encounter_projection_status", "status"),
        Index("ix_encounter_projection_class", "class_code"),
        Index("ix_encounter_projection_period_start", "period_start"),
        Index("ix_encounter_projection_period_end", "period_end"),
        Index("ix_encounter_projection_last_updated", "last_updated"),
    )


# --- Observation -----------------------------------------------------------


class ObservationProjectionSchema(BaseProjection):
    """Search projection for FHIR Observation.

    We materialize the first coding's code (`code_code`) which covers >95%
    of real-world Observation queries (LOINC code). Numeric value extraction
    powers FHIR `value-quantity` queries.
    """

    __tablename__ = "observation_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    encounter_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    code_system: Mapped[str | None] = mapped_column(String(255))
    code_code: Mapped[str | None] = mapped_column(String(64))
    category_code: Mapped[str | None] = mapped_column(String(64))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    value_quantity_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    value_quantity_unit: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        # Composite indexes for the most common queries (chart timeline,
        # all observations of a specific code for a specific patient).
        Index("ix_observation_projection_subject_effective", "subject_reference", "effective_at"),
        Index("ix_observation_projection_subject_code", "subject_reference", "code_code"),
        Index("ix_observation_projection_code", "code_code"),
        Index("ix_observation_projection_category", "category_code"),
        Index("ix_observation_projection_encounter", "encounter_reference"),
        Index("ix_observation_projection_status", "status"),
        # Standalone single-column index on effective_at: composite indexes
        # only accelerate queries that filter on their leading column, so
        # subject-less `?date=ge...` searches still need this.
        Index("ix_observation_projection_effective_at", "effective_at"),
        Index("ix_observation_projection_value_quantity", "value_quantity_value"),
        Index("ix_observation_projection_last_updated", "last_updated"),
    )


# --- Condition -------------------------------------------------------------


class ConditionProjectionSchema(BaseProjection):
    __tablename__ = "condition_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    encounter_reference: Mapped[str | None] = mapped_column(String(128))
    clinical_status: Mapped[str | None] = mapped_column(String(32))
    code_code: Mapped[str | None] = mapped_column(String(64))
    recorded_date: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        Index("ix_condition_projection_subject", "subject_reference"),
        Index("ix_condition_projection_encounter", "encounter_reference"),
        Index("ix_condition_projection_code", "code_code"),
        Index("ix_condition_projection_clinical_status", "clinical_status"),
        Index("ix_condition_projection_recorded_date", "recorded_date"),
        Index("ix_condition_projection_last_updated", "last_updated"),
    )


# --- AllergyIntolerance ----------------------------------------------------


class AllergyIntoleranceProjectionSchema(BaseProjection):
    __tablename__ = "allergyintolerance_projection"

    patient_reference: Mapped[str | None] = mapped_column(String(128))
    code_code: Mapped[str | None] = mapped_column(String(64))
    clinical_status: Mapped[str | None] = mapped_column(String(32))
    criticality: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("ix_allergyintolerance_projection_patient", "patient_reference"),
        Index("ix_allergyintolerance_projection_code", "code_code"),
        Index("ix_allergyintolerance_projection_clinical_status", "clinical_status"),
        Index("ix_allergyintolerance_projection_criticality", "criticality"),
        Index("ix_allergyintolerance_projection_last_updated", "last_updated"),
    )


# --- MedicationRequest -----------------------------------------------------


class MedicationRequestProjectionSchema(BaseProjection):
    __tablename__ = "medicationrequest_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    intent: Mapped[str | None] = mapped_column(String(32))
    authored_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requester_reference: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        Index("ix_medicationrequest_projection_subject", "subject_reference"),
        Index("ix_medicationrequest_projection_status", "status"),
        Index("ix_medicationrequest_projection_intent", "intent"),
        Index("ix_medicationrequest_projection_requester", "requester_reference"),
        Index("ix_medicationrequest_projection_authored_on", "authored_on"),
        Index("ix_medicationrequest_projection_last_updated", "last_updated"),
    )


# --- DiagnosticReport ------------------------------------------------------


class DiagnosticReportProjectionSchema(BaseProjection):
    __tablename__ = "diagnosticreport_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    encounter_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    code_code: Mapped[str | None] = mapped_column(String(64))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_diagnosticreport_projection_subject", "subject_reference"),
        Index("ix_diagnosticreport_projection_encounter", "encounter_reference"),
        Index("ix_diagnosticreport_projection_code", "code_code"),
        Index("ix_diagnosticreport_projection_status", "status"),
        Index("ix_diagnosticreport_projection_effective_at", "effective_at"),
        Index("ix_diagnosticreport_projection_last_updated", "last_updated"),
    )


# --- Procedure -------------------------------------------------------------


class ProcedureProjectionSchema(BaseProjection):
    __tablename__ = "procedure_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    encounter_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    code_code: Mapped[str | None] = mapped_column(String(64))
    performed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_procedure_projection_subject", "subject_reference"),
        Index("ix_procedure_projection_encounter", "encounter_reference"),
        Index("ix_procedure_projection_code", "code_code"),
        Index("ix_procedure_projection_status", "status"),
        Index("ix_procedure_projection_performed_at", "performed_at"),
        Index("ix_procedure_projection_last_updated", "last_updated"),
    )
