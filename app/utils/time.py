"""Time helpers for FHIR HTTP responses."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime


def now_utc() -> datetime:
    return datetime.now(UTC)


# Fixed at import time so the CapabilityStatement date doesn't shift per request.
STARTUP_TIME: datetime = now_utc()


def fhir_instant(value: datetime) -> str:
    """Render a UTC datetime as a FHIR `instant`."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def http_date(value: datetime) -> str:
    """Render a datetime in RFC 7231 IMF-fixdate format for `Last-Modified`."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return format_datetime(value, usegmt=True)


def weak_etag(version_id: str) -> str:
    return f'W/"{version_id}"'
