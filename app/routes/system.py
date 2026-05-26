"""Whole-system FHIR HTTP interactions.

* ``GET /metadata``  - CapabilityStatement
* ``GET /_history``  - system-wide history Bundle
* ``GET /``          - root info or system-wide search across all resource types
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.store import store
from app.utils.fhir.bundles import bundle_response
from app.utils.fhir.capability import capability_statement
from app.utils.fhir.search import apply_pagination, query_params
from app.utils.fhir.validation import configured_resource_types
from app.utils.outcomes import fhir_json_response, operation_outcome

router = APIRouter(tags=["system"])


def _all_resource_types() -> list[str]:
    return sorted(configured_resource_types() | store.resource_types_in_use())


@router.get("/metadata")
@router.get("/metadata/")
async def metadata(request: Request) -> JSONResponse:
    return fhir_json_response(capability_statement(request, _all_resource_types()))


@router.get("/_history")
@router.get("/_history/")
async def system_history(request: Request) -> JSONResponse:
    entries = store.history(limit=1000)
    return fhir_json_response(bundle_response(request, "history", entries, total=len(entries)))


@router.get("/")
async def root_or_system_search(request: Request) -> JSONResponse:
    params = query_params(request)
    if not params:
        return fhir_json_response(
            operation_outcome(
                "FHIR REST layer is running. Use /metadata for the CapabilityStatement.",
                severity="information",
                code="informational",
            )
        )
    matches = store.system_search(params)
    page, offset, page_size = apply_pagination(matches, params)
    return fhir_json_response(
        bundle_response(request, "searchset", page, total=len(matches), offset=offset, page_size=page_size)
    )
