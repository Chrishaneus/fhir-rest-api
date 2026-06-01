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


# --- PractitionerRole -------------------------------------------------------


class PractitionerRoleProjectionSchema(BaseProjection):
    __tablename__ = "practitionerrole_projection"

    practitioner_reference: Mapped[str | None] = mapped_column(String(128))
    organization_reference: Mapped[str | None] = mapped_column(String(128))
    role_code: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        Index("ix_practitionerrole_projection_practitioner", "practitioner_reference"),
        Index("ix_practitionerrole_projection_organization", "organization_reference"),
        Index("ix_practitionerrole_projection_role", "role_code"),
        Index("ix_practitionerrole_projection_active", "active"),
        Index("ix_practitionerrole_projection_last_updated", "last_updated"),
    )


# --- Location ----------------------------------------------------------------


class LocationProjectionSchema(BaseProjection):
    __tablename__ = "location_projection"

    name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(32))
    type_code: Mapped[str | None] = mapped_column(String(64))
    organization_reference: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        Index("ix_location_projection_name", "name"),
        Index("ix_location_projection_status", "status"),
        Index("ix_location_projection_type", "type_code"),
        Index("ix_location_projection_organization", "organization_reference"),
        Index("ix_location_projection_last_updated", "last_updated"),
    )


# --- RelatedPerson -----------------------------------------------------------


class RelatedPersonProjectionSchema(BaseProjection):
    __tablename__ = "relatedperson_projection"

    patient_reference: Mapped[str | None] = mapped_column(String(128))
    family: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        Index("ix_relatedperson_projection_patient", "patient_reference"),
        Index("ix_relatedperson_projection_family", "family"),
        Index("ix_relatedperson_projection_active", "active"),
        Index("ix_relatedperson_projection_last_updated", "last_updated"),
    )


# --- EpisodeOfCare -----------------------------------------------------------


class EpisodeOfCareProjectionSchema(BaseProjection):
    __tablename__ = "episodeofcare_projection"

    patient_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_episodeofcare_projection_patient", "patient_reference"),
        Index("ix_episodeofcare_projection_status", "status"),
        Index("ix_episodeofcare_projection_period_start", "period_start"),
        Index("ix_episodeofcare_projection_last_updated", "last_updated"),
    )


# --- Schedule ----------------------------------------------------------------


class ScheduleProjectionSchema(BaseProjection):
    __tablename__ = "schedule_projection"

    actor_reference: Mapped[str | None] = mapped_column(String(128))
    active: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        Index("ix_schedule_projection_actor", "actor_reference"),
        Index("ix_schedule_projection_active", "active"),
        Index("ix_schedule_projection_last_updated", "last_updated"),
    )


# --- Slot --------------------------------------------------------------------


class SlotProjectionSchema(BaseProjection):
    __tablename__ = "slot_projection"

    schedule_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_slot_projection_schedule", "schedule_reference"),
        Index("ix_slot_projection_status", "status"),
        Index("ix_slot_projection_start_at", "start_at"),
        Index("ix_slot_projection_last_updated", "last_updated"),
    )


# --- Appointment -------------------------------------------------------------


class AppointmentProjectionSchema(BaseProjection):
    __tablename__ = "appointment_projection"

    patient_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_appointment_projection_patient", "patient_reference"),
        Index("ix_appointment_projection_status", "status"),
        Index("ix_appointment_projection_start_at", "start_at"),
        Index("ix_appointment_projection_last_updated", "last_updated"),
    )


# --- FamilyMemberHistory -----------------------------------------------------


class FamilyMemberHistoryProjectionSchema(BaseProjection):
    __tablename__ = "familymemberhistory_projection"

    patient_reference: Mapped[str | None] = mapped_column(String(128))
    relationship_code: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("ix_familymemberhistory_projection_patient", "patient_reference"),
        Index("ix_familymemberhistory_projection_relationship", "relationship_code"),
        Index("ix_familymemberhistory_projection_status", "status"),
        Index("ix_familymemberhistory_projection_last_updated", "last_updated"),
    )


# --- Immunization ------------------------------------------------------------


class ImmunizationProjectionSchema(BaseProjection):
    __tablename__ = "immunization_projection"

    patient_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    vaccine_code: Mapped[str | None] = mapped_column(String(64))
    occurrence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_immunization_projection_patient", "patient_reference"),
        Index("ix_immunization_projection_status", "status"),
        Index("ix_immunization_projection_vaccine_code", "vaccine_code"),
        Index("ix_immunization_projection_occurrence_at", "occurrence_at"),
        Index("ix_immunization_projection_last_updated", "last_updated"),
    )


