"""_include / _revinclude resolution helpers.

FHIR R5 spec:
* ``_include=ResourceType:searchParam[:TargetType]`` — append resources
  referenced *by* the match set to the Bundle.
* ``_revinclude=ResourceType:searchParam[:SourceType]`` — append resources
  that *reference* the match set to the Bundle.

Both operate on the current *page* only (not the full match set).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.store import FHIRStore, ResourceVersion


def parse_include_specs(
    params: dict[str, list[str]],
    *,
    reverse: bool = False,
) -> list[tuple[str, str, str | None]]:
    """Parse ``_include`` or ``_revinclude`` values into structured triples.

    FHIR format: ``ResourceType:searchParam`` or ``ResourceType:searchParam:TargetType``.
    Returns ``[(source_type, param_name, target_type_or_None), ...]``.
    Multiple comma-separated specs in one header value are split correctly.
    """
    key = "_revinclude" if reverse else "_include"
    specs: list[tuple[str, str, str | None]] = []
    for raw in params.get(key, []):
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            parts = item.split(":")
            if len(parts) < 2:
                continue
            source_type = parts[0]
            param_name = parts[1]
            target_type: str | None = parts[2] if len(parts) >= 3 else None
            specs.append((source_type, param_name, target_type))
    return specs


def _to_camel(name: str) -> str:
    """Convert kebab-case to camelCase: ``based-on`` → ``basedOn``."""
    parts = name.split("-")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _collect_refs(value: Any, out: list[str]) -> None:
    """Recursively extract ``Reference.reference`` strings from a resource subtree."""
    if isinstance(value, dict):
        ref = value.get("reference")
        if isinstance(ref, str) and ref:
            out.append(ref)
        for v in value.values():
            _collect_refs(v, out)
    elif isinstance(value, list):
        for item in value:
            _collect_refs(item, out)


def _extract_references(resource: dict[str, Any], param: str) -> list[str]:
    """Extract ``Reference.reference`` strings for a search param from a resource.

    Tries both the raw param name and its camelCase form so that ``based-on``
    finds the ``basedOn`` key in the FHIR JSON.
    """
    refs: list[str] = []
    for key in {param, _to_camel(param)}:
        value = resource.get(key)
        if value is not None:
            _collect_refs(value, refs)
    return refs


def _parse_ref(ref: str) -> tuple[str, str] | None:
    """Parse ``ResourceType/id`` into ``(ResourceType, id)``, or ``None``."""
    if "/" in ref:
        parts = ref.split("/", 1)
        if parts[0] and parts[1]:
            return parts[0], parts[1]
    return None


def resolve_includes(
    store: "FHIRStore",
    resource_type: str,
    page: list["ResourceVersion"],
    params: dict[str, list[str]],
) -> list["ResourceVersion"]:
    """Fetch resources named by ``_include`` / ``_revinclude`` specs.

    Operates only on the current *page*, deduplicates against both the page
    itself and previously included resources via a ``(type, id)`` seen set.
    """
    include_specs = parse_include_specs(params)
    revinclude_specs = parse_include_specs(params, reverse=True)
    if not include_specs and not revinclude_specs:
        return []

    seen: set[tuple[str, str]] = set()
    included: list["ResourceVersion"] = []

    for version in page:
        if version.resource:
            seen.add((
                version.resource.get("resourceType", ""),
                version.resource.get("id", ""),
            ))

    # _include: for each page resource extract outbound references, fetch targets
    for source_type, param_name, target_type in include_specs:
        if source_type != resource_type and source_type != "*":
            continue
        for version in page:
            if not version.resource:
                continue
            for ref in _extract_references(version.resource, param_name):
                parsed = _parse_ref(ref)
                if parsed is None:
                    continue
                ref_type, ref_id = parsed
                if target_type and ref_type != target_type:
                    continue
                if (ref_type, ref_id) in seen:
                    continue
                result = store.latest(ref_type, ref_id)
                if result and not result.deleted:
                    seen.add((ref_type, ref_id))
                    included.append(result)

    # _revinclude: search for resources that point back to the page resources
    for source_type, param_name, target_type in revinclude_specs:
        if target_type and target_type != resource_type:
            continue
        match_refs = [
            f"{resource_type}/{v.resource['id']}"
            for v in page
            if v.resource and v.resource.get("id")
        ]
        if not match_refs:
            continue
        for rev_version in store.search(source_type, {param_name: match_refs}):
            if not rev_version.resource:
                continue
            key = (source_type, rev_version.resource.get("id", ""))
            if key in seen:
                continue
            seen.add(key)
            included.append(rev_version)

    return included
