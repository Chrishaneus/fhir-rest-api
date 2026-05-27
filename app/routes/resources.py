"""Type-level and instance-level FHIR HTTP interactions.

Type-level:
* ``GET    /{resource_type}/_history``      - history Bundle for a resource type
* ``POST   /{resource_type}/_search``       - search Bundle (POST form)
* ``GET    /{resource_type}``               - search Bundle (GET query)
* ``POST   /{resource_type}``               - create

Instance-level:
* ``GET    /{resource_type}/{id}/_history/{vid}``  - vread
* ``GET    /{resource_type}/{id}/_history``        - instance history Bundle
* ``GET    /{resource_type}/{id}``                 - read
* ``PUT    /{resource_type}/{id}``                 - update / upsert
* ``PATCH  /{resource_type}/{id}``                 - patch (JSON Patch)
* ``DELETE /{resource_type}/{id}``                 - delete (tombstone)

Route order matters here: Starlette matches routes in the order they are
registered, so the ``_history`` / ``_search`` literal-segment routes must be
declared before the generic ``{resource_type}/{resource_id}`` routes.
"""

from __future__ import annotations

from typing import Any

import jsonpatch
from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse, Response

from app.auth.dependencies import require_auth
from app.hooks import hooks
from app.store import VersionConflictError, store
from app.utils.errors import FHIRHTTPError
from app.utils.fhir.bundles import bundle_response
from app.utils.fhir.includes import resolve_includes
from app.utils.fhir.search import apply_pagination, form_and_query_params, query_params
from app.utils.fhir.validation import (
    assert_resource_id,
    assert_resource_type,
    validate_request_body,
)
from app.utils.headers import (
    check_conditional_read,
    preferred_success_response,
    read_headers,
    response_headers,
)
from app.utils.outcomes import fhir_json_response

router = APIRouter(tags=["resources"], dependencies=[Depends(require_auth)])



@router.get("/{resource_type}/_history")
@router.get("/{resource_type}/_history/")
async def type_history(resource_type: str, request: Request) -> JSONResponse:
    assert_resource_type(resource_type)
    entries = store.history(resource_type=resource_type)
    return fhir_json_response(bundle_response(request, "history", entries, total=len(entries)))


@router.post("/{resource_type}/_search")
@router.post("/{resource_type}/_search/")
async def post_type_search(resource_type: str, request: Request) -> JSONResponse:
    assert_resource_type(resource_type)
    params = await form_and_query_params(request)
    matches = store.search(resource_type, params)
    page, offset, page_size = apply_pagination(matches, params)
    included = resolve_includes(store, resource_type, page, params)
    return fhir_json_response(
        bundle_response(
            request, "searchset", page,
            total=len(matches), offset=offset, page_size=page_size,
            included=included,
        )
    )


@router.get("/{resource_type}")
@router.get("/{resource_type}/")
async def get_type_search(resource_type: str, request: Request) -> JSONResponse:
    assert_resource_type(resource_type)
    params = query_params(request)
    matches = store.search(resource_type, params)
    page, offset, page_size = apply_pagination(matches, params)
    included = resolve_includes(store, resource_type, page, params)
    return fhir_json_response(
        bundle_response(
            request, "searchset", page,
            total=len(matches), offset=offset, page_size=page_size,
            included=included,
        )
    )


