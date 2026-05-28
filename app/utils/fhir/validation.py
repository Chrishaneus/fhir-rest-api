"""Resource-type and request-body validation helpers."""

from __future__ import annotations

from typing import Any

from app.config import FHIR_RESOURCE_TYPES
from app.utils.errors import FHIRHTTPError
from app.utils.fhir.constants import FHIR_ID_PATTERN, RESERVED_TYPE_NAMES
from app.utils.fhir.fhir_models import (
    FHIR_R5_RESOURCE_TYPES,
    is_known_resource_type,
    validate_fhir_resource,
)


def configured_resource_types() -> set[str]:
    """Return the set of resource types advertised in the CapabilityStatement.

    Defaults to every FHIR R5 resource type. Set `FHIR_RESOURCE_TYPES` to a
    comma-separated list to restrict the advertised set; unknown names are
    silently dropped.
    """
    if not FHIR_RESOURCE_TYPES:
        return set(FHIR_R5_RESOURCE_TYPES)
    return {
        item.strip()
        for item in FHIR_RESOURCE_TYPES.split(",")
        if item.strip() and is_known_resource_type(item.strip())
    }


def assert_resource_type(resource_type: str) -> None:
    if resource_type in RESERVED_TYPE_NAMES or not is_known_resource_type(resource_type):
        raise FHIRHTTPError(
            404,
            f"Unsupported FHIR resource type: {resource_type}",
            "not-supported",
        )


def assert_resource_id(resource_id: str) -> None:
    if not FHIR_ID_PATTERN.fullmatch(resource_id):
        raise FHIRHTTPError(400, f"Invalid FHIR logical id: {resource_id}", "invalid")


def validate_request_body(
    resource_type: str,
    payload: dict[str, Any],
    *,
    require_id: str | None = None,
) -> dict[str, Any]:
    """Validate a request body against `fhir.resources` for `resource_type`.

    * Ensures the body is a JSON object.
    * Ensures `resourceType` in the body, if present, matches the URL.
    * For updates, ensures `id` in the body matches the URL id.
    * Delegates structural validation to :func:`validate_fhir_resource`.
    """
    if not isinstance(payload, dict):
        raise FHIRHTTPError(400, "Request body must be a JSON object", "structure")

    body_resource_type = payload.get("resourceType")
    if body_resource_type is not None and body_resource_type != resource_type:
        raise FHIRHTTPError(
            400,
            f"Body resourceType must be {resource_type}",
            "invalid",
        )

    if require_id is not None and payload.get("id") != require_id:
        raise FHIRHTTPError(
            400,
            "Body id must be present and match the id in the URL",
            "invalid",
        )

    return validate_fhir_resource(resource_type, payload)
