"""SQLAlchemy ORM models for per-resource-type projection tables.

A projection table holds **one row per current resource** (not per version),
with the small set of columns that we want to be able to filter and sort by
in SQL directly. The full FHIR JSON still lives in ``resource_versions``;
projections only carry the search keys plus a back-pointer to the version row.

Schema conventions for every projection
---------------------------------------

* ``resource_id`` -- primary key (resource type is implicit in the table).
* ``version_id``  -- the version of ``resource_versions`` this projection row
  reflects. Lets the search path join to the exact version that was current
  when the projection was last touched.
* ``last_updated`` -- mirrors ``resource_versions.last_updated`` so sorting
  and ``_lastUpdated`` range queries stay in the projection.
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

# --- Patient ---------------------------------------------------------------


class PatientIndex(Base):
    """Search projection for FHIR Patient."""

    __tablename__ = "patient_index"

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    family: Mapped[str | None] = mapped_column(String(255))
    given: Mapped[str | None] = mapped_column(String(255))
    gender: Mapped[str | None] = mapped_column(String(16))
    birth_date: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        Index("ix_patient_index_family", "family"),
        Index("ix_patient_index_given", "given"),
        Index("ix_patient_index_gender", "gender"),
        Index("ix_patient_index_birth_date", "birth_date"),
        Index("ix_patient_index_active", "active"),
        Index("ix_patient_index_last_updated", "last_updated"),
    )


# --- Practitioner ----------------------------------------------------------


class PractitionerIndex(Base):
    __tablename__ = "practitioner_index"

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    family: Mapped[str | None] = mapped_column(String(255))
    given: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        Index("ix_practitioner_index_family", "family"),
        Index("ix_practitioner_index_given", "given"),
        Index("ix_practitioner_index_active", "active"),
        Index("ix_practitioner_index_last_updated", "last_updated"),
    )


# --- Organization ----------------------------------------------------------


class OrganizationIndex(Base):
    __tablename__ = "organization_index"

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    name: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool | None] = mapped_column(Boolean)
    type_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_organization_index_name", "name"),
        Index("ix_organization_index_type", "type_code"),
        Index("ix_organization_index_active", "active"),
        Index("ix_organization_index_last_updated", "last_updated"),
    )


# --- Encounter -------------------------------------------------------------


class EncounterIndex(Base):
    __tablename__ = "encounter_index"

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    subject_ref: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    class_code: Mapped[str | None] = mapped_column(String(32))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_encounter_index_subject", "subject_ref"),
        Index("ix_encounter_index_status", "status"),
        Index("ix_encounter_index_class", "class_code"),
        Index("ix_encounter_index_period_start", "period_start"),
        Index("ix_encounter_index_period_end", "period_end"),
        Index("ix_encounter_index_last_updated", "last_updated"),
    )


# --- Observation -----------------------------------------------------------


class ObservationIndex(Base):
    """Search projection for FHIR Observation.

    We materialize the first coding's code (``code_code``) which covers >95%
    of real-world Observation queries (LOINC code). Numeric value extraction
    powers FHIR ``value-quantity`` queries.
    """

    __tablename__ = "observation_index"

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    subject_ref: Mapped[str | None] = mapped_column(String(128))
    encounter_ref: Mapped[str | None] = mapped_column(String(128))
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
        Index("ix_observation_index_subject_effective", "subject_ref", "effective_at"),
        Index("ix_observation_index_subject_code", "subject_ref", "code_code"),
        Index("ix_observation_index_code", "code_code"),
        Index("ix_observation_index_category", "category_code"),
        Index("ix_observation_index_encounter", "encounter_ref"),
        Index("ix_observation_index_status", "status"),
        # Standalone single-column index on effective_at: composite indexes
        # only accelerate queries that filter on their leading column, so
        # subject-less ``?date=ge...`` searches still need this.
        Index("ix_observation_index_effective_at", "effective_at"),
        Index("ix_observation_index_value_quantity", "value_quantity_value"),
        Index("ix_observation_index_last_updated", "last_updated"),
    )


# --- Condition -------------------------------------------------------------


class ConditionIndex(Base):
    __tablename__ = "condition_index"

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    subject_ref: Mapped[str | None] = mapped_column(String(128))
    encounter_ref: Mapped[str | None] = mapped_column(String(128))
    clinical_status: Mapped[str | None] = mapped_column(String(32))
    code_code: Mapped[str | None] = mapped_column(String(64))
    recorded_date: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        Index("ix_condition_index_subject", "subject_ref"),
        Index("ix_condition_index_encounter", "encounter_ref"),
        Index("ix_condition_index_code", "code_code"),
        Index("ix_condition_index_clinical_status", "clinical_status"),
        Index("ix_condition_index_recorded_date", "recorded_date"),
        Index("ix_condition_index_last_updated", "last_updated"),
    )


# --- AllergyIntolerance ----------------------------------------------------


class AllergyIntoleranceIndex(Base):
    __tablename__ = "allergyintolerance_index"

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    patient_ref: Mapped[str | None] = mapped_column(String(128))
    code_code: Mapped[str | None] = mapped_column(String(64))
    clinical_status: Mapped[str | None] = mapped_column(String(32))
    criticality: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("ix_allergyintolerance_index_patient", "patient_ref"),
        Index("ix_allergyintolerance_index_code", "code_code"),
        Index("ix_allergyintolerance_index_clinical_status", "clinical_status"),
        Index("ix_allergyintolerance_index_criticality", "criticality"),
        Index("ix_allergyintolerance_index_last_updated", "last_updated"),
    )


# --- MedicationRequest -----------------------------------------------------


class MedicationRequestIndex(Base):
    __tablename__ = "medicationrequest_index"

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    subject_ref: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    intent: Mapped[str | None] = mapped_column(String(32))
    authored_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requester_ref: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        Index("ix_medicationrequest_index_subject", "subject_ref"),
        Index("ix_medicationrequest_index_status", "status"),
        Index("ix_medicationrequest_index_intent", "intent"),
        Index("ix_medicationrequest_index_requester", "requester_ref"),
        Index("ix_medicationrequest_index_authored_on", "authored_on"),
        Index("ix_medicationrequest_index_last_updated", "last_updated"),
    )


# --- DiagnosticReport ------------------------------------------------------


class DiagnosticReportIndex(Base):
    __tablename__ = "diagnosticreport_index"

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    subject_ref: Mapped[str | None] = mapped_column(String(128))
    encounter_ref: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    code_code: Mapped[str | None] = mapped_column(String(64))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_diagnosticreport_index_subject", "subject_ref"),
        Index("ix_diagnosticreport_index_encounter", "encounter_ref"),
        Index("ix_diagnosticreport_index_code", "code_code"),
        Index("ix_diagnosticreport_index_status", "status"),
        Index("ix_diagnosticreport_index_effective_at", "effective_at"),
        Index("ix_diagnosticreport_index_last_updated", "last_updated"),
    )


# --- Procedure -------------------------------------------------------------


class ProcedureIndex(Base):
    __tablename__ = "procedure_index"

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    subject_ref: Mapped[str | None] = mapped_column(String(128))
    encounter_ref: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    code_code: Mapped[str | None] = mapped_column(String(64))
    performed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_procedure_index_subject", "subject_ref"),
        Index("ix_procedure_index_encounter", "encounter_ref"),
        Index("ix_procedure_index_code", "code_code"),
        Index("ix_procedure_index_status", "status"),
        Index("ix_procedure_index_performed_at", "performed_at"),
        Index("ix_procedure_index_last_updated", "last_updated"),
    )
