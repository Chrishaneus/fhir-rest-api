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
    "patch",
    "delete",
    "history-instance",
    "create",
    "search-type",
    "history-type",
)

# All underscore-prefixed parameters the server actively handles.  Anything
# starting with `_` that is NOT in this set is considered unknown and will
# trigger a 400 when `Prefer: handling=strict` is requested.
KNOWN_SEARCH_CONTROL_PARAMS: frozenset[str] = frozenset(
    {
        "_id",    # logical id filter
        "_type",  # system-search resource-type filter
    }
)

IGNORED_SEARCH_PARAMS = {
    "_format",
    "_pretty",
    "_summary",
    "_elements",
    "_sort",
    "_include",
    "_revinclude",
    # Iterate variants are separate query-string keys parsed by parse_include_specs;
    # they must be here so strict-mode handling does not flag them as unknown.
    "_include:iterate",
    "_revinclude:iterate",
    "_contained",
    "_containedType",
    "_offset",
    # `_count` is a pagination control, not a filter -- listing it here
    # keeps it from disqualifying the projection fast path in
    # `Projection.supports` and from being treated as an unknown filter
    # in the JSONB containment loop.
    "_count",
    # `_lastUpdated` is handled by dedicated column-level filters in
    # `_sql_search` and `_python_search` (and by `_range_clause` in
    # the projection path), so it must be excluded from the generic JSONB
    # containment loop which cannot apply prefix semantics or period expansion.
    "_lastUpdated",
    # `_total` controls whether the bundle total field is included; it is not
    # a resource field filter and must not reach the search matching logic.
    "_total",
}
