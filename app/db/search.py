"""Translate FHIR search parameters into JSONB containment payloads.

This module is the bridge between FHIR-flavoured query strings like
``?family=Smith&gender=male`` and the Postgres ``content @> '...'::jsonb``
operator that uses our GIN index.

Strategy
--------
Each search parameter maps to one or more *containment templates* — small
dict shapes where the literal ``"{value}"`` placeholder gets substituted with
the search value before being JSON-encoded and pushed to Postgres. ``@>``
matches when the right-hand JSON is a subtree of the left, which is exactly
the FHIR "match if this value appears" semantics we want for token-style
parameters.

This is intentionally a *curated* mapping rather than a full FHIR SearchParameter
engine. It covers the common patterns (token/string, simple references) for
the resource types most consumers care about. Unknown parameters are dropped
on the floor (FHIR ``Prefer: handling=lenient`` default), which is preferable
to falling back to a full table scan.
"""

from __future__ import annotations

from typing import Any

# Each entry is a list of containment templates. Order doesn't matter; we OR them.
SEARCH_PARAM_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    # --- Patient ---
    "family": [{"name": [{"family": "{value}"}]}],
    "given": [{"name": [{"given": ["{value}"]}]}],
    "name": [
        {"name": [{"family": "{value}"}]},
        {"name": [{"given": ["{value}"]}]},
        {"name": [{"text": "{value}"}]},
    ],
    "gender": [{"gender": "{value}"}],
    "birthdate": [{"birthDate": "{value}"}],
    # --- Common contact ---
    "telecom": [{"telecom": [{"value": "{value}"}]}],
    "email": [{"telecom": [{"system": "email", "value": "{value}"}]}],
    "phone": [{"telecom": [{"system": "phone", "value": "{value}"}]}],
    # --- Identifiers ---
    "identifier": [{"identifier": [{"value": "{value}"}]}],
    # --- References ---
    "subject": [
        {"subject": {"reference": "{value}"}},
        {"subject": {"reference": "Patient/{value}"}},
    ],
    "patient": [
        {"subject": {"reference": "Patient/{value}"}},
        {"subject": {"reference": "{value}"}},
    ],
    "encounter": [
        {"encounter": {"reference": "{value}"}},
        {"encounter": {"reference": "Encounter/{value}"}},
    ],
    "performer": [
        {"performer": [{"reference": "{value}"}]},
        {"performer": [{"reference": "Practitioner/{value}"}]},
    ],
    # --- Observation / DiagnosticReport / Condition ---
    "status": [{"status": "{value}"}],
    "code": [
        {"code": {"coding": [{"code": "{value}"}]}},
        {"code": {"text": "{value}"}},
    ],
    "category": [
        {"category": [{"coding": [{"code": "{value}"}]}]},
        {"category": [{"text": "{value}"}]},
    ],
    # --- Organization / Practitioner ---
    "active": [{"active": True}, {"active": False}],
    "organization": [
        {"managingOrganization": {"reference": "{value}"}},
        {"managingOrganization": {"reference": "Organization/{value}"}},
    ],
}


def is_supported(param: str) -> bool:
    return param.lower() in SEARCH_PARAM_TEMPLATES


def _render(template: Any, value: str) -> Any:
    """Recursively substitute ``"{value}"`` placeholders inside a template."""
    if isinstance(template, dict):
        return {key: _render(template_value, value) for key, template_value in template.items()}
    if isinstance(template, list):
        return [_render(item, value) for item in template]
    if isinstance(template, str) and template == "{value}":
        return value
    return template


def containment_payloads(param: str, values: list[str]) -> list[dict[str, Any]]:
    """Return a list of Python-dict containment payloads to OR together.

    Each payload is meant to be bound into ``content @> :payload`` against a
    JSONB column; SQLAlchemy's JSONB type will serialize the dict for us, so
    we deliberately do *not* json.dumps here (double-encoding would wrap the
    whole thing in quotes and break containment).

    Returns an empty list if the parameter isn't in the curated registry; the
    caller should treat that as "ignore this filter" (lenient handling).
    """
    templates = SEARCH_PARAM_TEMPLATES.get(param.lower())
    if not templates:
        return []
    if param.lower() == "active":
        # `active` is a boolean; pick the matching template per value
        payloads: list[dict[str, Any]] = []
        for value in values:
            truthy = value.lower() in {"true", "1", "yes"}
            payloads.append({"active": truthy})
        return payloads
    payloads = []
    for value in values:
        for template in templates:
            payloads.append(_render(template, value))
    return payloads
