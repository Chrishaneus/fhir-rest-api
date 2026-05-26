"""OperationOutcome and FHIR JSON response helpers."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

_FHIR_JSON = "application/fhir+json"


def operation_outcome(
    diagnostics: str,
    *,
    severity: str = "error",
    code: str = "processing",
) -> dict[str, Any]:
    return {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": severity,
                "code": code,
                "diagnostics": diagnostics,
            }
        ],
    }


def fhir_json_response(
    content: Any,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        content=content,
        status_code=status_code,
        headers=headers,
        media_type=_FHIR_JSON,
    )
