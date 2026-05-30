"""FHIR response shaping: _summary and _elements post-processing.

FHIR R5 §3.3.1.1 (_summary) and §3.3.1.2 (_elements).
Applied to resource dicts BEFORE serialisation; never persisted.
"""

from __future__ import annotations

from typing import Any

_ALWAYS_KEEP = frozenset({"id", "meta", "resourceType"})

_SUBSETTED_TAG = {
    "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationValue",
    "code": "SUBSETTED",
}


def _tag_subsetted(resource: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of *resource* with the SUBSETTED meta tag added."""
    resource = dict(resource)
    meta = dict(resource.get("meta") or {})
    tags = list(meta.get("tag") or [])
    if not any(tag.get("code") == "SUBSETTED" for tag in tags):
        tags = [*tags, _SUBSETTED_TAG]
    meta["tag"] = tags
    resource["meta"] = meta
    return resource


def apply_summary(resource: dict[str, Any], value: str) -> dict[str, Any]:
    """Shape *resource* according to a single `_summary` value.

    `count` is a bundle-level concern; ignored here.
    """
    summary_mode = value.strip().lower()
    if summary_mode in ("false", "", "count"):
        return resource
    if summary_mode == "data":
        shaped = {key: field_value for key, field_value in resource.items() if key != "text"}
        return _tag_subsetted(shaped)
    # "true" or "text": keep mandatory fields + text narrative only
    shaped = {key: field_value for key, field_value in resource.items() if key in _ALWAYS_KEEP or key == "text"}
    return _tag_subsetted(shaped)


def apply_elements(resource: dict[str, Any], elements: list[str]) -> dict[str, Any]:
    """Shape *resource* to only the requested top-level elements (plus mandatory ones)."""
    keep = _ALWAYS_KEEP | frozenset(elements)
    shaped = {key: field_value for key, field_value in resource.items() if key in keep}
    return _tag_subsetted(shaped)


def shape_resource(resource: dict[str, Any], params: dict[str, list[str]]) -> dict[str, Any]:
    """Apply `_summary` and `_elements` shaping to a single resource dict."""
    summary_values = params.get("_summary", [])
    elements_values = params.get("_elements", [])

    if summary_values:
        resource = apply_summary(resource, summary_values[-1])

    if elements_values:
        elements: list[str] = []
        for raw_element in elements_values:
            elements.extend(element.strip() for element in raw_element.split(",") if element.strip())
        if elements:
            resource = apply_elements(resource, elements)

    return resource


def is_count_only(params: dict[str, list[str]]) -> bool:
    """Return True when `_summary=count` is active (bundle entries must be omitted)."""
    summary_values = params.get("_summary", [])
    return bool(summary_values) and summary_values[-1].strip().lower() == "count"


def shape_bundle(bundle: dict[str, Any], params: dict[str, list[str]]) -> dict[str, Any]:
    """Apply `_summary` / `_elements` shaping to all resources inside a bundle.

    For `_summary=count` the entries are stripped; only `total` is kept.
    """
    if is_count_only(params):
        return {key: bundle_value for key, bundle_value in bundle.items() if key != "entry"}

    entries = bundle.get("entry", [])
    shaped_entries: list[dict[str, Any]] = []
    for entry in entries:
        if "resource" in entry:
            entry = dict(entry)
            entry["resource"] = shape_resource(entry["resource"], params)
        shaped_entries.append(entry)

    result = dict(bundle)
    result["entry"] = shaped_entries
    return result
