"""Bundle response helpers for search and history interactions."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from fastapi import Request

from app.utils.time import fhir_instant, now_utc, weak_etag


def _paged_url(url: str, offset: int, count: int) -> str:
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    query_params["_offset"] = [str(offset)]
    query_params["_count"] = [str(count)]
    return urlunparse(parsed._replace(query=urlencode(query_params, doseq=True)))


def _warning_entry(diagnostics: str) -> dict[str, Any]:
    return {
        "search": {"mode": "outcome"},
        "resource": {
            "resourceType": "OperationOutcome",
            "issue": [{"severity": "warning", "code": "not-supported", "diagnostics": diagnostics}],
        },
    }


def bundle_response(
    request: Request,
    bundle_type: str,
    entries: list,
    *,
    total: int | None = None,
    offset: int = 0,
    page_size: int | None = None,
    warnings: list[str] | None = None,
    included: list | None = None,
) -> dict[str, Any]:
    base = str(request.base_url).rstrip("/")
    is_searchset = bundle_type == "searchset"
    is_history = bundle_type == "history"
    bundle_entries: list[dict[str, Any]] = []
    for version in entries:
        resource = version.resource
        entry_resource_type = version.resource_type or (resource["resourceType"] if resource else "Resource")
        entry_resource_id = version.resource_id or (resource.get("id") if resource else None)
        entry: dict[str, Any] = {
            "fullUrl": f"{base}/{entry_resource_type}/{entry_resource_id}" if entry_resource_id else base,
            "response": {
                "status": "204 No Content" if version.deleted else "200 OK",
                "etag": weak_etag(version.version_id),
                "lastModified": fhir_instant(version.last_updated),
            },
        }
        if resource:
            entry["resource"] = resource
        if is_searchset:
            entry["search"] = {"mode": "match"}
        if is_history:
            if version.deleted:
                request_method = "DELETE"
                request_url = f"{entry_resource_type}/{entry_resource_id}" if entry_resource_id else entry_resource_type
            elif version.version_id == "1":
                request_method = "POST"
                request_url = entry_resource_type
            else:
                request_method = "PUT"
                request_url = f"{entry_resource_type}/{entry_resource_id}" if entry_resource_id else entry_resource_type
            entry["request"] = {"method": request_method, "url": request_url}
        bundle_entries.append(entry)

    if included:
        for version in included:
            resource = version.resource
            if not resource:
                continue
            included_resource_type = resource["resourceType"]
            included_resource_id = resource.get("id")
            bundle_entries.append({
                "fullUrl": f"{base}/{included_resource_type}/{included_resource_id}" if included_resource_id else base,
                "search": {"mode": "include"},
                "resource": resource,
                "response": {
                    "status": "200 OK",
                    "etag": weak_etag(version.version_id),
                    "lastModified": fhir_instant(version.last_updated),
                },
            })

    current_url = str(request.url)
    links: list[dict[str, str]] = [{"relation": "self", "url": current_url}]

    if page_size is not None:
        if offset > 0:
            links.append({"relation": "first", "url": _paged_url(current_url, 0, page_size)})
            prev_offset = max(0, offset - page_size)
            links.append(
                {"relation": "previous", "url": _paged_url(current_url, prev_offset, page_size)}
            )
        if total is not None and offset + page_size < total:
            links.append(
                {
                    "relation": "next",
                    "url": _paged_url(current_url, offset + page_size, page_size),
                }
            )

    if warnings:
        bundle_entries.extend(_warning_entry(msg) for msg in warnings)

    bundle: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": bundle_type,
        "timestamp": fhir_instant(now_utc()),
        "link": links,
        "entry": bundle_entries,
    }
    if total is not None:
        bundle["total"] = total
    return bundle
