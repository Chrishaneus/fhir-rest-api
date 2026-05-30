"""Search parameter handling and resource matching."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Request

from app.utils.errors import FHIRHTTPError
from app.utils.fhir.constants import IGNORED_SEARCH_PARAMS
from app.utils.fhir.date_search import parse_at_param, parse_since_param


def parse_sort_params(params: dict[str, list[str]]) -> list[tuple[str, bool]]:
    """Parse `_sort` into `[(field_name, ascending), ...]`.

    `?_sort=family,-birthDate` → `[("family", True), ("birthDate", False)]`.
    Comma-separated values and multiple `_sort` params are both supported.
    """
    fields: list[tuple[str, bool]] = []
    for item in params.get("_sort", []):
        for part in item.split(","):
            part = part.strip()
            if not part:
                continue
            if part.startswith("-"):
                fields.append((part[1:], False))
            else:
                fields.append((part, True))
    return fields


def split_csv_values(values: list[str]) -> list[str]:
    parts: list[str] = []
    for value in values:
        parts.extend(item.strip() for item in value.split(",") if item.strip())
    return parts


def query_params(request: Request) -> dict[str, list[str]]:
    params: dict[str, list[str]] = {}
    for key, value in request.query_params.multi_items():
        params.setdefault(key, []).append(value)
    return params


async def form_and_query_params(request: Request) -> dict[str, list[str]]:
    params = query_params(request)
    form = await request.form()
    for key, value in form.multi_items():
        params.setdefault(key, []).append(str(value))
    return params


def extract_since_param(params: dict[str, list[str]]) -> datetime | None:
    """Extract and parse `_since` from history query params.

    Returns a UTC datetime, or None if `_since` is absent.
    Raises 400 on an unrecognisable value.
    """
    values = params.get("_since")
    if not values:
        return None
    try:
        return parse_since_param(values[-1])
    except ValueError as exc:
        raise FHIRHTTPError(400, f"Invalid _since value: {exc}", "invalid") from exc


def extract_at_param(params: dict[str, list[str]]) -> datetime | None:
    """Extract and parse `_at` from history query params.

    Returns a UTC datetime representing the end of the implied period, or
    None if `_at` is absent.  Raises 400 on an unrecognisable value.
    """
    values = params.get("_at")
    if not values:
        return None
    try:
        return parse_at_param(values[-1])
    except ValueError as exc:
        raise FHIRHTTPError(400, f"Invalid _at value: {exc}", "invalid") from exc


def apply_pagination(
    items: list, params: dict[str, list[str]]
) -> tuple[list, int, int | None]:
    """Return (page, offset, page_size).

    page_size is None when _count is not supplied (return everything from offset).
    """
    offset_values = params.get("_offset")
    offset = 0
    if offset_values:
        try:
            offset = max(0, int(offset_values[-1]))
        except ValueError:
            raise FHIRHTTPError(400, "_offset must be an integer", "invalid") from None

    count_values = params.get("_count")
    page_size: int | None = None
    if count_values:
        try:
            page_size = max(0, int(count_values[-1]))
        except ValueError:
            raise FHIRHTTPError(400, "_count must be an integer", "invalid") from None

    if page_size is None:
        return items[offset:], offset, None
    return items[offset : offset + page_size], offset, page_size


def resource_matches(
    resource: dict[str, Any],
    params: dict[str, list[str]],
    *,
    system_search: bool = False,
) -> bool:
    for key, values in params.items():
        if key in IGNORED_SEARCH_PARAMS:
            continue
        if system_search and key == "_type":
            continue
        expected_values = split_csv_values(values)
        if not expected_values:
            continue
        if key == "_id":
            if resource.get("id") not in expected_values:
                return False
            continue
        if not any(_search_value(resource, key, expected) for expected in expected_values):
            return False
    return True


def _search_value(node: Any, key: str, expected: str) -> bool:
    if isinstance(node, dict):
        if key in node and _value_matches(node[key], expected):
            return True
        return any(_search_value(value, key, expected) for value in node.values())
    if isinstance(node, list):
        return any(_search_value(value, key, expected) for value in node)
    return False


def _value_matches(value: Any, expected: str) -> bool:
    if isinstance(value, dict):
        return any(_value_matches(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_value_matches(child, expected) for child in value)
    if value is None:
        return False
    actual = str(value)
    if "|" in expected:
        expected = expected.rsplit("|", 1)[-1]
    return actual.casefold() == expected.casefold() or expected.casefold() in actual.casefold()
