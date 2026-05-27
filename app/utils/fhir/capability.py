"""CapabilityStatement builder."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.utils.fhir.constants import FHIR_VERSION, SUPPORTED_INTERACTIONS
from app.utils.time import STARTUP_TIME, fhir_instant


def capability_statement(request: Request, resource_types: list[str]) -> dict[str, Any]:
    base = str(request.base_url).rstrip("/")
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "date": fhir_instant(STARTUP_TIME),
        "kind": "instance",
        "fhirVersion": FHIR_VERSION,
        "format": ["json"],
        "implementation": {
            "description": "Python FastAPI FHIR REST layer",
            "url": base,
        },
        "rest": [
            {
                "mode": "server",
                "security": {
                    "cors": True,
                    "service": [
                        {
                            "coding": [
                                {
                                    "system": "http://terminology.hl7.org/CodeSystem/restful-security-service",
                                    "code": "JWT",
                                    "display": "JSON Web Token",
                                }
                            ]
                        }
                    ],
                    "description": (
                        "JWT Bearer authentication required on all endpoints except "
                        "GET /health and GET /metadata. Tokens are issued via POST /auth/login. "
                        "Failed login attempts are rate-limited with a timed account lockout "
                        "backed by Redis."
                    ),
                },
                "interaction": [
                    {"code": "capabilities"},
                    {"code": "search-system"},
                    {"code": "history-system"},
                ],
                "resource": [
                    {
                        "type": resource_type,
                        "interaction": [{"code": interaction} for interaction in SUPPORTED_INTERACTIONS],
                        "versioning": "versioned-update",
                        "readHistory": True,
                        "updateCreate": True,
                        "conditionalCreate": False,
                        "conditionalUpdate": False,
                        "conditionalDelete": "not-supported",
                        "referencePolicy": ["local"],
                    }
                    for resource_type in resource_types
                ],
            }
        ],
    }