# --- Specimen ----------------------------------------------------------------


class SpecimenProjectionSchema(BaseProjection):
    __tablename__ = "specimen_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    type_code: Mapped[str | None] = mapped_column(String(64))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("ix_specimen_projection_subject", "subject_reference"),
        Index("ix_specimen_projection_type", "type_code"),
        Index("ix_specimen_projection_collected_at", "collected_at"),
        Index("ix_specimen_projection_status", "status"),
        Index("ix_specimen_projection_last_updated", "last_updated"),
    )


# --- ImagingStudy ------------------------------------------------------------


class ImagingStudyProjectionSchema(BaseProjection):
    __tablename__ = "imagingstudy_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    encounter_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_imagingstudy_projection_subject", "subject_reference"),
        Index("ix_imagingstudy_projection_encounter", "encounter_reference"),
        Index("ix_imagingstudy_projection_status", "status"),
        Index("ix_imagingstudy_projection_started_at", "started_at"),
        Index("ix_imagingstudy_projection_last_updated", "last_updated"),
    )


# --- Medication --------------------------------------------------------------


class MedicationProjectionSchema(BaseProjection):
    __tablename__ = "medication_projection"

    code_code: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("ix_medication_projection_code", "code_code"),
        Index("ix_medication_projection_status", "status"),
        Index("ix_medication_projection_last_updated", "last_updated"),
    )


# --- MedicationStatement -----------------------------------------------------


class MedicationStatementProjectionSchema(BaseProjection):
    __tablename__ = "medicationstatement_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_medicationstatement_projection_subject", "subject_reference"),
        Index("ix_medicationstatement_projection_status", "status"),
        Index("ix_medicationstatement_projection_effective_at", "effective_at"),
        Index("ix_medicationstatement_projection_last_updated", "last_updated"),
    )


# --- MedicationDispense ------------------------------------------------------


class MedicationDispenseProjectionSchema(BaseProjection):
    __tablename__ = "medicationdispense_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    when_handed_over: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_medicationdispense_projection_subject", "subject_reference"),
        Index("ix_medicationdispense_projection_status", "status"),
        Index("ix_medicationdispense_projection_when_handed_over", "when_handed_over"),
        Index("ix_medicationdispense_projection_last_updated", "last_updated"),
    )


# --- MedicationAdministration ------------------------------------------------


class MedicationAdministrationProjectionSchema(BaseProjection):
    __tablename__ = "medicationadministration_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    encounter_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    occurrence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_medicationadministration_projection_subject", "subject_reference"),
        Index("ix_medicationadministration_projection_encounter", "encounter_reference"),
        Index("ix_medicationadministration_projection_status", "status"),
        Index("ix_medicationadministration_projection_occurrence_at", "occurrence_at"),
        Index("ix_medicationadministration_projection_last_updated", "last_updated"),
    )


# --- CarePlan ----------------------------------------------------------------


class CarePlanProjectionSchema(BaseProjection):
    __tablename__ = "careplan_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    encounter_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("ix_careplan_projection_subject", "subject_reference"),
        Index("ix_careplan_projection_encounter", "encounter_reference"),
        Index("ix_careplan_projection_status", "status"),
        Index("ix_careplan_projection_last_updated", "last_updated"),
    )


# --- Goal --------------------------------------------------------------------


class GoalProjectionSchema(BaseProjection):
    __tablename__ = "goal_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    lifecycle_status: Mapped[str | None] = mapped_column(String(32))
    start_date: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        Index("ix_goal_projection_subject", "subject_reference"),
        Index("ix_goal_projection_lifecycle_status", "lifecycle_status"),
        Index("ix_goal_projection_start_date", "start_date"),
        Index("ix_goal_projection_last_updated", "last_updated"),
    )


# --- ServiceRequest ----------------------------------------------------------


class ServiceRequestProjectionSchema(BaseProjection):
    __tablename__ = "servicerequest_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    encounter_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    authored_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_servicerequest_projection_subject", "subject_reference"),
        Index("ix_servicerequest_projection_encounter", "encounter_reference"),
        Index("ix_servicerequest_projection_status", "status"),
        Index("ix_servicerequest_projection_authored_on", "authored_on"),
        Index("ix_servicerequest_projection_last_updated", "last_updated"),
    )


# --- Task --------------------------------------------------------------------