@router.post("/{resource_type}")
@router.post("/{resource_type}/")
async def create_resource(
    resource_type: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> Response:
    assert_resource_type(resource_type)
    resource = validate_request_body(resource_type, payload)

    hook = hooks.get(resource_type)
    resource = hook.before_create(resource)

    version = store.create(resource_type, resource)
    assert version.resource is not None

    hook.after_create(version.resource)

    headers = response_headers(request, resource_type, version.resource["id"], version)
    return preferred_success_response(
        version.resource,
        status_code=201,
        headers=headers,
        prefer=request.headers.get("prefer"),
    )


@router.get("/{resource_type}/{resource_id}/_history/{version_id}")
@router.get("/{resource_type}/{resource_id}/_history/{version_id}/")
async def read_version(resource_type: str, resource_id: str, version_id: str) -> JSONResponse:
    assert_resource_type(resource_type)
    assert_resource_id(resource_id)
    version = store.version(resource_type, resource_id, version_id)
    if version is None:
        raise FHIRHTTPError(404, "Resource version was not found", "not-found")
    if version.deleted:
        raise FHIRHTTPError(410, "Resource version represents a deleted resource", "deleted")
    assert version.resource is not None
    return fhir_json_response(version.resource, headers=read_headers(version))


@router.get("/{resource_type}/{resource_id}/_history")
@router.get("/{resource_type}/{resource_id}/_history/")
async def instance_history(
    resource_type: str, resource_id: str, request: Request
) -> JSONResponse:
    assert_resource_type(resource_type)
    assert_resource_id(resource_id)
    entries = store.history(resource_type=resource_type, resource_id=resource_id)
    if not entries:
        raise FHIRHTTPError(404, "Resource was not found", "not-found")
    return fhir_json_response(bundle_response(request, "history", entries, total=len(entries)))


@router.get("/{resource_type}/{resource_id}")
@router.get("/{resource_type}/{resource_id}/")
async def read_resource(resource_type: str, resource_id: str, request: Request) -> Response:
    assert_resource_type(resource_type)
    assert_resource_id(resource_id)
    version = store.latest(resource_type, resource_id)
    if version is None:
        raise FHIRHTTPError(404, "Resource was not found", "not-found")
    if version.deleted:
        raise FHIRHTTPError(410, "Resource has been deleted", "deleted")
    conditional = check_conditional_read(request, version)
    if conditional is not None:
        return conditional
    assert version.resource is not None
    return fhir_json_response(version.resource, headers=read_headers(version))


@router.put("/{resource_type}/{resource_id}")
@router.put("/{resource_type}/{resource_id}/")
async def update_resource(
    resource_type: str,
    resource_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> Response:
    assert_resource_type(resource_type)
    assert_resource_id(resource_id)
    resource: dict[str, Any] = validate_request_body(
        resource_type, payload, require_id=resource_id
    )

    current = store.latest(resource_type, resource_id)

    hook = hooks.get(resource_type)
    old_resource = current.resource if current and not current.deleted else None
    resource = hook.before_update(old_resource, resource)

    try:
        version, created = store.update(
            resource_type, resource_id, resource,
            if_match=request.headers.get("if-match"),
        )
    except VersionConflictError as exc:
        raise FHIRHTTPError(412, str(exc), "conflict") from exc
    assert version.resource is not None

    hook.after_update(old_resource, version.resource)

    headers = response_headers(request, resource_type, resource_id, version)
    return preferred_success_response(
        version.resource,
        status_code=201 if created else 200,
        headers=headers,
        prefer=request.headers.get("prefer"),
    )


@router.patch("/{resource_type}/{resource_id}")
@router.patch("/{resource_type}/{resource_id}/")
async def patch_resource(
    resource_type: str,
    resource_id: str,
    request: Request,
) -> Response:
    assert_resource_type(resource_type)
    assert_resource_id(resource_id)

    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/json-patch+json"):
        raise FHIRHTTPError(
            415,
            "Content-Type must be application/json-patch+json for PATCH",
            "invalid",
        )

    try:
        operations = await request.json()
    except Exception:
        raise FHIRHTTPError(400, "Request body is not valid JSON", "structure") from None

    if not isinstance(operations, list):
        raise FHIRHTTPError(400, "JSON Patch body must be a JSON array", "structure")

    current = store.latest(resource_type, resource_id)
    if current is None or current.deleted:
        raise FHIRHTTPError(404, "Resource was not found", "not-found")
    assert current.resource is not None

    try:
        patched = jsonpatch.apply_patch(current.resource, operations)
    except (jsonpatch.JsonPatchException, jsonpatch.JsonPointerException) as exc:
        raise FHIRHTTPError(400, f"JSON Patch error: {exc}", "invalid") from exc

    resource = validate_request_body(resource_type, patched, require_id=resource_id)

    hook = hooks.get(resource_type)
    resource = hook.before_update(current.resource, resource)

    try:
        version, _ = store.update(
            resource_type, resource_id, resource,
            if_match=request.headers.get("if-match"),
        )
    except VersionConflictError as exc:
        raise FHIRHTTPError(412, str(exc), "conflict") from exc
    assert version.resource is not None

    hook.after_update(current.resource, version.resource)

    headers = response_headers(request, resource_type, resource_id, version)
    return preferred_success_response(
        version.resource,
        status_code=200,
        headers=headers,
        prefer=request.headers.get("prefer"),
    )


@router.delete("/{resource_type}/{resource_id}")
@router.delete("/{resource_type}/{resource_id}/")
async def delete_resource(resource_type: str, resource_id: str) -> Response:
    assert_resource_type(resource_type)
    assert_resource_id(resource_id)

    current = store.latest(resource_type, resource_id)
    if current is None or current.deleted:
        raise FHIRHTTPError(404, "Resource was not found", "not-found")
    assert current.resource is not None

    hook = hooks.get(resource_type)
    hook.before_delete(current.resource)

    deleted = store.delete(resource_type, resource_id)
    if deleted is None:
        raise FHIRHTTPError(404, "Resource was not found", "not-found")

    hook.after_delete(current.resource)

    return Response(status_code=204, headers=read_headers(deleted))
