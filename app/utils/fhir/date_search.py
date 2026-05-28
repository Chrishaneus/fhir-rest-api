"""FHIR date search parameter parsing for ``_lastUpdated`` and date-type params.

Supports the standard FHIR prefixes: eq, ne, gt, ge, lt, le, sa, eb.
Partial dates (YYYY, YYYY-MM, YYYY-MM-DD) are expanded to an implied period;
full instants are treated as a point (start == end).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_UTC = timezone.utc

# Optional prefix + the rest
_PREFIX_RE = re.compile(r"^(eq|ne|lt|le|gt|ge|sa|eb)?(.+)$", re.ASCII)
_YEAR_RE = re.compile(r"^\d{4}$")
_YEAR_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date_param(value: str) -> tuple[str, datetime, datetime]:
    """Parse a FHIR date search value into ``(prefix, period_start, period_end)``.

    Both bounds are UTC-normalised.  Partial dates expand to their full implied
    period (e.g. ``2024-01-01`` → midnight…23:59:59.999999).  Full instants
    have ``period_start == period_end``.

    Raises ``ValueError`` for unrecognised formats.
    """
    value = value.strip()
    m = _PREFIX_RE.match(value)
    if not m:
        raise ValueError(f"Cannot parse date search value: {value!r}")
    prefix = m.group(1) or "eq"
    date_str = m.group(2).strip()
    start, end = _to_period(date_str)
    return prefix, start, end


def _to_period(s: str) -> tuple[datetime, datetime]:
    if _YEAR_RE.match(s):
        year = int(s)
        start = datetime(year, 1, 1, tzinfo=_UTC)
        end = datetime(year + 1, 1, 1, tzinfo=_UTC) - timedelta(microseconds=1)
        return start, end

    if _YEAR_MONTH_RE.match(s):
        year, month = int(s[:4]), int(s[5:7])
        start = datetime(year, month, 1, tzinfo=_UTC)
        if month == 12:
            end_dt = datetime(year + 1, 1, 1, tzinfo=_UTC) - timedelta(microseconds=1)
        else:
            end_dt = datetime(year, month + 1, 1, tzinfo=_UTC) - timedelta(microseconds=1)
        return start, end_dt

    if _DATE_RE.match(s):
        year, month, day = int(s[:4]), int(s[5:7]), int(s[8:10])
        start = datetime(year, month, day, tzinfo=_UTC)
        end = datetime(year, month, day, 23, 59, 59, 999999, tzinfo=_UTC)
        return start, end

    # Full datetime — Python 3.11+ fromisoformat handles Z and ±HH:MM
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_UTC)
        else:
            dt = dt.astimezone(_UTC).replace(tzinfo=_UTC)
        return dt, dt
    except ValueError:
        pass

    raise ValueError(f"Cannot parse date: {s!r}")


def matches_last_updated(last_updated: datetime, value: str) -> bool:
    """Return True if *last_updated* satisfies a single ``_lastUpdated`` search value.

    Raises ``ValueError`` for unrecognisable values.
    """
    prefix, start, end = parse_date_param(value)

    ts = last_updated
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_UTC)
    else:
        ts = ts.astimezone(_UTC).replace(tzinfo=_UTC)

    if prefix == "eq":
        return start <= ts <= end
    if prefix == "ne":
        return not (start <= ts <= end)
    if prefix in ("gt", "sa"):
        return ts > end
    if prefix == "ge":
        return ts >= start
    if prefix in ("lt", "eb"):
        return ts < start
    if prefix == "le":
        return ts <= end
    return False
