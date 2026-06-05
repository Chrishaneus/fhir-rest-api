"""Transaction and batch bundle processing (FHIR R5 §3.16).

Supported entry methods: POST (create), PUT (update/upsert), DELETE, GET (read).
Unsupported: PATCH, conditional operations, urn:uuid: reference resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.store import (
    FHIRStore,
    ResourceVersion,
    TransactionCreate,
    TransactionDelete,
    TransactionOperation,
    TransactionRead,
    TransactionResult,
    TransactionUpdate,
    VersionConflictError,
)
from app.utils.errors import FHIRHTTPError
from app.utils.fhir.validation import assert_resource_type, validate_request_body
from app.utils.outcomes import operation_outcome
from app.utils.time import fhir_instant, now_utc, weak_etag

_SUPPORTED_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})

_HTTP_STATUS_TEXT = {
    200: "200 OK",
    201: "201 Created",
    204: "204 No Content",
    400: "400 Bad Request",
    404: "404 Not Found",
    409: "409 Conflict",
    410: "410 Gone",
    412: "412 Precondition Failed",
    422: "422 Unprocessable Entity",
}


@dataclass
class _ParsedEntry:
    index: int
    method: str
    resource_type: str
    resource_id: str | None
    resource: dict[str, Any] | None  # validated + normalised by fhir.resources
    if_match: str | None
    full_url: str | None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def process_bundle(
    bundle: dict[str, Any],
    store: FHIRStore,
    request: Request,
) -> dict[str, Any]:
    """Parse and execute a transaction or batch bundle.

    Raises :class:`FHIRHTTPError` for top-level structural errors and for any
    failed entry in a *transaction* bundle.  Batch errors are reported
    per-entry in the response bundle instead.
    """
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise FHIRHTTPError(400, "Request body must be a FHIR Bundle resource", "structure")

    bundle_type = bundle.get("type", "")
    if bundle_type not in ("transaction", "batch"):
        raise FHIRHTTPError(400, "Bundle.type must be 'transaction' or 'batch'", "invalid")

    entries = bundle.get("entry") or []
    if not isinstance(entries, list):
        raise FHIRHTTPError(400, "Bundle.entry must be an array", "structure")

    base = str(request.base_url).rstrip("/")

    if bundle_type == "transaction":
        return _process_transaction(entries, store, base)
    return _process_batch(entries, store, base)


# ---------------------------------------------------------------------------
# Transaction (atomic)
# ---------------------------------------------------------------------------


def _process_transaction(
    entries: list[dict[str, Any]],
    store: FHIRStore,
    base: str,
) -> dict[str, Any]:
    # Phase 1: parse + validate every entry before touching the DB
    parsed: list[_ParsedEntry] = []
    for entry_index, entry in enumerate(entries):
        parsed.append(_parse_entry(entry, base, entry_index))

    # Phase 2: build store operations and execute atomically
    operations: list[TransactionOperation] = [
        _to_transaction_operation(parsed_entry) for parsed_entry in parsed
    ]
    try:
        results = store.execute_transaction(operations)
    except VersionConflictError as exc:
        raise FHIRHTTPError(412, str(exc), "conflict") from exc

    response_entries = [
        _build_success_entry(parsed_entry, result, base)
        for parsed_entry, result in zip(parsed, results, strict=True)
    ]
    return {
        "resourceType": "Bundle",
        "type": "transaction-response",
        "timestamp": fhir_instant(now_utc()),
        "entry": response_entries,
    }


# ---------------------------------------------------------------------------
# Batch (per-entry independent)
# ---------------------------------------------------------------------------


def _process_batch(
    entries: list[dict[str, Any]],
    store: FHIRStore,
    base: str,
) -> dict[str, Any]:
    response_entries: list[dict[str, Any]] = []
    for i, entry in enumerate(entries):
        try:
            parsed_entry = _parse_entry(entry, base, i)
            result = _execute_batch_entry(parsed_entry, store)
            response_entries.append(_build_success_entry(parsed_entry, result, base))
        except FHIRHTTPError as exc:
            response_entries.append(_build_error_entry(exc.status_code, exc.diagnostics, exc.code))
        except VersionConflictError as exc:
            response_entries.append(_build_error_entry(412, str(exc), "conflict"))
        except Exception as exc:
            response_entries.append(_build_error_entry(500, str(exc), "exception"))
    return {
        "resourceType": "Bundle",
        "type": "batch-response",
        "timestamp": fhir_instant(now_utc()),
        "entry": response_entries,
    }


def _execute_batch_entry(parsed_entry: _ParsedEntry, store: FHIRStore) -> TransactionResult:
    version: ResourceVersion | None
    if parsed_entry.method == "POST":
        version = store.create(parsed_entry.resource_type, parsed_entry.resource or {})
        return TransactionResult(version=version, created=True)
    if parsed_entry.method == "PUT":
        assert parsed_entry.resource_id is not None
        version, created = store.update(
            parsed_entry.resource_type,
            parsed_entry.resource_id,
            parsed_entry.resource or {},
            if_match=parsed_entry.if_match,
        )
        return TransactionResult(version=version, created=created)
    if parsed_entry.method == "DELETE":
        assert parsed_entry.resource_id is not None
        version = store.delete(parsed_entry.resource_type, parsed_entry.resource_id)
        if version is None:
            raise FHIRHTTPError(
                404,
                f"{parsed_entry.resource_type}/{parsed_entry.resource_id} was not found",
                "not-found",
            )
        return TransactionResult(version=version)
    # GET
    assert parsed_entry.resource_id is not None
    version = store.latest(parsed_entry.resource_type, parsed_entry.resource_id)
    if version is None:
        raise FHIRHTTPError(
            404,
            f"{parsed_entry.resource_type}/{parsed_entry.resource_id} was not found",
            "not-found",
        )
    if version.deleted:
        raise FHIRHTTPError(
            410,
            f"{parsed_entry.resource_type}/{parsed_entry.resource_id} has been deleted",
            "deleted",
        )
    return TransactionResult(version=version)


# ---------------------------------------------------------------------------
# Entry parsing + validation
# ---------------------------------------------------------------------------


def _parse_entry(entry: dict[str, Any], base: str, index: int) -> _ParsedEntry:
    if not isinstance(entry, dict):
        raise FHIRHTTPError(400, f"Entry {index} is not an object", "structure")

    request_entry = entry.get("request")
    if not isinstance(request_entry, dict):
        raise FHIRHTTPError(400, f"Entry {index} is missing required 'request' field", "required")

    method = str(request_entry.get("method", "")).upper()
    if method not in _SUPPORTED_METHODS:
        raise FHIRHTTPError(
            400,
            f"Entry {index}: unsupported method '{method}'. Supported: {sorted(_SUPPORTED_METHODS)}",
            "not-supported",
        )

    url = str(request_entry.get("url", ""))
    if not url:
        raise FHIRHTTPError(400, f"Entry {index}: request.url is required", "required")

    resource_type, resource_id = _parse_url(url, base, index)
    assert_resource_type(resource_type)

    if_match = request_entry.get("ifMatch") or None
    raw_resource = entry.get("resource")
    validated_resource: dict[str, Any] | None = None

    if method == "POST":
        if not isinstance(raw_resource, dict):
            raise FHIRHTTPError(400, f"Entry {index}: POST requires a resource body", "required")
        # Strip id — server assigns it
        payload = {key: value for key, value in raw_resource.items() if key != "id"}
        validated_resource = validate_request_body(resource_type, payload)

    elif method == "PUT":
        if resource_id is None:
            raise FHIRHTTPError(
                400, f"Entry {index}: PUT requires a resource id in the URL", "required"
            )
        if not isinstance(raw_resource, dict):
            raise FHIRHTTPError(400, f"Entry {index}: PUT requires a resource body", "required")
        validated_resource = validate_request_body(
            resource_type, raw_resource, require_id=resource_id
        )

    elif method in ("DELETE", "GET"):
        if resource_id is None:
            raise FHIRHTTPError(
                400, f"Entry {index}: {method} requires a resource id in the URL", "required"
            )

    return _ParsedEntry(
        index=index,
        method=method,
        resource_type=resource_type,
        resource_id=resource_id,
        resource=validated_resource,
        if_match=if_match,
        full_url=entry.get("fullUrl"),
    )


def _parse_url(url: str, base: str, index: int) -> tuple[str, str | None]:
    """Return (resource_type, resource_id | None) from an entry request URL."""
    # Strip server base prefix
    for prefix in (base + "/", base):
        if url.startswith(prefix):
            url = url[len(prefix) :]
            break
    url = url.lstrip("/").split("?")[0]  # ignore query string (conditional ops not supported)
    parts = [part for part in url.split("/") if part]
    if not parts:
        raise FHIRHTTPError(
            400, f"Entry {index}: could not parse resource type from URL '{url}'", "invalid"
        )
    return parts[0], parts[1] if len(parts) > 1 else None


# ---------------------------------------------------------------------------
# Store operation conversion
# ---------------------------------------------------------------------------


def _to_transaction_operation(parsed_entry: _ParsedEntry) -> TransactionOperation:
    if parsed_entry.method == "POST":
        return TransactionCreate(
            resource_type=parsed_entry.resource_type, resource=parsed_entry.resource or {}
        )
    if parsed_entry.method == "PUT":
        assert parsed_entry.resource_id is not None
        return TransactionUpdate(
            resource_type=parsed_entry.resource_type,
            resource_id=parsed_entry.resource_id,
            resource=parsed_entry.resource or {},
            if_match=parsed_entry.if_match,
        )
    if parsed_entry.method == "DELETE":
        assert parsed_entry.resource_id is not None
        return TransactionDelete(
            resource_type=parsed_entry.resource_type, resource_id=parsed_entry.resource_id
        )
    # GET
    assert parsed_entry.resource_id is not None
    return TransactionRead(
        resource_type=parsed_entry.resource_type, resource_id=parsed_entry.resource_id
    )


# ---------------------------------------------------------------------------
# Response entry builders
# ---------------------------------------------------------------------------


def _build_success_entry(
    parsed_entry: _ParsedEntry, result: TransactionResult, base: str
) -> dict[str, Any]:
    version = result.version
    entry: dict[str, Any] = {}

    if version is None:
        # DELETE of non-existent resource — not an error per FHIR spec for transactions
        entry["response"] = {"status": "204 No Content"}
        return entry

    resource_type = parsed_entry.resource_type
    resource_id = version.resource["id"] if version.resource else parsed_entry.resource_id

    if parsed_entry.method == "DELETE":
        entry["response"] = {
            "status": "204 No Content",
            "etag": weak_etag(version.version_id),
            "lastModified": fhir_instant(version.last_updated),
        }
        return entry

    full_url = f"{base}/{resource_type}/{resource_id}"
    entry["fullUrl"] = full_url

    if version.resource:
        entry["resource"] = version.resource

    status_code = 201 if result.created else 200
    response: dict[str, Any] = {
        "status": _HTTP_STATUS_TEXT[status_code],
        "etag": weak_etag(version.version_id),
        "lastModified": fhir_instant(version.last_updated),
    }
    if parsed_entry.method in ("POST", "PUT"):
        response["location"] = f"{full_url}/_history/{version.version_id}"

    entry["response"] = response
    return entry


def _build_error_entry(status_code: int, diagnostics: str, code: str) -> dict[str, Any]:
    return {
        "response": {
            "status": _HTTP_STATUS_TEXT.get(status_code, str(status_code)),
            "outcome": operation_outcome(diagnostics, severity="error", code=code),
        }
    }
