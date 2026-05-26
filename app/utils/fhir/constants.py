"""Static FHIR HTTP layer constants and patterns."""

from __future__ import annotations

import re

FHIR_JSON = "application/fhir+json"
FHIR_VERSION = "5.0.0"

_FHIR_ID_REGEX = r"^[A-Za-z0-9\-.]{1,64}$"
_RESOURCE_TYPE_REGEX = r"^[A-Z][A-Za-z0-9]{0,63}$"

FHIR_ID_PATTERN = re.compile(_FHIR_ID_REGEX)
RESOURCE_TYPE_PATTERN = re.compile(_RESOURCE_TYPE_REGEX)

RESERVED_TYPE_NAMES = {"metadata", "_history", "_search"}

DEFAULT_RESOURCE_TYPES = (
    "Patient",
    "Observation",
    "Encounter",
    "Condition",
    "Practitioner",
    "Organization",
    "MedicationRequest",
    "DiagnosticReport",
    "AllergyIntolerance",
    "Procedure",
)

SUPPORTED_INTERACTIONS = (
    "read",
    "vread",
    "update",
    "delete",
    "history-instance",
    "create",
    "search-type",
    "history-type",
)

IGNORED_SEARCH_PARAMS = {
    "_format",
    "_pretty",
    "_summary",
    "_elements",
    "_sort",
    "_include",
    "_revinclude",
    "_contained",
    "_containedType",
    "_offset",
    # ``_count`` is a pagination control, not a filter -- listing it here
    # keeps it from disqualifying the projection fast path in
    # ``Projection.supports`` and from being treated as an unknown filter
    # in the JSONB containment loop.
    "_count",
}
