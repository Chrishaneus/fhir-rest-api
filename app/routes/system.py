"""Whole-system FHIR HTTP interactions.

* `GET /metadata`  - CapabilityStatement
* `GET /_history`  - system-wide history Bundle
* `GET /`          - root info or system-wide search across all resource types
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.dependencies import require_auth
from app.store import store
from app.utils.errors import FHIRHTTPError
from app.utils.fhir.bundle_exec import process_bundle
from app.utils.fhir.bundles import bundle_response
from app.utils.fhir.capability import capability_statement
from app.utils.fhir.response_shaping import shape_bundle
from app.utils.fhir.search import (
    apply_pagination,
    extract_at_param,
    extract_handling_mode,
    extract_since_param,
    extract_total_mode,
    find_unknown_params,
    parse_pagination_params,
    query_params,
)
from app.utils.fhir.security_labels import filter_for_user
from app.utils.fhir.validation import configured_resource_types
from app.utils.outcomes import fhir_json_response, operation_outcome

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _all_resource_types() -> list[str]:
    return sorted(configured_resource_types() | store.resource_types_in_use())


@router.get("/metadata")
@router.get("/metadata/")
async def metadata(request: Request) -> JSONResponse:
    return fhir_json_response(capability_statement(request, _all_resource_types()))


@router.get("/_history", dependencies=[Depends(require_auth)])
@router.get("/_history/", dependencies=[Depends(require_auth)])
async def system_history(request: Request) -> JSONResponse:
    params = query_params(request)
    offset, page_size = parse_pagination_params(params)
    page, total = store.history(
        since=extract_since_param(params),
        at=extract_at_param(params),
        offset=offset,
        page_size=page_size,
    )
    page = filter_for_user(request.state.current_user, page)
    bundle = bundle_response(
        request, "history", page, total=total, offset=offset, page_size=page_size
    )
    return fhir_json_response(shape_bundle(bundle, params))


@router.post("/", dependencies=[Depends(require_auth)])
async def transaction_or_batch(
    request: Request,
    payload: dict = Body(...),
) -> JSONResponse:
    result = process_bundle(payload, store, request)
    return fhir_json_response(result)


@router.get("/", dependencies=[Depends(require_auth)])
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
    if extract_handling_mode(request.headers.get("prefer")) == "strict":
        unknown = find_unknown_params(params)
        if unknown:
            raise FHIRHTTPError(
                400, f"Unknown search parameters: {', '.join(unknown)}", "not-supported"
            )
    matches = store.system_search(params)
    page, offset, page_size = apply_pagination(matches, params)
    total = None if extract_total_mode(params) == "none" else len(matches)
    bundle = bundle_response(
        request, "searchset", page, total=total, offset=offset, page_size=page_size
    )
    return fhir_json_response(shape_bundle(bundle, params))
