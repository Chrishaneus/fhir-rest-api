"""FHIR R5 compartment definitions.

A compartment groups resources that share a common reference to a subject
resource (Patient, Practitioner, Encounter, RelatedPerson, or Device). The
route `GET /{CompartmentType}/{id}/{ResourceType}` returns all resources of
the given type that belong to that compartment.

Each compartment entry lists the search parameter names that constitute
membership — a resource belongs to the compartment if any of the listed
params references the compartment subject. Parameters are ordered with the
most direct/common reference first so callers can take `params[0]` as the
primary filter.

Reference: https://hl7.org/fhir/R5/compartmentdefinition.html
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Patient compartment
# ---------------------------------------------------------------------------

_PATIENT: dict[str, list[str]] = {
    "AllergyIntolerance": ["patient", "recorder", "asserter"],
    "CarePlan": ["patient", "subject", "performer"],
    "CareTeam": ["patient", "subject", "participant"],
    "Condition": ["patient", "subject", "asserter"],
    "Consent": ["patient", "subject"],
    "Coverage": ["patient", "beneficiary", "subscriber", "policy-holder"],
    "DetectedIssue": ["patient", "subject"],
    "DeviceRequest": ["patient", "subject", "requester", "performer"],
    "DeviceUsage": ["patient", "subject"],
    "DiagnosticReport": ["patient", "subject", "performer"],
    "Encounter": ["patient", "subject"],
    "EpisodeOfCare": ["patient"],
    "FamilyMemberHistory": ["patient"],
    "Flag": ["patient", "subject"],
    "Goal": ["patient", "subject"],
    "GuidanceResponse": ["patient", "subject"],
    "ImagingSelection": ["patient", "subject"],
    "ImagingStudy": ["patient", "subject"],
    "Immunization": ["patient", "subject"],
    "ImmunizationEvaluation": ["patient", "subject"],
    "ImmunizationRecommendation": ["patient", "subject"],
    "MedicationAdministration": ["patient", "subject"],
    "MedicationDispense": ["patient", "subject"],
    "MedicationRequest": ["patient", "subject"],
    "MedicationStatement": ["patient", "subject"],
    "NutritionIntake": ["patient", "subject"],
    "NutritionOrder": ["patient", "subject"],
    "Observation": ["patient", "subject"],
    "Procedure": ["patient", "subject"],
    "QuestionnaireResponse": ["patient", "subject"],
    "RelatedPerson": ["patient"],
    "RequestOrchestration": ["patient", "subject"],
    "RiskAssessment": ["patient", "subject"],
    "ServiceRequest": ["patient", "subject"],
    "Specimen": ["patient", "subject"],
    "SupplyDelivery": ["patient"],
    "SupplyRequest": ["patient", "subject"],
    "Task": ["patient", "subject"],
    "VisionPrescription": ["patient"],
}

# ---------------------------------------------------------------------------
# Practitioner compartment
# ---------------------------------------------------------------------------

_PRACTITIONER: dict[str, list[str]] = {
    "AllergyIntolerance": ["recorder", "asserter"],
    "CarePlan": ["performer"],
    "CareTeam": ["participant"],
    "Condition": ["asserter"],
    "DiagnosticReport": ["performer"],
    "Encounter": ["practitioner", "participant"],
    "EpisodeOfCare": ["care-manager"],
    "Flag": ["author"],
    "Goal": ["expressed-by"],
    "ImagingStudy": ["performer"],
    "Immunization": ["performer"],
    "MedicationAdministration": ["performer"],
    "MedicationDispense": ["performer"],
    "MedicationRequest": ["requester"],
    "MedicationStatement": ["source"],
    "NutritionOrder": ["provider"],
    "Observation": ["performer"],
    "Procedure": ["performer", "recorder", "asserter"],
    "QuestionnaireResponse": ["author", "source"],
    "ServiceRequest": ["requester", "performer"],
    "SupplyRequest": ["requester"],
    "Task": ["owner"],
}

# ---------------------------------------------------------------------------
# Encounter compartment
# ---------------------------------------------------------------------------

_ENCOUNTER: dict[str, list[str]] = {
    "Condition": ["encounter"],
    "DiagnosticReport": ["encounter"],
    "MedicationRequest": ["encounter"],
    "Observation": ["encounter"],
    "Procedure": ["encounter"],
    "QuestionnaireResponse": ["encounter"],
    "ServiceRequest": ["encounter"],
    "Task": ["encounter"],
}

# ---------------------------------------------------------------------------
# RelatedPerson compartment
# ---------------------------------------------------------------------------

_RELATED_PERSON: dict[str, list[str]] = {
    "AllergyIntolerance": ["asserter"],
    "CarePlan": ["performer"],
    "CareTeam": ["participant"],
    "Condition": ["asserter"],
    "Observation": ["performer"],
    "Procedure": ["performer"],
    "QuestionnaireResponse": ["author", "source"],
    "ServiceRequest": ["performer"],
}

# ---------------------------------------------------------------------------
# Device compartment
# ---------------------------------------------------------------------------

_DEVICE: dict[str, list[str]] = {
    "DeviceRequest": ["device", "subject"],
    "DeviceUsage": ["device"],
    "DiagnosticReport": ["subject"],
    "Observation": ["device", "subject"],
    "Procedure": ["performer"],
    "ServiceRequest": ["performer", "device"],
}

# ---------------------------------------------------------------------------
# Group compartment
# ---------------------------------------------------------------------------

_GROUP: dict[str, list[str]] = {
    "CarePlan": ["subject"],
    "CareTeam": ["participant", "subject"],
    "Communication": ["subject"],
    "CommunicationRequest": ["subject"],
    "Condition": ["subject"],
    "DiagnosticReport": ["subject"],
    "DocumentReference": ["subject"],
    "Encounter": ["subject"],
    "Goal": ["subject"],
    "Group": ["member"],
    "MeasureReport": ["subject"],
    "Observation": ["subject"],
    "Procedure": ["subject"],
    "RequestOrchestration": ["subject"],
    "ResearchSubject": ["subject"],
    "ServiceRequest": ["subject"],
    "Task": ["subject"],
}

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

COMPARTMENT_PARAMS: dict[str, dict[str, list[str]]] = {
    "Patient": _PATIENT,
    "Practitioner": _PRACTITIONER,
    "Encounter": _ENCOUNTER,
    "RelatedPerson": _RELATED_PERSON,
    "Device": _DEVICE,
    "Group": _GROUP,
}

SUPPORTED_COMPARTMENTS: frozenset[str] = frozenset(COMPARTMENT_PARAMS)


def get_compartment_params(compartment_type: str, resource_type: str) -> list[str] | None:
    """Return membership search params for resource_type in compartment_type, or None.

    Returns None when either the compartment type is unknown or the resource
    type is not defined as a member of that compartment.
    """
    compartment = COMPARTMENT_PARAMS.get(compartment_type)
    if compartment is None:
        return None
    return compartment.get(resource_type)
