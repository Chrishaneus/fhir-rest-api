"""HTTP header helpers for FHIR responses (ETag, Last-Modified, Prefer)."""

from __future__ import annotations

from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any

from fastapi import Request
from fastapi.responses import Response

from app.utils.outcomes import fhir_json_response, operation_outcome
from app.utils.time import http_date, weak_etag


def response_headers(
    request: Request,
    resource_type: str,
    resource_id: str,
    version,
) -> dict[str, str]:
    base = str(request.base_url).rstrip("/")
    location = f"{base}/{resource_type}/{resource_id}/_history/{version.version_id}"
    return {
        "ETag": weak_etag(version.version_id),
        "Last-Modified": http_date(version.last_updated),
        "Location": location,
    }


def read_headers(version) -> dict[str, str]:
    return {
        "ETag": weak_etag(version.version_id),
        "Last-Modified": http_date(version.last_updated),
    }


def has_prefer(prefer: str | None, token: str) -> bool:
    return bool(prefer and token.casefold() in prefer.casefold())


def preferred_success_response(
    content: dict[str, Any],
    *,
    status_code: int,
    headers: dict[str, str],
    prefer: str | None,
) -> Response:
    if has_prefer(prefer, "return=minimal"):
        return Response(status_code=status_code, headers=headers)
    if has_prefer(prefer, "return=OperationOutcome"):
        return fhir_json_response(
            operation_outcome(
                "Interaction completed successfully",
                severity="information",
                code="informational",
            ),
            status_code=status_code,
            headers=headers,
        )
    return fhir_json_response(content, status_code=status_code, headers=headers)


def check_conditional_read(request: Request, version) -> Response | None:
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match == weak_etag(version.version_id):
        return Response(status_code=304, headers=read_headers(version))

    if_modified_since = request.headers.get("if-modified-since")
    if if_modified_since:
        try:
            since = parsedate_to_datetime(if_modified_since)
            if since.tzinfo is None:
                since = since.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return None
        if version.last_updated.replace(microsecond=0) <= since.astimezone(UTC).replace(microsecond=0):
            return Response(status_code=304, headers=read_headers(version))

    return None


