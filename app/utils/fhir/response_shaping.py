"""FHIR response shaping: _summary and _elements post-processing.

FHIR R5 §3.3.1.1 (_summary) and §3.3.1.2 (_elements).
Applied to resource dicts BEFORE serialisation; never persisted.
"""

from __future__ import annotations

import copy
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
    if not any(t.get("code") == "SUBSETTED" for t in tags):
        tags = [*tags, _SUBSETTED_TAG]
    meta["tag"] = tags
    resource["meta"] = meta
    return resource


def apply_summary(resource: dict[str, Any], value: str) -> dict[str, Any]:
    """Shape *resource* according to a single ``_summary`` value.

    ``count`` is a bundle-level concern; ignored here.
    """
    v = value.strip().lower()
    if v in ("false", "", "count"):
        return resource
    if v == "data":
        shaped = {k: val for k, val in resource.items() if k != "text"}
        return _tag_subsetted(shaped)
    # "true" or "text": keep mandatory fields + text narrative only
    shaped = {k: val for k, val in resource.items() if k in _ALWAYS_KEEP or k == "text"}
    return _tag_subsetted(shaped)


def apply_elements(resource: dict[str, Any], elements: list[str]) -> dict[str, Any]:
    """Shape *resource* to only the requested top-level elements (plus mandatory ones)."""
    keep = _ALWAYS_KEEP | frozenset(elements)
    shaped = {k: val for k, val in resource.items() if k in keep}
    return _tag_subsetted(shaped)


def shape_resource(resource: dict[str, Any], params: dict[str, list[str]]) -> dict[str, Any]:
    """Apply ``_summary`` and ``_elements`` shaping to a single resource dict."""
    summary_vals = params.get("_summary", [])
    elements_vals = params.get("_elements", [])

    if summary_vals:
        resource = apply_summary(resource, summary_vals[-1])

    if elements_vals:
        elements: list[str] = []
        for v in elements_vals:
            elements.extend(e.strip() for e in v.split(",") if e.strip())
        if elements:
            resource = apply_elements(resource, elements)

    return resource


def is_count_only(params: dict[str, list[str]]) -> bool:
    """Return True when ``_summary=count`` is active (bundle entries must be omitted)."""
    vals = params.get("_summary", [])
    return bool(vals) and vals[-1].strip().lower() == "count"


def shape_bundle(bundle: dict[str, Any], params: dict[str, list[str]]) -> dict[str, Any]:
    """Apply ``_summary`` / ``_elements`` shaping to all resources inside a bundle.

    For ``_summary=count`` the entries are stripped; only ``total`` is kept.
    """
    if is_count_only(params):
        return {k: v for k, v in bundle.items() if k != "entry"}

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
