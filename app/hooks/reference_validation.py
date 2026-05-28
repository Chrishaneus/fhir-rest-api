"""Reference validation hooks for resource types with outbound Reference fields.

When a resource is created or updated, any `Reference.reference` values that
follow the `ResourceType/id` pattern are resolved against the local store.
If the target does not exist (or has been deleted), a 422 is returned before
the write is committed.

Absolute URLs (`https://...`) and URNs (`urn:...`) are silently skipped
because those reference external servers that this store cannot reach.
"""

from __future__ import annotations

from typing import Any

from app.hooks import ResourceHooks, hooks
from app.utils.errors import FHIRHTTPError


def _parse_local_reference(ref: str) -> tuple[str, str] | None:
    """Return `(ResourceType, id)` for a relative `ResourceType/id` reference.

    Returns `None` for absolute URLs, URNs, contained references, or anything
    that cannot be resolved against the local store.
    """
    if not ref or ref.startswith(("http://", "https://", "urn:", "#")):
        return None
    if "/" in ref:
        parts = ref.split("/", 1)
        if parts[0] and parts[1]:
            return parts[0], parts[1]
    return None


def _check_reference(resource: dict[str, Any], field: str) -> None:
    """Raise 422 if `resource[field]` is a local reference that does not exist."""
    value = resource.get(field)
    if not isinstance(value, dict):
        return
    reference_str = value.get("reference")
    if not isinstance(reference_str, str) or not reference_str:
        return
    parsed = _parse_local_reference(reference_str)
    if parsed is None:
        return
    reference_type, reference_id = parsed
    from app.store import store  # local import avoids module-load-time circularity

    result = store.latest(reference_type, reference_id)
    if result is None or result.deleted:
        raise FHIRHTTPError(
            422,
            f"Referenced resource {reference_str!r} does not exist",
            "not-found",
        )


class ReferenceValidatingHooks(ResourceHooks):
    """Validate that named Reference fields resolve to existing local resources.

    Pass the JSON field names that should be checked.  Fields that are absent,
    hold no `reference` string, or whose reference is an absolute URL are
    silently skipped — only `ResourceType/id` local references are checked.
    """

    def __init__(self, *field_names: str) -> None:
        self._fields = field_names

    def _validate(self, resource: dict[str, Any]) -> None:
        for field in self._fields:
            _check_reference(resource, field)

    def before_create(self, resource: dict[str, Any]) -> dict[str, Any]:
        self._validate(resource)
        return resource

    def before_update(
        self,
        old: dict[str, Any] | None,
        new: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate(new)
        return new


# ---------------------------------------------------------------------------
# Per-type registrations
# Field names are the JSON keys in the FHIR resource body, not search params.
# ---------------------------------------------------------------------------

hooks.register("AllergyIntolerance", ReferenceValidatingHooks("patient"))
hooks.register("Condition",          ReferenceValidatingHooks("subject", "encounter"))
hooks.register("DiagnosticReport",   ReferenceValidatingHooks("subject", "encounter"))
hooks.register("Encounter",          ReferenceValidatingHooks("subject"))
hooks.register("MedicationRequest",  ReferenceValidatingHooks("subject", "requester"))
hooks.register("Observation",        ReferenceValidatingHooks("subject", "encounter"))
hooks.register("Procedure",          ReferenceValidatingHooks("subject", "encounter"))
