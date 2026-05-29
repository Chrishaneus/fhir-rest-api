"""Shared extractors for pulling projection columns out of FHIR JSON.

Projections are deliberately conservative: when a FHIR field is multi-valued
we project only the first entry. Real EHR data overwhelmingly puts the
"official" entry first, and a single-row projection keeps reads cheap. The
README documents the limitation alongside Phase B.

Every helper here returns `None` when the requested field is missing or
malformed, never raises. The point is to keep the write path defensive
against weirdly-shaped resources (faker output, partial migrations, etc.)
without making the store transaction die over a typo in a single record.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


def first(seq: Any) -> Any | None:
    """Return `seq[0]` if `seq` is a non-empty list, else `None`."""
    if isinstance(seq, list) and seq:
        return seq[0]
    return None


def first_name_part(resource: dict[str, Any], part: str) -> str | None:
    """Extract the first `HumanName`'s family or first given name.

    `part` is `"family"` or `"given"`. `given` is itself a list so we
    return its first entry.
    """
    name = first(resource.get("name"))
    if not isinstance(name, dict):
        return None
    if part == "family":
        family = name.get("family")
        return family if isinstance(family, str) else None
    if part == "given":
        given = first(name.get("given"))
        return given if isinstance(given, str) else None
    return None


def first_coding(codeable_concept: Any) -> tuple[str | None, str | None]:
    """Return `(system, code)` for the first coding in a CodeableConcept."""
    if not isinstance(codeable_concept, dict):
        return None, None
    coding = first(codeable_concept.get("coding"))
    if not isinstance(coding, dict):
        return None, None
    system = coding.get("system")
    code = coding.get("code")
    return (
        system if isinstance(system, str) else None,
        code if isinstance(code, str) else None,
    )


def first_code(codeable_concept: Any) -> str | None:
    """Shortcut for `first_coding` when only the `code` is needed."""
    return first_coding(codeable_concept)[1]


def reference_of(value: Any) -> str | None:
    """Return the `"reference"` string from a Reference object."""
    if not isinstance(value, dict):
        return None
    reference = value.get("reference")
    return reference if isinstance(reference, str) else None


def codeable_reference(value: Any) -> str | None:
    """Extract the `reference` from a R5 `CodeableReference`.

    R5 CodeableReference can hold either a `reference` or a `concept`
    (CodeableConcept). We only project the reference half here; if the entry
    is concept-only, return `None` so the projection column stays NULL
    (which is the truthful answer).
    """
    if not isinstance(value, dict):
        return None
    return reference_of(value.get("reference"))


def _expand_partial_date(value: str) -> tuple[int, int, int] | None:
    """Parse FHIR partial dates (YYYY or YYYY-MM) to a (year, month, day) triple.

    Returns ``None`` for strings that are not a recognisable partial date.
    Full dates (YYYY-MM-DD) and datetimes are handled by the callers directly.
    """
    parts = value.split("-")
    try:
        if len(parts) == 1:
            return int(parts[0]), 1, 1
        if len(parts) == 2:
            return int(parts[0]), int(parts[1]), 1
    except ValueError:
        pass
    return None


def parse_fhir_datetime(value: Any) -> datetime | None:
    """Parse a FHIR ``instant`` / ``dateTime`` string into a Python ``datetime``.

    Full ISO strings (with time component) are parsed directly.  FHIR R5
    allows partial ``dateTime`` values (``YYYY`` or ``YYYY-MM``); these are
    expanded to the start of their implied period at UTC midnight.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    triple = _expand_partial_date(value)
    if triple is not None:
        return datetime(*triple, tzinfo=timezone.utc)
    return None


def parse_fhir_date(value: Any) -> date | None:
    """Parse a FHIR ``date`` string into a Python ``date``.

    Full ``YYYY-MM-DD`` strings and ISO datetimes are parsed directly.
    Partial dates (``YYYY`` or ``YYYY-MM``) are expanded to the first day of
    their implied period so they are stored and sortable rather than dropped.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    triple = _expand_partial_date(value)
    if triple is not None:
        return date(*triple)
    return None


def parse_decimal(value: Any) -> Decimal | None:
    """Parse a numeric value (FHIR `decimal` / `integer`) into a Decimal."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def period_start(period: Any) -> datetime | None:
    if not isinstance(period, dict):
        return None
    return parse_fhir_datetime(period.get("start"))


def period_end(period: Any) -> datetime | None:
    if not isinstance(period, dict):
        return None
    return parse_fhir_datetime(period.get("end"))


def effective_datetime(resource: dict[str, Any]) -> datetime | None:
    """FHIR `effective[x]` collapses to a single timestamp here.

    Strategy: prefer `effectiveDateTime` (instant), fall back to the start
    of `effectivePeriod` (range), then `effectiveInstant`.
    """
    result = parse_fhir_datetime(resource.get("effectiveDateTime"))
    if result is not None:
        return result
    result = period_start(resource.get("effectivePeriod"))
    if result is not None:
        return result
    return parse_fhir_datetime(resource.get("effectiveInstant"))


def performed_datetime(resource: dict[str, Any]) -> datetime | None:
    """Procedure / similar `performed[x]` -> a single timestamp."""
    result = parse_fhir_datetime(resource.get("performedDateTime"))
    if result is not None:
        return result
    return period_start(resource.get("performedPeriod"))


def quantity_parts(quantity: Any) -> tuple[Decimal | None, str | None]:
    """Return `(value, unit)` from a FHIR `Quantity`."""
    if not isinstance(quantity, dict):
        return None, None
    value = parse_decimal(quantity.get("value"))
    unit = quantity.get("unit") or quantity.get("code")
    return value, unit if isinstance(unit, str) else None
