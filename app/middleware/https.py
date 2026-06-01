"""HTTPS enforcement middleware."""

from __future__ import annotations

from fastapi import Request
from starlette.responses import Response

from app.config import REQUIRE_HTTPS
from app.utils.outcomes import fhir_json_response, operation_outcome


async def enforce_https(request: Request, call_next) -> Response:
    if REQUIRE_HTTPS and request.headers.get("x-forwarded-proto") == "http":
        return fhir_json_response(
            operation_outcome("HTTPS is required", severity="error", code="security"),
            status_code=403,
        )
    return await call_next(request)
