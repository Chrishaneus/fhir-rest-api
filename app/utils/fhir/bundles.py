"""Bundle response helpers for search and history interactions."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from fastapi import Request

from app.utils.time import fhir_instant, now_utc, weak_etag


def _paged_url(url: str, offset: int, count: int) -> str:
    parsed = urlparse(url)
    qp = parse_qs(parsed.query, keep_blank_values=True)
    qp["_offset"] = [str(offset)]
    qp["_count"] = [str(count)]
    return urlunparse(parsed._replace(query=urlencode(qp, doseq=True)))


def bundle_response(
    request: Request,
    bundle_type: str,
    entries: list,
    *,
    total: int | None = None,
    offset: int = 0,
    page_size: int | None = None,
) -> dict[str, Any]:
    base = str(request.base_url).rstrip("/")
    bundle_entries: list[dict[str, Any]] = []
    for version in entries:
        resource = version.resource
        resource_type = resource["resourceType"] if resource else "Resource"
        resource_id = resource["id"] if resource else None
        entry: dict[str, Any] = {
            "fullUrl": f"{base}/{resource_type}/{resource_id}" if resource_id else base,
            "response": {
                "status": "204 No Content" if version.deleted else "200 OK",
                "etag": weak_etag(version.version_id),
                "lastModified": fhir_instant(version.last_updated),
            },
        }
        if resource:
            entry["resource"] = resource
        bundle_entries.append(entry)

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
