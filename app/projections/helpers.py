"""Shared extractors for pulling projection columns out of FHIR JSON.

Projections are deliberately conservative: when a FHIR field is multi-valued
we project only the first entry. Real EHR data overwhelmingly puts the
"official" entry first, and a single-row projection keeps reads cheap. The
README documents the limitation alongside Phase B.

Every helper here returns ``None`` when the requested field is missing or
malformed, never raises. The point is to keep the write path defensive
against weirdly-shaped resources (faker output, partial migrations, etc.)
without making the store transaction die over a typo in a single record.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def first(seq: Any) -> Any | None:
    """Return ``seq[0]`` if ``seq`` is a non-empty list, else ``None``."""
    if isinstance(seq, list) and seq:
        return seq[0]
    return None


def first_name_part(resource: dict[str, Any], part: str) -> str | None:
    """Extract the first ``HumanName``'s family or first given name.

    ``part`` is ``"family"`` or ``"given"``. ``given`` is itself a list so we
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


def first_coding(cc: Any) -> tuple[str | None, str | None]:
    """Return ``(system, code)`` for the first coding in a CodeableConcept."""
    if not isinstance(cc, dict):
        return None, None
    coding = first(cc.get("coding"))
    if not isinstance(coding, dict):
        return None, None
    system = coding.get("system")
    code = coding.get("code")
    return (
        system if isinstance(system, str) else None,
        code if isinstance(code, str) else None,
    )


def first_code(cc: Any) -> str | None:
    """Shortcut for ``first_coding`` when only the ``code`` is needed."""
    return first_coding(cc)[1]


def reference_of(ref: Any) -> str | None:
    """Return the ``"reference"`` string from a Reference object."""
    if not isinstance(ref, dict):
        return None
    value = ref.get("reference")
    return value if isinstance(value, str) else None


def codeable_reference(value: Any) -> str | None:
    """Extract the ``reference`` from a R5 ``CodeableReference``.

    R5 CodeableReference can hold either a ``reference`` or a ``concept``
    (CodeableConcept). We only project the reference half here; if the entry
    is concept-only, return ``None`` so the projection column stays NULL
    (which is the truthful answer).
    """
    if not isinstance(value, dict):
        return None
    return reference_of(value.get("reference"))


def parse_fhir_datetime(value: Any) -> datetime | None:
    """Parse a FHIR ``instant`` / ``dateTime`` string into a Python ``datetime``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_fhir_date(value: Any) -> date | None:
    """Parse a FHIR ``date`` (``YYYY-MM-DD``) into a Python ``date``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        # Tolerate full datetimes here too; downstream column is DATE.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None


def parse_decimal(value: Any) -> Decimal | None:
    """Parse a numeric value (FHIR ``decimal`` / ``integer``) into a Decimal."""
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
    """FHIR ``effective[x]`` collapses to a single timestamp here.

    Strategy: prefer ``effectiveDateTime`` (instant), fall back to the start
    of ``effectivePeriod`` (range), then ``effectiveInstant``.
    """
    dt = parse_fhir_datetime(resource.get("effectiveDateTime"))
    if dt is not None:
        return dt
    dt = period_start(resource.get("effectivePeriod"))
    if dt is not None:
        return dt
    return parse_fhir_datetime(resource.get("effectiveInstant"))


def performed_datetime(resource: dict[str, Any]) -> datetime | None:
    """Procedure / similar ``performed[x]`` -> a single timestamp."""
    dt = parse_fhir_datetime(resource.get("performedDateTime"))
    if dt is not None:
        return dt
    return period_start(resource.get("performedPeriod"))


def quantity_parts(quantity: Any) -> tuple[Decimal | None, str | None]:
    """Return ``(value, unit)`` from a FHIR ``Quantity``."""
    if not isinstance(quantity, dict):
        return None, None
    value = parse_decimal(quantity.get("value"))
    unit = quantity.get("unit") or quantity.get("code")
    return value, unit if isinstance(unit, str) else None
