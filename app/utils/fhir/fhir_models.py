"""Bridge between ``fhir.resources`` Pydantic models and our REST layer.

This module is the single place that knows which FHIR R5 resource types exist
and how to validate them. The validator runs the request payload through the
``fhir.resources`` Pydantic model for the relevant resource type, raising a
:class:`FHIRHTTPError` (with an ``OperationOutcome``-shaped body) on failure.

The resource-type list mirrors the concrete Resource subclasses listed at
https://build.fhir.org/resourcelist.html for FHIR R5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fhir.resources import get_fhir_model_class as _get_fhir_model_class
from pydantic import ValidationError

if TYPE_CHECKING:
    from fhir_core.fhirabstractmodel import FHIRAbstractModel

from app.utils.errors import FHIRHTTPError

FHIR_R5_RESOURCE_TYPES: tuple[str, ...] = (
    "Account",
    "ActivityDefinition",
    "ActorDefinition",
    "AdministrableProductDefinition",
    "AdverseEvent",
    "AllergyIntolerance",
    "Appointment",
    "AppointmentResponse",
    "ArtifactAssessment",
    "AuditEvent",
    "Basic",
    "Binary",
    "BiologicallyDerivedProduct",
    "BiologicallyDerivedProductDispense",
    "BodyStructure",
    "Bundle",
    "CapabilityStatement",
    "CarePlan",
    "CareTeam",
    "ChargeItem",
    "ChargeItemDefinition",
    "Citation",
    "Claim",
    "ClaimResponse",
    "ClinicalImpression",
    "ClinicalUseDefinition",
    "CodeSystem",
    "Communication",
    "CommunicationRequest",
    "CompartmentDefinition",
    "Composition",
    "ConceptMap",
    "Condition",
    "ConditionDefinition",
    "Consent",
    "Contract",
    "Coverage",
    "CoverageEligibilityRequest",
    "CoverageEligibilityResponse",
    "DetectedIssue",
    "Device",
    "DeviceAssociation",
    "DeviceDefinition",
    "DeviceDispense",
    "DeviceMetric",
    "DeviceRequest",
    "DeviceUsage",
    "DiagnosticReport",
    "DocumentReference",
    "Encounter",
    "EncounterHistory",
    "Endpoint",
    "EnrollmentRequest",
    "EnrollmentResponse",
    "EpisodeOfCare",
    "EventDefinition",
    "Evidence",
    "EvidenceReport",
    "EvidenceVariable",
    "ExampleScenario",
    "ExplanationOfBenefit",
    "FamilyMemberHistory",
    "Flag",
    "FormularyItem",
    "GenomicStudy",
    "Goal",
    "GraphDefinition",
    "Group",
    "GuidanceResponse",
    "HealthcareService",
    "ImagingSelection",
    "ImagingStudy",
    "Immunization",
    "ImmunizationEvaluation",
    "ImmunizationRecommendation",
    "ImplementationGuide",
    "Ingredient",
    "InsurancePlan",
    "InventoryItem",
    "InventoryReport",
    "Invoice",
    "Library",
    "Linkage",
    "List",
    "Location",
    "ManufacturedItemDefinition",
    "Measure",
    "MeasureReport",
    "Medication",
    "MedicationAdministration",
    "MedicationDispense",
    "MedicationKnowledge",
    "MedicationRequest",
    "MedicationStatement",
    "MedicinalProductDefinition",
    "MessageDefinition",
    "MessageHeader",
    "MolecularSequence",
    "NamingSystem",
    "NutritionIntake",
    "NutritionOrder",
    "NutritionProduct",
    "Observation",
    "ObservationDefinition",
    "OperationDefinition",
    "OperationOutcome",
    "Organization",
    "OrganizationAffiliation",
    "PackagedProductDefinition",
    "Parameters",
    "Patient",
    "PaymentNotice",
    "PaymentReconciliation",
    "Permission",
    "Person",
    "PlanDefinition",
    "Practitioner",
    "PractitionerRole",
    "Procedure",
    "Provenance",
    "Questionnaire",
    "QuestionnaireResponse",
    "RegulatedAuthorization",
    "RelatedPerson",
    "RequestOrchestration",
    "Requirements",
    "ResearchStudy",
    "ResearchSubject",
    "RiskAssessment",
    "Schedule",
    "SearchParameter",
    "ServiceRequest",
    "Slot",
    "Specimen",
    "SpecimenDefinition",
    "StructureDefinition",
    "StructureMap",
    "Subscription",
    "SubscriptionStatus",
    "SubscriptionTopic",
    "Substance",
    "SubstanceDefinition",
    "SubstanceNucleicAcid",
    "SubstancePolymer",
    "SubstanceProtein",
    "SubstanceReferenceInformation",
    "SubstanceSourceMaterial",
    "SupplyDelivery",
    "SupplyRequest",
    "Task",
    "TerminologyCapabilities",
    "TestPlan",
    "TestReport",
    "TestScript",
    "Transport",
    "ValueSet",
    "VerificationResult",
    "VisionPrescription",
)


KNOWN_RESOURCE_TYPES: frozenset[str] = frozenset(FHIR_R5_RESOURCE_TYPES)


def is_known_resource_type(resource_type: str) -> bool:
    return resource_type in KNOWN_RESOURCE_TYPES


def get_fhir_resource_class(resource_type: str) -> type[FHIRAbstractModel] | None:
    """Return the ``fhir.resources`` Pydantic model class for a resource type."""
    if not is_known_resource_type(resource_type):
        return None
    try:
        return _get_fhir_model_class(resource_type)
    except (ValueError, KeyError):
        return None


def validate_fhir_resource(resource_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate ``payload`` against the FHIR R5 Pydantic model for ``resource_type``.

    Returns the model-dumped dict (after Pydantic normalization). Raises
    :class:`FHIRHTTPError` with a FHIR-shaped OperationOutcome status code when
    validation fails.
    """
    model_class = get_fhir_resource_class(resource_type)
    if model_class is None:
        raise FHIRHTTPError(
            404,
            f"Unsupported FHIR resource type: {resource_type}",
            "not-supported",
        )

    data = {**payload, "resourceType": resource_type}
    try:
        model = model_class.model_validate(data)
    except ValidationError as exc:
        raise FHIRHTTPError(
            422,
            f"FHIR validation failed: {_format_pydantic_errors(exc)}",
            "invariant",
        ) from exc
    # mode="json" converts FHIR primitive types (date, dateTime, instant, ...) to
    # JSON-safe strings so the result can be persisted via SQLAlchemy's JSON column.
    return model.model_dump(exclude_none=True, mode="json")


def _format_pydantic_errors(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) if parts else str(exc)