class TaskProjectionSchema(BaseProjection):
    __tablename__ = "task_projection"

    status: Mapped[str | None] = mapped_column(String(32))
    intent: Mapped[str | None] = mapped_column(String(32))
    authored_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_reference: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        Index("ix_task_projection_status", "status"),
        Index("ix_task_projection_intent", "intent"),
        Index("ix_task_projection_authored_on", "authored_on"),
        Index("ix_task_projection_owner", "owner_reference"),
        Index("ix_task_projection_last_updated", "last_updated"),
    )


# --- NutritionOrder ----------------------------------------------------------


class NutritionOrderProjectionSchema(BaseProjection):
    __tablename__ = "nutritionorder_projection"

    patient_reference: Mapped[str | None] = mapped_column(String(128))
    encounter_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("ix_nutritionorder_projection_patient", "patient_reference"),
        Index("ix_nutritionorder_projection_encounter", "encounter_reference"),
        Index("ix_nutritionorder_projection_status", "status"),
        Index("ix_nutritionorder_projection_last_updated", "last_updated"),
    )


# --- DocumentReference -------------------------------------------------------


class DocumentReferenceProjectionSchema(BaseProjection):
    __tablename__ = "documentreference_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    doc_status: Mapped[str | None] = mapped_column(String(32))
    type_code: Mapped[str | None] = mapped_column(String(64))
    doc_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_documentreference_projection_subject", "subject_reference"),
        Index("ix_documentreference_projection_status", "status"),
        Index("ix_documentreference_projection_doc_status", "doc_status"),
        Index("ix_documentreference_projection_type", "type_code"),
        Index("ix_documentreference_projection_doc_date", "doc_date"),
        Index("ix_documentreference_projection_last_updated", "last_updated"),
    )


# --- Composition -------------------------------------------------------------


class CompositionProjectionSchema(BaseProjection):
    __tablename__ = "composition_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    encounter_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    type_code: Mapped[str | None] = mapped_column(String(64))
    doc_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_composition_projection_subject", "subject_reference"),
        Index("ix_composition_projection_encounter", "encounter_reference"),
        Index("ix_composition_projection_status", "status"),
        Index("ix_composition_projection_type", "type_code"),
        Index("ix_composition_projection_doc_date", "doc_date"),
        Index("ix_composition_projection_last_updated", "last_updated"),
    )


# --- Coverage ----------------------------------------------------------------


class CoverageProjectionSchema(BaseProjection):
    __tablename__ = "coverage_projection"

    beneficiary_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("ix_coverage_projection_beneficiary", "beneficiary_reference"),
        Index("ix_coverage_projection_status", "status"),
        Index("ix_coverage_projection_last_updated", "last_updated"),
    )


# --- Claim -------------------------------------------------------------------


class ClaimProjectionSchema(BaseProjection):
    __tablename__ = "claim_projection"

    patient_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_claim_projection_patient", "patient_reference"),
        Index("ix_claim_projection_status", "status"),
        Index("ix_claim_projection_created_at", "created_at"),
        Index("ix_claim_projection_last_updated", "last_updated"),
    )


# --- Communication -----------------------------------------------------------


class CommunicationProjectionSchema(BaseProjection):
    __tablename__ = "communication_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_communication_projection_subject", "subject_reference"),
        Index("ix_communication_projection_status", "status"),
        Index("ix_communication_projection_sent_at", "sent_at"),
        Index("ix_communication_projection_last_updated", "last_updated"),
    )


# --- CommunicationRequest ----------------------------------------------------


class CommunicationRequestProjectionSchema(BaseProjection):
    __tablename__ = "communicationrequest_projection"

    subject_reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    authored_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_communicationrequest_projection_subject", "subject_reference"),
        Index("ix_communicationrequest_projection_status", "status"),
        Index("ix_communicationrequest_projection_authored_on", "authored_on"),
        Index("ix_communicationrequest_projection_last_updated", "last_updated"),
    )


# --- AuditEvent --------------------------------------------------------------


class AuditEventProjectionSchema(BaseProjection):
    """Search projection for FHIR AuditEvent.

    Covers the four most common audit queries: by date range, by action
    code (C/R/U/D/E), by agent username, and by the affected resource.
    """

    __tablename__ = "auditevent_projection"

    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    action: Mapped[str | None] = mapped_column(String(1))
    agent_username: Mapped[str | None] = mapped_column(String(128))
    entity_reference: Mapped[str | None] = mapped_column(String(256))

    __table_args__ = (
        Index("ix_auditevent_projection_recorded_at", "recorded_at"),
        Index("ix_auditevent_projection_action", "action"),
        Index("ix_auditevent_projection_agent", "agent_username"),
        Index("ix_auditevent_projection_entity", "entity_reference"),
        Index("ix_auditevent_projection_last_updated", "last_updated"),
    )
