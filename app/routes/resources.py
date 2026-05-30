"""Type-level and instance-level FHIR HTTP interactions.

Type-level:
* `GET    /{resource_type}/_history`      - history Bundle for a resource type
* `POST   /{resource_type}/_search`       - search Bundle (POST form)
* `GET    /{resource_type}`               - search Bundle (GET query)
* `POST   /{resource_type}/$validate`     - validate without persisting
* `POST   /{resource_type}`               - create
* `PUT    /{resource_type}`               - conditional update (search criteria in query string)

Instance-level:
* `GET    /{resource_type}/{id}/_history/{vid}`  - vread
* `GET    /{resource_type}/{id}/_history`        - instance history Bundle
* `GET    /{resource_type}/{id}/$everything`     - everything operation
* `GET    /{resource_type}/{id}`                 - read
* `PUT    /{resource_type}/{id}`                 - update / upsert
* `PATCH  /{resource_type}/{id}`                 - patch (JSON Patch)
* `DELETE /{resource_type}/{id}`                 - delete (tombstone)
* `DELETE /{resource_type}`                      - conditional delete (search criteria in query string)

Compartment search:
* `GET    /{compartment_type}/{compartment_id}/{resource_type}` - compartment search

Route order matters here: Starlette matches routes in the order they are
registered, so the `_history` / `_search` literal-segment routes must be
declared before the generic `{resource_type}/{resource_id}` routes, and the
compartment search route must follow routes with literal third segments
(`_history`, `$everything`) to avoid shadowing them.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import parse_qs

import jsonpatch
from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse, Response

from app.auth.dependencies import require_auth
from app.hooks import hooks
from app.store import VersionConflictError, store
from app.utils.errors import FHIRHTTPError
from app.utils.fhir.bundles import bundle_response
from app.utils.fhir.compartments import SUPPORTED_COMPARTMENTS, get_compartment_params
from app.utils.fhir.constants import IGNORED_SEARCH_PARAMS
from app.utils.fhir.includes import resolve_includes
from app.utils.fhir.response_shaping import shape_bundle, shape_resource
from app.utils.fhir.search import (
    apply_pagination,
    extract_at_param,
    extract_handling_mode,
    extract_since_param,
    extract_total_mode,
    find_unknown_params,
    form_and_query_params,
    query_params,
)
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
from app.utils.outcomes import fhir_json_response, operation_outcome


def _parse_if_none_exist(header: str) -> dict[str, list[str]]:
    params = parse_qs(header, keep_blank_values=False)
    if not params:
        raise FHIRHTTPError(
            400, "If-None-Exist header must contain at least one search parameter", "invalid"
        )
    return params

router = APIRouter(tags=["resources"], dependencies=[Depends(require_auth)])



@router.get("/{resource_type}/_history")
@router.get("/{resource_type}/_history/")
async def type_history(resource_type: str, request: Request) -> JSONResponse:
    assert_resource_type(resource_type)
    params = query_params(request)
    entries = store.history(resource_type=resource_type, since=extract_since_param(params), at=extract_at_param(params))
    page, offset, page_size = apply_pagination(entries, params)
    bundle = bundle_response(request, "history", page, total=len(entries), offset=offset, page_size=page_size)
    return fhir_json_response(shape_bundle(bundle, params))


@router.post("/{resource_type}/_search")
@router.post("/{resource_type}/_search/")
async def post_type_search(resource_type: str, request: Request) -> JSONResponse:
    assert_resource_type(resource_type)
    params = await form_and_query_params(request)
    if extract_handling_mode(request.headers.get("prefer")) == "strict":
        unknown = find_unknown_params(params)
        if unknown:
            raise FHIRHTTPError(400, f"Unknown search parameters: {', '.join(unknown)}", "not-supported")
    matches = store.search(resource_type, params)
    page, offset, page_size = apply_pagination(matches, params)
    included = resolve_includes(store, resource_type, page, params)
    total = None if extract_total_mode(params) == "none" else len(matches)
    bundle = bundle_response(
        request, "searchset", page,
        total=total, offset=offset, page_size=page_size,
        included=included,
    )
    return fhir_json_response(shape_bundle(bundle, params))


@router.get("/{resource_type}")
@router.get("/{resource_type}/")
async def get_type_search(resource_type: str, request: Request) -> JSONResponse:
    assert_resource_type(resource_type)
    params = query_params(request)
    if extract_handling_mode(request.headers.get("prefer")) == "strict":
        unknown = find_unknown_params(params)
        if unknown:
            raise FHIRHTTPError(400, f"Unknown search parameters: {', '.join(unknown)}", "not-supported")
    matches = store.search(resource_type, params)
    page, offset, page_size = apply_pagination(matches, params)
    included = resolve_includes(store, resource_type, page, params)
    total = None if extract_total_mode(params) == "none" else len(matches)
    bundle = bundle_response(
        request, "searchset", page,
        total=total, offset=offset, page_size=page_size,
        included=included,
    )
    return fhir_json_response(shape_bundle(bundle, params))


@router.post("/{resource_type}")
@router.post("/{resource_type}/")
async def create_resource(
    resource_type: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> Response:
    assert_resource_type(resource_type)

    if_none_exist = request.headers.get("if-none-exist")
    if if_none_exist is not None:
        search_params = _parse_if_none_exist(if_none_exist)
        matches = store.search(resource_type, search_params)
        if len(matches) > 1:
            raise FHIRHTTPError(
                412,
                f"If-None-Exist matched {len(matches)} existing resources — criteria must be unambiguous",
                "multiple-matches",
            )
        if len(matches) == 1:
            version = matches[0]
            assert version.resource is not None
            headers = response_headers(request, resource_type, version.resource["id"], version)
            return preferred_success_response(
                version.resource,
                status_code=200,
                headers=headers,
                prefer=request.headers.get("prefer"),
            )

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


@router.put("/{resource_type}")
@router.put("/{resource_type}/")
async def conditional_update_resource(
    resource_type: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> Response:
    assert_resource_type(resource_type)

    params = query_params(request)
    search_criteria = {k for k in params if k not in IGNORED_SEARCH_PARAMS}
    if not search_criteria:
        raise FHIRHTTPError(
            400,
            "Conditional update requires at least one search parameter in the query string",
            "invalid",
        )

    matches = store.search(resource_type, params)

    if len(matches) > 1:
        raise FHIRHTTPError(
            412,
            f"Conditional update matched {len(matches)} resources — criteria must be unambiguous",
            "multiple-matches",
        )

    body_id: str | None = payload.get("id")

    if len(matches) == 1:
        assert matches[0].resource is not None
        matched_id: str = matches[0].resource["id"]
        if body_id is not None and body_id != matched_id:
            raise FHIRHTTPError(
                400,
                f"Body id '{body_id}' conflicts with the matched resource id '{matched_id}'",
                "invalid",
            )
        resource_id = matched_id
    else:
        resource_id = body_id or uuid.uuid4().hex

    resource = validate_request_body(
        resource_type,
        {**payload, "id": resource_id},
        require_id=resource_id,
    )

    current = store.latest(resource_type, resource_id)
    hook = hooks.get(resource_type)
    old_resource = current.resource if current and not current.deleted else None
    resource = hook.before_update(old_resource, resource)

    try:
        version, created = store.update(resource_type, resource_id, resource)
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


@router.post("/{resource_type}/$validate")
@router.post("/{resource_type}/$validate/")
async def validate_resource_type(
    resource_type: str,
    payload: dict[str, Any] = Body(...),
) -> JSONResponse:
    assert_resource_type(resource_type)
    validate_request_body(resource_type, payload)
    return fhir_json_response(
        operation_outcome("Validation passed", severity="information", code="informational"),
    )


@router.get("/{resource_type}/{resource_id}/_history/{version_id}")
@router.get("/{resource_type}/{resource_id}/_history/{version_id}/")
async def read_version(
    resource_type: str, resource_id: str, version_id: str, request: Request
) -> JSONResponse:
    assert_resource_type(resource_type)
    assert_resource_id(resource_id)
    version = store.version(resource_type, resource_id, version_id)
    if version is None:
        raise FHIRHTTPError(404, "Resource version was not found", "not-found")
    if version.deleted:
        raise FHIRHTTPError(410, "Resource version represents a deleted resource", "deleted")
    assert version.resource is not None
    params = query_params(request)
    return fhir_json_response(shape_resource(version.resource, params), headers=read_headers(version))


@router.get("/{resource_type}/{resource_id}/_history")
@router.get("/{resource_type}/{resource_id}/_history/")
async def instance_history(
    resource_type: str, resource_id: str, request: Request
) -> JSONResponse:
    assert_resource_type(resource_type)
    assert_resource_id(resource_id)
    params = query_params(request)
    since = extract_since_param(params)
    at = extract_at_param(params)
    if store.latest(resource_type, resource_id) is None:
        raise FHIRHTTPError(404, "Resource was not found", "not-found")
    entries = store.history(resource_type=resource_type, resource_id=resource_id, since=since, at=at)
    page, offset, page_size = apply_pagination(entries, params)
    bundle = bundle_response(request, "history", page, total=len(entries), offset=offset, page_size=page_size)
    return fhir_json_response(shape_bundle(bundle, params))


@router.get("/{resource_type}/{resource_id}/$everything")
async def resource_everything(resource_type: str, resource_id: str, request: Request) -> JSONResponse:
    assert_resource_type(resource_type)
    assert_resource_id(resource_id)
    anchor, linked = store.resource_everything(resource_type, resource_id)
    if anchor is None:
        raise FHIRHTTPError(404, f"{resource_type}/{resource_id} was not found", "not-found")
    if anchor.deleted:
        raise FHIRHTTPError(410, f"{resource_type}/{resource_id} has been deleted", "deleted")

    params = query_params(request)
    all_versions = [anchor] + linked
    page, offset, page_size = apply_pagination(all_versions, params)

    # Anchor resource is always the "match" entry; everything else is "include"
    page_anchor = [v for v in page if v is anchor]
    page_linked = [v for v in page if v is not anchor]

    bundle = bundle_response(
        request, "searchset", page_anchor,
        total=len(all_versions),
        offset=offset,
        page_size=page_size,
        included=page_linked,
    )
    return fhir_json_response(shape_bundle(bundle, params))


@router.get("/{compartment_type}/{compartment_id}/{resource_type}")
@router.get("/{compartment_type}/{compartment_id}/{resource_type}/")
async def compartment_search(
    compartment_type: str,
    compartment_id: str,
    resource_type: str,
    request: Request,
) -> JSONResponse:
    if compartment_type not in SUPPORTED_COMPARTMENTS:
        raise FHIRHTTPError(
            404,
            f"Compartment '{compartment_type}' is not supported; supported compartments: "
            + ", ".join(sorted(SUPPORTED_COMPARTMENTS)),
            "not-supported",
        )
    assert_resource_id(compartment_id)
    assert_resource_type(resource_type)

    subject = store.latest(compartment_type, compartment_id)
    if subject is None or subject.deleted:
        raise FHIRHTTPError(
            404,
            f"{compartment_type}/{compartment_id} was not found",
            "not-found",
        )

    membership_params = get_compartment_params(compartment_type, resource_type)
    if membership_params is None:
        raise FHIRHTTPError(
            404,
            f"Resource type '{resource_type}' is not a member of the {compartment_type} compartment",
            "not-supported",
        )

    # Inject the compartment membership constraint as the primary reference
    # filter. The first listed param is the most direct reference and the one
    # our projections index; additional membership params (e.g. performer)
    # are OR-linked per the FHIR spec but collapse to the same column for
    # the resource types that have SQL projections.
    params = query_params(request)
    merged_params = {**params, membership_params[0]: [f"{compartment_type}/{compartment_id}"]}

    if extract_handling_mode(request.headers.get("prefer")) == "strict":
        unknown = find_unknown_params(params)
        if unknown:
            raise FHIRHTTPError(400, f"Unknown search parameters: {', '.join(unknown)}", "not-supported")
    matches = store.search(resource_type, merged_params)
    page, offset, page_size = apply_pagination(matches, merged_params)
    included = resolve_includes(store, resource_type, page, merged_params)
    total = None if extract_total_mode(params) == "none" else len(matches)
    bundle = bundle_response(
        request, "searchset", page,
        total=total, offset=offset, page_size=page_size,
        included=included,
    )
    return fhir_json_response(shape_bundle(bundle, params))


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
    params = query_params(request)
    return fhir_json_response(shape_resource(version.resource, params), headers=read_headers(version))


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


@router.delete("/{resource_type}")
@router.delete("/{resource_type}/")
async def conditional_delete_resource(
    resource_type: str,
    request: Request,
) -> Response:
    assert_resource_type(resource_type)

    params = query_params(request)
    search_criteria = {k for k in params if k not in IGNORED_SEARCH_PARAMS}
    if not search_criteria:
        raise FHIRHTTPError(
            400,
            "Conditional delete requires at least one search parameter in the query string",
            "invalid",
        )

    matches = store.search(resource_type, params)

    if len(matches) == 0:
        return Response(status_code=204)

    hook = hooks.get(resource_type)

    if len(matches) == 1:
        match = matches[0]
        assert match.resource is not None
        resource_id = match.resource["id"]
        hook.before_delete(match.resource)
        deleted = store.delete(resource_type, resource_id)
        if deleted is None:
            raise FHIRHTTPError(404, "Resource was not found", "not-found")
        hook.after_delete(match.resource)
        return Response(status_code=204, headers=read_headers(deleted))

    # Build a map of id -> resource content so before/after hooks use the same
    # snapshot and after_delete is only called for resources that were actually
    # tombstoned (delete_many silently skips already-deleted resources).
    resource_by_id: dict[str, dict[str, Any]] = {}
    for resource_match in matches:
        assert resource_match.resource is not None
        resource_by_id[resource_match.resource["id"]] = resource_match.resource
        hook.before_delete(resource_match.resource)
    deleted_pairs = store.delete_many(resource_type, list(resource_by_id))
    deleted_ids = {deleted_id for deleted_id, _ in deleted_pairs}
    for resource_id, resource in resource_by_id.items():
        if resource_id in deleted_ids:
            hook.after_delete(resource)
    return Response(status_code=204)


@router.delete("/{resource_type}/{resource_id}")
@router.delete("/{resource_type}/{resource_id}/")
async def delete_resource(
    resource_type: str,
    resource_id: str,
    request: Request,
) -> Response:
    assert_resource_type(resource_type)
    assert_resource_id(resource_id)

    current = store.latest(resource_type, resource_id)
    if current is None or current.deleted:
        raise FHIRHTTPError(404, "Resource was not found", "not-found")
    assert current.resource is not None

    hook = hooks.get(resource_type)
    hook.before_delete(current.resource)

    try:
        deleted = store.delete(resource_type, resource_id, if_match=request.headers.get("if-match"))
    except VersionConflictError as exc:
        raise FHIRHTTPError(412, str(exc), "conflict") from exc
    if deleted is None:
        raise FHIRHTTPError(404, "Resource was not found", "not-found")

    hook.after_delete(current.resource)

    return Response(status_code=204, headers=read_headers(deleted))
