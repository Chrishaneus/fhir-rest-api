"""Structured request logging middleware."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request
from starlette.responses import Response

logger = logging.getLogger("fhir")


async def log_requests(request: Request, call_next) -> Response:
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "request_id": request_id,
        },
    )
    response.headers["x-request-id"] = request_id
    return response
