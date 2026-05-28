"""``_format`` query-parameter negotiation middleware.

FHIR R5 §3.1.2 — clients may pass ``?_format=<mime-type>`` as an alternative
to the ``Accept`` header. The server MUST honour it and return 406 if the
requested format is not supported.

This server only produces ``application/fhir+json``, so any ``_format`` value
that is not a JSON variant results in a 406 OperationOutcome.
"""

from __future__ import annotations

import json

from fastapi import Request
from starlette.responses import Response

from app.utils.fhir.constants import FHIR_JSON

_SUPPORTED_FORMATS = {
    "application/fhir+json",
    "application/json",
    "json",
    "text/json",
}


async def format_negotiation(request: Request, call_next) -> Response:
    raw_format = request.query_params.get("_format", "").strip()
    # In query strings '+' decodes as a space character; re-encode so that
    # "application/fhir+json" passed without %-encoding still matches.
    normalized_format = raw_format.replace(" ", "+").lower()
    if normalized_format and normalized_format not in _SUPPORTED_FORMATS:
        body = {
            "resourceType": "OperationOutcome",
            "issue": [
                {
                    "severity": "error",
                    "code": "not-supported",
                    "diagnostics": (
                        f"Unsupported _format value: {normalized_format!r}. "
                        "This server only supports application/fhir+json."
                    ),
                }
            ],
        }
        return Response(
            content=json.dumps(body),
            status_code=406,
            media_type=FHIR_JSON,
        )
    return await call_next(request)
