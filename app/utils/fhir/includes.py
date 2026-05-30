"""_include / _revinclude resolution helpers.

FHIR R5 spec:
* `_include=ResourceType:searchParam[:TargetType]` — append resources
  referenced *by* the match set to the Bundle.
* `_revinclude=ResourceType:searchParam[:SourceType]` — append resources
  that *reference* the match set to the Bundle.
* `_include:iterate` / `_revinclude:iterate` — apply transitively until
  no new resources are added (R5 §3.2.1.5).

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
) -> list[tuple[str, str, str | None, bool]]:
    """Parse `_include` or `_revinclude` values into structured 4-tuples.

    FHIR format: `ResourceType:searchParam` or `ResourceType:searchParam:TargetType`.
    Returns `[(source_type, param_name, target_type_or_None, iterate), ...]`.
    Multiple comma-separated specs in one header value are split correctly.
    The fourth element is True when parsed from `_include:iterate` /
    `_revinclude:iterate`.
    """
    base_key = "_revinclude" if reverse else "_include"
    specs: list[tuple[str, str, str | None, bool]] = []
    for iterate, key in ((False, base_key), (True, f"{base_key}:iterate")):
        for raw_value in params.get(key, []):
            for item in raw_value.split(","):
                item = item.strip()
                if not item:
                    continue
                parts = item.split(":")
                if len(parts) < 2:
                    continue
                source_type = parts[0]
                param_name = parts[1]
                target_type: str | None = parts[2] if len(parts) >= 3 else None
                specs.append((source_type, param_name, target_type, iterate))
    return specs


def _to_camel(name: str) -> str:
    """Convert kebab-case to camelCase: `based-on` → `basedOn`."""
    parts = name.split("-")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _collect_refs(value: Any, output: list[str]) -> None:
    """Recursively extract `Reference.reference` strings from a resource subtree."""
    if isinstance(value, dict):
        reference_value = value.get("reference")
        if isinstance(reference_value, str) and reference_value:
            output.append(reference_value)
        for child_value in value.values():
            _collect_refs(child_value, output)
    elif isinstance(value, list):
        for item in value:
            _collect_refs(item, output)


def _extract_references(resource: dict[str, Any], param: str) -> list[str]:
    """Extract `Reference.reference` strings for a search param from a resource.

    Tries both the raw param name and its camelCase form so that `based-on`
    finds the `basedOn` key in the FHIR JSON.
    """
    references: list[str] = []
    for key in {param, _to_camel(param)}:
        value = resource.get(key)
        if value is not None:
            _collect_refs(value, references)
    return references


def _parse_reference(ref: str) -> tuple[str, str] | None:
    """Parse `ResourceType/id` into `(ResourceType, id)`, or `None`."""
    if "/" in ref:
        parts = ref.split("/", 1)
        if parts[0] and parts[1]:
            return parts[0], parts[1]
    return None


def _apply_includes(
    store: FHIRStore,
    specs: list[tuple[str, str, str | None]],
    frontier: list[ResourceVersion],
    seen: set[tuple[str, str]],
) -> list[ResourceVersion]:
    """Fetch outbound-reference targets for `_include` specs from a frontier."""
    batch: list[ResourceVersion] = []
    for source_type, param_name, target_type in specs:
        for version in frontier:
            if not version.resource:
                continue
            if source_type != "*" and version.resource.get("resourceType") != source_type:
                continue
            for ref in _extract_references(version.resource, param_name):
                parsed = _parse_reference(ref)
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
                    batch.append(result)
    return batch


def _apply_revincludes(
    store: FHIRStore,
    specs: list[tuple[str, str, str | None]],
    frontier: list[ResourceVersion],
    seen: set[tuple[str, str]],
) -> list[ResourceVersion]:
    """Fetch resources that point at any resource in the frontier."""
    batch: list[ResourceVersion] = []

    refs_by_type: dict[str, list[str]] = {}
    for version in frontier:
        if not version.resource or not version.resource.get("id"):
            continue
        rtype = version.resource.get("resourceType", "")
        if rtype:
            refs_by_type.setdefault(rtype, []).append(
                f"{rtype}/{version.resource['id']}"
            )

    for source_type, param_name, target_type in specs:
        for ref_type, match_refs in refs_by_type.items():
            if target_type and target_type != ref_type:
                continue
            for rev_version in store.search(source_type, {param_name: match_refs}):
                if not rev_version.resource:
                    continue
                key = (source_type, rev_version.resource.get("id", ""))
                if key in seen:
                    continue
                seen.add(key)
                batch.append(rev_version)

    return batch


def resolve_includes(
    store: FHIRStore,
    resource_type: str,
    page: list[ResourceVersion],
    params: dict[str, list[str]],
) -> list[ResourceVersion]:
    """Fetch resources named by `_include` / `_revinclude` specs.

    Operates only on the current *page*, deduplicates against both the page
    itself and previously included resources via a `(type, id)` seen set.
    Specs with the `:iterate` modifier are applied transitively until no new
    resources are found (R5 §3.2.1.5).
    """
    include_specs = parse_include_specs(params)
    revinclude_specs = parse_include_specs(params, reverse=True)
    if not include_specs and not revinclude_specs:
        return []

    seen: set[tuple[str, str]] = set()
    included: list[ResourceVersion] = []

    for version in page:
        if version.resource:
            seen.add((
                version.resource.get("resourceType", ""),
                version.resource.get("id", ""),
            ))

    base_includes = [(s, p, t) for s, p, t, it in include_specs if not it]
    iter_includes = [(s, p, t) for s, p, t, it in include_specs if it]
    base_revincludes = [(s, p, t) for s, p, t, it in revinclude_specs if not it]
    iter_revincludes = [(s, p, t) for s, p, t, it in revinclude_specs if it]

    # One-shot pass on the original page
    one_shot = (
        _apply_includes(store, base_includes, page, seen)
        + _apply_revincludes(store, base_revincludes, page, seen)
    )
    included.extend(one_shot)

    # Iterate passes: first frontier is page + one-shot results; then each
    # new batch becomes the frontier for the next round.
    if iter_includes or iter_revincludes:
        frontier: list[ResourceVersion] = list(page) + one_shot
        while True:
            new_batch = (
                _apply_includes(store, iter_includes, frontier, seen)
                + _apply_revincludes(store, iter_revincludes, frontier, seen)
            )
            if not new_batch:
                break
            included.extend(new_batch)
            frontier = new_batch

    return included
