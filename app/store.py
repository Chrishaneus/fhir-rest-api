"""SQLAlchemy-backed FHIR resource store."""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from sqlalchemy import (
    ColumnElement,
    and_,
    bindparam,
    desc,
    func,
    insert,
    literal_column,
    not_,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, aliased, sessionmaker

import app.utils.cache as cache
from app.db.base import SessionLocal, engine
from app.db.models import ResourceVersionRecord
from app.db.search import containment_payloads
from app.projections import registry as projection_registry
from app.utils.fhir.constants import IGNORED_SEARCH_PARAMS
from app.utils.fhir.date_search import matches_last_updated, parse_date_param
from app.utils.fhir.search import parse_sort_params, resource_matches, split_csv_values
from app.utils.time import fhir_instant, now_utc, weak_etag


def _sort_key_for_field(version: ResourceVersion, field: str) -> Any:
    """Extract a comparison-safe sort key from a ResourceVersion for one FHIR sort field."""
    lower = field.lower()
    resource = version.resource or {}
    if lower == "_id":
        return resource.get("id") or ""
    if lower == "_lastupdated":
        return version.last_updated
    if lower in ("family", "name"):
        names = resource.get("name") or []
        return (names[0].get("family") or "") if names else ""
    if lower == "given":
        names = resource.get("name") or []
        given_list = (names[0].get("given") or []) if names else []
        return given_list[0] if given_list else ""
    if lower == "birthdate":
        return resource.get("birthDate") or ""
    return resource.get(field) or resource.get(lower) or ""


def _apply_python_sort(
    versions: list[ResourceVersion],
    params: dict[str, list[str]],
) -> list[ResourceVersion]:
    """Re-sort ResourceVersions by `_sort` params using Python comparison.

    Applies each sort field in reverse order so the first field in `_sort`
    takes highest priority (Python's stable sort preserves previous orderings).
    """
    sort_fields = parse_sort_params(params)
    if not sort_fields:
        return versions
    result = list(versions)
    for field, ascending in reversed(sort_fields):
        result.sort(
            key=lambda version: _sort_key_for_field(version, field),
            reverse=not ascending,
        )
    return result


def _last_updated_sql_clause(
    column: Any, value: str
) -> ColumnElement[bool] | None:
    """Return a SQLAlchemy filter clause for a single `_lastUpdated` value.

    Returns `None` if the value cannot be parsed so callers can skip silently.
    """
    try:
        prefix, start, end = parse_date_param(value)
    except ValueError:
        return None
    if prefix == "eq":
        return and_(column >= start, column <= end)
    if prefix == "ne":
        return not_(and_(column >= start, column <= end))
    if prefix in ("gt", "sa"):
        return column > end
    if prefix == "ge":
        return column >= start
    if prefix in ("lt", "eb"):
        return column < start
    if prefix == "le":
        return column <= end
    return None


def _apply_last_updated_filter(
    versions: list[ResourceVersion],
    params: dict[str, list[str]],
) -> list[ResourceVersion]:
    """Post-filter *versions* by every `_lastUpdated` value in *params* (AND semantics)."""
    filter_values = split_csv_values(params.get("_lastUpdated", []))
    if not filter_values:
        return versions
    return [
        version for version in versions
        if all(matches_last_updated(version.last_updated, last_updated_value) for last_updated_value in filter_values)
    ]


class VersionConflictError(Exception):
    """Raised by FHIRStore.update() when If-Match fails inside the write lock."""


@dataclass(frozen=True)
class ResourceVersion:
    """In-memory representation of a single FHIR resource version."""

    version_id: str
    resource: dict[str, Any] | None
    last_updated: datetime
    deleted: bool = False
    resource_type: str = ""
    resource_id: str = ""


# ---------------------------------------------------------------------------
# ResourceVersion cache serialisation
# ---------------------------------------------------------------------------

_CACHE_KEY_LATEST = "fhir:v:{}:{}"
_CACHE_KEY_VERSION = "fhir:v:{}:{}:{}"


def _resource_version_to_json(resource_version: ResourceVersion) -> str:
    return json.dumps({
        "version_id": resource_version.version_id,
        "resource": resource_version.resource,
        "last_updated": resource_version.last_updated.isoformat(),
        "deleted": resource_version.deleted,
        "resource_type": resource_version.resource_type,
        "resource_id": resource_version.resource_id,
    })


def _json_to_resource_version(raw: str) -> ResourceVersion:
    d = json.loads(raw)
    return ResourceVersion(
        version_id=d["version_id"],
        resource=d["resource"],
        last_updated=datetime.fromisoformat(d["last_updated"]),
        deleted=d["deleted"],
        resource_type=d.get("resource_type", ""),
        resource_id=d.get("resource_id", ""),
    )


# ---------------------------------------------------------------------------
# Transaction / batch operation types
# ---------------------------------------------------------------------------


@dataclass
class TransactionCreate:
    resource_type: str
    resource: dict[str, Any]


@dataclass
class TransactionUpdate:
    resource_type: str
    resource_id: str
    resource: dict[str, Any]
    if_match: str | None = None


@dataclass
class TransactionDelete:
    resource_type: str
    resource_id: str


@dataclass
class TransactionRead:
    resource_type: str
    resource_id: str


TransactionOperation = TransactionCreate | TransactionUpdate | TransactionDelete | TransactionRead


@dataclass
class TransactionResult:
    version: ResourceVersion | None
    created: bool = False


class FHIRStore:
    """FHIR resource store backed by SQLAlchemy.

    A single `resource_versions` table holds every historical version, with
    deletion tombstones marked by `deleted=True` and `content=None`.
    """

    TTL_LATEST: int = 300    # 5 min — invalidated on every write; safety-net TTL
    TTL_VERSION: int = 3600  # 1 hour — historical versions are immutable

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or SessionLocal
        self._lock = RLock()
        self._is_postgres = engine.dialect.name == "postgresql"

    @cache.write_through(
        key_function=lambda resource_version, resource_type: _CACHE_KEY_LATEST.format(resource_type, (resource_version.resource or {}).get("id")),
        ttl=TTL_LATEST,
        serialize=_resource_version_to_json,
    )
    @cache.write_through(
        key_function=lambda resource_version, resource_type: _CACHE_KEY_VERSION.format(resource_type, (resource_version.resource or {}).get("id"), resource_version.version_id),
        ttl=TTL_VERSION,
        serialize=_resource_version_to_json,
    )
    def create(self, resource_type: str, resource: dict[str, Any]) -> ResourceVersion:
        with self._lock, self._session_factory() as session:
            resource_id = uuid.uuid4().hex
            prepared, last_updated = self._prepare(resource_type, resource_id, "1", resource)
            session.add(
                ResourceVersionRecord(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    version_id="1",
                    last_updated=last_updated,
                    deleted=False,
                    content=prepared,
                )
            )
            self._upsert_projection(
                session, resource_type, resource_id, "1", last_updated, prepared
            )
            session.commit()
            return ResourceVersion("1", prepared, last_updated, False, resource_type, resource_id)

    @cache.write_through(
        key_function=lambda _, resource_type, resource_id: _CACHE_KEY_LATEST.format(resource_type, resource_id),
        ttl=TTL_LATEST,
        serialize=_resource_version_to_json,
        result_function=lambda result: result[0],
    )
    @cache.write_through(
        key_function=lambda resource_version, resource_type, resource_id: _CACHE_KEY_VERSION.format(resource_type, resource_id, resource_version.version_id),
        ttl=TTL_VERSION,
        serialize=_resource_version_to_json,
        result_function=lambda result: result[0],
    )
    def update(
        self,
        resource_type: str,
        resource_id: str,
        resource: dict[str, Any],
        *,
        if_match: str | None = None,
    ) -> tuple[ResourceVersion, bool]:
        with self._lock, self._session_factory() as session:
            latest = self._latest_record(session, resource_type, resource_id)
            if if_match is not None:
                current_etag = (
                    weak_etag(latest.version_id)
                    if (latest is not None and not latest.deleted)
                    else None
                )
                if current_etag is None or if_match != current_etag:
                    raise VersionConflictError(
                        "If-Match did not match the current resource version"
                    )
            created = latest is None or latest.deleted
            next_version_id = str(int(latest.version_id) + 1) if latest else "1"
            prepared, last_updated = self._prepare(
                resource_type, resource_id, next_version_id, resource
            )
            session.add(
                ResourceVersionRecord(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    version_id=next_version_id,
                    last_updated=last_updated,
                    deleted=False,
                    content=prepared,
                )
            )
            self._upsert_projection(
                session,
                resource_type,
                resource_id,
                next_version_id,
                last_updated,
                prepared,
            )
            session.commit()
            return ResourceVersion(next_version_id, prepared, last_updated, False, resource_type, resource_id), created

    @cache.write_through(
        key_function=lambda _, resource_type, resource_id: _CACHE_KEY_LATEST.format(resource_type, resource_id),
        ttl=TTL_LATEST,
        serialize=_resource_version_to_json,
    )
    def delete(
        self,
        resource_type: str,
        resource_id: str,
        *,
        if_match: str | None = None,
    ) -> ResourceVersion | None:
        with self._lock, self._session_factory() as session:
            latest = self._latest_record(session, resource_type, resource_id)
            if latest is None or latest.deleted:
                return None
            if if_match is not None:
                current_etag = weak_etag(latest.version_id)
                if if_match != current_etag:
                    raise VersionConflictError(
                        "If-Match did not match the current resource version"
                    )
            next_version_id = str(int(latest.version_id) + 1)
            last_updated = now_utc()
            session.add(
                ResourceVersionRecord(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    version_id=next_version_id,
                    last_updated=last_updated,
                    deleted=True,
                    content=None,
                )
            )
            self._delete_projection(session, resource_type, resource_id)
            session.commit()
            return ResourceVersion(next_version_id, None, last_updated, True, resource_type, resource_id)

    def delete_many(
        self,
        resource_type: str,
        resource_ids: list[str],
    ) -> list[tuple[str, ResourceVersion]]:
        """Delete multiple resources in a single atomic transaction.

        Resources that are already deleted or not found are skipped silently.
        Returns (resource_id, tombstone) pairs for every resource that was actually deleted.
        Cache is updated for each tombstone after the commit.
        """
        results: list[tuple[str, ResourceVersion]] = []
        last_updated = now_utc()
        with self._lock, self._session_factory() as session:
            for resource_id in resource_ids:
                latest = self._latest_record(session, resource_type, resource_id)
                if latest is None or latest.deleted:
                    continue
                next_version_id = str(int(latest.version_id) + 1)
                session.add(
                    ResourceVersionRecord(
                        resource_type=resource_type,
                        resource_id=resource_id,
                        version_id=next_version_id,
                        last_updated=last_updated,
                        deleted=True,
                        content=None,
                    )
                )
                self._delete_projection(session, resource_type, resource_id)
                results.append((resource_id, ResourceVersion(next_version_id, None, last_updated, True, resource_type, resource_id)))
            session.commit()
        for resource_id, tombstone in results:
            cache.cache_set(
                _CACHE_KEY_LATEST.format(resource_type, resource_id),
                tombstone,
                FHIRStore.TTL_LATEST,
                _resource_version_to_json,
            )
        return results

    def bulk_create(
        self,
        rows: Iterable[tuple[str, str, dict[str, Any]]],
        *,
        batch_size: int = 500,
    ) -> int:
        """Bulk insert version 1 records using batched INSERTs.

        Each `rows` item is `(resource_type, resource_id, resource_dict)`.
        The store fills in `versionId="1"`, `meta.lastUpdated`, and the
        `last_updated` column. Duplicates (same resource_type/resource_id/
        version_id) are silently skipped via `ON CONFLICT DO NOTHING`, which
        keeps the bulk seeder idempotent without requiring the caller to
        truncate first.

        Returns the number of rows submitted to the database (i.e. the count
        of `rows` consumed -- not necessarily the count actually inserted,
        because psycopg's `rowcount` is unreliable for multi-row
        `INSERT ... ON CONFLICT` statements). Callers that need an exact
        "inserted vs skipped" breakdown should `SELECT COUNT(*)` before and
        after.

        This is intended for the bulk seeder. Production writes should go
        through :meth:`create` / :meth:`update` so that hooks, version
        bookkeeping, and concurrency control all kick in.
        """
        # Bypass the ORM unit-of-work entirely: we know we're inserting fresh
        # version-1 rows, and at scale the per-row overhead of session.add()
        # plus flush dominates wall time.
        submitted = 0
        with self._lock, self._session_factory() as session:
            dialect = session.bind.dialect.name if session.bind else ""
            batch: list[dict[str, Any]] = []
            # Projection rows are grouped per resource type; each type gets
            # its own dialect-aware upsert at flush time.
            projection_batches: dict[str, list[dict[str, Any]]] = {}

            def flush(batch: list[dict[str, Any]]) -> None:
                if not batch:
                    return
                # Each branch builds and executes its own statement: the
                # dialect-specific Insert subclasses can't share a typed local.
                if dialect == "postgresql":
                    session.execute(
                        pg_insert(ResourceVersionRecord)
                        .values(batch)
                        .on_conflict_do_nothing(
                            index_elements=[
                                "resource_type",
                                "resource_id",
                                "version_id",
                            ]
                        )
                    )
                elif dialect == "sqlite":
                    session.execute(
                        sqlite_insert(ResourceVersionRecord)
                        .values(batch)
                        .on_conflict_do_nothing(
                            index_elements=[
                                "resource_type",
                                "resource_id",
                                "version_id",
                            ]
                        )
                    )
                else:
                    session.execute(insert(ResourceVersionRecord).values(batch))

            def flush_projections() -> None:
                for resource_type, proj_rows in projection_batches.items():
                    projection = projection_registry.for_type(resource_type)
                    if projection is None or not proj_rows:
                        continue
                    projection.bulk_upsert_rows(session, proj_rows)
                projection_batches.clear()

            for resource_type, resource_id, resource in rows:
                prepared, last_updated = self._prepare(
                    resource_type, resource_id, "1", resource
                )
                batch.append(
                    {
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "version_id": "1",
                        "last_updated": last_updated,
                        "deleted": False,
                        "content": prepared,
                    }
                )
                projection = projection_registry.for_type(resource_type)
                if projection is not None:
                    extracted_columns = projection.extract(prepared)
                    extracted_columns.update(
                        resource_id=resource_id,
                        version_id="1",
                        last_updated=last_updated,
                    )
                    projection_batches.setdefault(resource_type, []).append(extracted_columns)
                submitted += 1
                if len(batch) >= batch_size:
                    flush(batch)
                    flush_projections()
                    session.commit()
                    batch = []

            flush(batch)
            flush_projections()
            session.commit()

        return submitted

    @cache.redis_cached(
        key_function=lambda resource_type, resource_id: _CACHE_KEY_LATEST.format(resource_type, resource_id),
        ttl=TTL_LATEST,
        serialize=_resource_version_to_json,
        deserialize=_json_to_resource_version,
    )
    def latest(self, resource_type: str, resource_id: str) -> ResourceVersion | None:
        with self._session_factory() as session:
            return self._to_version(self._latest_record(session, resource_type, resource_id))

    @cache.redis_cached(
        key_function=lambda resource_type, resource_id, version_id: _CACHE_KEY_VERSION.format(resource_type, resource_id, version_id),
        ttl=TTL_VERSION,
        serialize=_resource_version_to_json,
        deserialize=_json_to_resource_version,
    )
    def version(
        self, resource_type: str, resource_id: str, version_id: str
    ) -> ResourceVersion | None:
        with self._session_factory() as session:
            statement = select(ResourceVersionRecord).where(
                ResourceVersionRecord.resource_type == resource_type,
                ResourceVersionRecord.resource_id == resource_id,
                ResourceVersionRecord.version_id == version_id,
            )
            return self._to_version(session.execute(statement).scalar_one_or_none())

    def search(
        self, resource_type: str, params: dict[str, list[str]]
    ) -> list[ResourceVersion]:
        with self._session_factory() as session:
            return self._dispatch_search(session, resource_type, params)

    def system_search(self, params: dict[str, list[str]]) -> list[ResourceVersion]:
        selected_types = split_csv_values(params.get("_type", []))
        with self._session_factory() as session:
            if selected_types:
                resource_types = selected_types
            else:
                statement = select(ResourceVersionRecord.resource_type).distinct()
                resource_types = [row for (row,) in session.execute(statement).all()]

            matches: list[ResourceVersion] = []
            for resource_type in resource_types:
                matches.extend(
                    self._dispatch_search(session, resource_type, params, system_search=True)
                )
            return matches

    def resource_everything(
        self, resource_type: str, resource_id: str
    ) -> tuple[ResourceVersion | None, list[ResourceVersion]]:
        """Return (anchor_version, linked_versions) for the $everything operation.

        Works for any resource type - Patient, Encounter, Group, etc.
        On Postgres uses a single `jsonb_path_exists` query with the recursive
        jsonpath `$.**.reference ? (@ == $ref)` against the `jsonb_path_ops`
        GIN index - O(log N) and catches references at any nesting depth.
        On SQLite falls back to a Python-level deep scan (used by unit tests).
        """
        anchor = self.latest(resource_type, resource_id)
        if anchor is None or anchor.deleted:
            return anchor, []
        resource_reference = f"{resource_type}/{resource_id}"
        with self._session_factory() as session:
            if self._is_postgres:
                linked = self._sql_resource_everything(session, resource_type, resource_reference)
            else:
                params: dict[str, list[str]] = {"reference": [resource_reference]}
                statement = select(ResourceVersionRecord.resource_type).distinct()
                all_types = [row for (row,) in session.execute(statement).all()]
                linked = []
                for linked_type in all_types:
                    if linked_type == resource_type or linked_type == "AuditEvent":
                        continue
                    linked.extend(self._python_search(session, linked_type, params))
        return anchor, linked

    def _sql_resource_everything(
        self, session: Session, anchor_type: str, resource_reference: str
    ) -> list[ResourceVersion]:
        """Postgres-only: find all resources of other types that reference `resource_reference`.

        Uses `jsonb_path_exists` with the recursive jsonpath operator `**`
        so any `"reference"` key at any nesting depth (including extensions)
        is matched.  `resource_reference` is bound through a JSONB vars object
        (`$ref`) rather than interpolated into the path string.
        """
        latest = (
            select(ResourceVersionRecord)
            .where(
                ResourceVersionRecord.resource_type != anchor_type,
                ResourceVersionRecord.resource_type != "AuditEvent",
            )
            .order_by(
                ResourceVersionRecord.resource_type,
                ResourceVersionRecord.resource_id,
                ResourceVersionRecord.last_updated.desc(),
                ResourceVersionRecord.id.desc(),
            )
            .distinct(
                ResourceVersionRecord.resource_type,
                ResourceVersionRecord.resource_id,
            )
            .subquery()
        )
        LatestRV = aliased(ResourceVersionRecord, latest)

        _jpath: ColumnElement[str] = literal_column("'$.**.reference ? (@ == $ref)'::jsonpath")
        _vars = bindparam(None, {"ref": resource_reference}, type_=JSONB)

        statement = select(LatestRV).where(
            LatestRV.deleted.is_(False),
            func.jsonb_path_exists(LatestRV.content, _jpath, _vars),
        )
        records = session.execute(statement).scalars().all()
        return [version for version in (self._to_version(record) for record in records) if version]

    def _dispatch_search(
        self,
        session: Session,
        resource_type: str,
        params: dict[str, list[str]],
        *,
        system_search: bool = False,
    ) -> list[ResourceVersion]:
        """Pick the cheapest viable search strategy for a (type, params) pair.

        Order of preference:
          1. Projection-table SQL search if one exists for `resource_type`
             *and* it covers every requested param. Hits B-tree indexes only.
          2. JSONB containment over `resource_versions` (Postgres only).
          3. Python fallback (SQLite, used by unit tests).

        When a projection partially covers a request, we deliberately fall
        through instead of mixing strategies: a JSONB filter on the unsupported
        param plus a projection JOIN would either need a second JOIN or an
        `IN (subquery)` rewrite, and the cost of the JSONB scan dominates
        either way. Cleaner to let the JSONB path own those requests.
        """
        # Projection fast path: works for both direct and system search because
        # _dispatch_search is already called once per resource_type, and each
        # projection's build_select filters on its own resource_type in the JOIN.
        projection = projection_registry.for_type(resource_type)
        if projection is not None and projection.supports(
            {key: value for key, value in params.items() if key != "_type"}
        ):
            return self._projection_search(session, projection, params)

        if self._is_postgres:
            return self._sql_search(
                session, resource_type, params, system_search=system_search
            )
        return self._python_search(
            session, resource_type, params, system_search=system_search
        )

    def _projection_search(
        self,
        session: Session,
        projection: Any,
        params: dict[str, list[str]],
    ) -> list[ResourceVersion]:
        """Run a projection-backed query and materialize to `ResourceVersion`s."""
        # Strip system-search-only params before passing to the projection.
        proj_params = {key: value for key, value in params.items() if key != "_type"}
        statement = projection.build_select(proj_params)
        records = session.execute(statement).scalars().all()
        return [version for version in (self._to_version(record) for record in records) if version]

    def _sql_search(
        self,
        session: Session,
        resource_type: str,
        params: dict[str, list[str]],
        *,
        system_search: bool = False,
    ) -> list[ResourceVersion]:
        """Indexed search on Postgres using JSONB `@>` against the GIN index.

        Builds a single statement that:
          1. picks the latest version per `resource_id` via `DISTINCT ON`;
          2. filters by `resource_type` and `deleted = false`;
          3. applies one containment clause per supported search parameter,
             OR'ing across alternative paths/values and AND'ing across params.

        Unknown search parameters are ignored (FHIR lenient handling), which
        is the right behavior for an unindexed filter — full table scans
        triggered by an unknown URL param would be a denial-of-service vector.
        """
        # Push lower-bound _lastUpdated filters *inside* the DISTINCT ON subquery
        # so Postgres can use the B-tree index on last_updated before materialising
        # the "latest version per resource" set.  Only lower-bound predicates are
        # safe to push: ge/gt/sa shrink the scan from the bottom; eq contributes
        # its start bound.  le/lt/eb/ne have no safe lower bound — pushing them
        # inside could promote an older version to "latest" within the filtered
        # set and return wrong results.  The full outer filters below still apply
        # for correctness; the inner filters are purely a performance hint.
        last_updated_values = split_csv_values(params.get("_lastUpdated", []))
        inner_last_updated_filters: list[ColumnElement[bool]] = []
        for last_updated_value in last_updated_values:
            try:
                prefix, start, end = parse_date_param(last_updated_value)
            except ValueError:
                continue
            if prefix == "ge":
                inner_last_updated_filters.append(ResourceVersionRecord.last_updated >= start)
            elif prefix in ("gt", "sa"):
                inner_last_updated_filters.append(ResourceVersionRecord.last_updated > end)
            elif prefix == "eq":
                inner_last_updated_filters.append(ResourceVersionRecord.last_updated >= start)

        latest = (
            select(ResourceVersionRecord)
            .where(ResourceVersionRecord.resource_type == resource_type, *inner_last_updated_filters)
            .order_by(
                ResourceVersionRecord.resource_id,
                ResourceVersionRecord.last_updated.desc(),
                ResourceVersionRecord.id.desc(),
            )
            .distinct(ResourceVersionRecord.resource_id)
            .subquery()
        )
        LatestRV = aliased(ResourceVersionRecord, latest)

        # `filters` mixes simple comparisons (`is_`, `in_`) and combined
        # boolean expressions (`or_`); declare the widest common return type
        # so mypy doesn't narrow the list to `BinaryExpression` from the
        # first append.
        filters: list[ColumnElement[bool]] = [LatestRV.deleted.is_(False)]

        id_values = split_csv_values(params.get("_id", []))
        if id_values:
            filters.append(LatestRV.resource_id.in_(id_values))

        for last_updated_value in last_updated_values:
            clause = _last_updated_sql_clause(LatestRV.last_updated, last_updated_value)
            if clause is not None:
                filters.append(clause)

        for key, raw_values in params.items():
            # `_count` is now in IGNORED_SEARCH_PARAMS; `_id` was already
            # consumed above and `containment_payloads` returns [] for it
            # anyway, so the only thing to skip here is the ignored set.
            if key in IGNORED_SEARCH_PARAMS or key == "_id":
                continue
            if system_search and key == "_type":
                continue
            values = split_csv_values(raw_values)
            if not values:
                continue
            payloads = containment_payloads(key, values)
            if not payloads:
                continue
            or_clauses = [
                LatestRV.content.op("@>")(bindparam(None, payload, type_=JSONB))
                for payload in payloads
            ]
            filters.append(or_(*or_clauses))

        statement = (
            select(LatestRV)
            .where(*filters)
            .order_by(LatestRV.last_updated.desc(), LatestRV.id.desc())
        )
        records = session.execute(statement).scalars().all()
        versions = [version for version in (self._to_version(record) for record in records) if version]
        return _apply_python_sort(versions, params)

    def _python_search(
        self,
        session: Session,
        resource_type: str,
        params: dict[str, list[str]],
        *,
        system_search: bool = False,
    ) -> list[ResourceVersion]:
        """SQLite fallback: filter latest versions in Python.

        Kept around so the unit-test suite (in-memory SQLite) doesn't depend
        on a real Postgres. Not used in production.
        """
        id_values = split_csv_values(params.get("_id", []))
        other_params = {
            key: value
            for key, value in params.items()
            if key not in IGNORED_SEARCH_PARAMS and key != "_id"
        }
        if id_values and not other_params:
            results: list[ResourceVersion] = []
            for resource_id in id_values:
                record = self._latest_record(session, resource_type, resource_id)
                if record and not record.deleted:
                    version = self._to_version(record)
                    if version:
                        results.append(version)
            return _apply_last_updated_filter(results, params)

        records = self._latest_records_for_type(session, resource_type)
        matches: list[ResourceVersion] = []
        for record in records:
            if record.deleted:
                continue
            version = self._to_version(record)
            if version and resource_matches(
                version.resource or {}, params, system_search=system_search
            ):
                matches.append(version)
        filtered = _apply_last_updated_filter(matches, params)
        return _apply_python_sort(filtered, params)

    def history(
        self,
        resource_type: str | None = None,
        resource_id: str | None = None,
        *,
        since: datetime | None = None,
        at: datetime | None = None,
        offset: int = 0,
        page_size: int | None = None,
    ) -> tuple[list[ResourceVersion], int]:
        """Return (page, total).

        Two queries run inside one session:
        - COUNT(*) with the WHERE filters — no data transfer, fast even on large tables.
        - SELECT with LIMIT/OFFSET applied in SQL, not Python.

        total is the row count before any Python-level filtering (e.g. security
        labels).  Callers should apply filter_for_user to the returned page only.
        """
        with self._session_factory() as session:
            filters: list[ColumnElement[bool]] = []
            if resource_type is not None:
                filters.append(ResourceVersionRecord.resource_type == resource_type)
            if resource_id is not None:
                filters.append(ResourceVersionRecord.resource_id == resource_id)
            if since is not None:
                filters.append(ResourceVersionRecord.last_updated > since)
            if at is not None:
                filters.append(ResourceVersionRecord.last_updated <= at)

            count_stmt = select(func.count()).select_from(ResourceVersionRecord)
            if filters:
                count_stmt = count_stmt.where(*filters)
            total: int = session.execute(count_stmt).scalar_one()

            page_stmt = (
                select(ResourceVersionRecord)
                .order_by(
                    desc(ResourceVersionRecord.last_updated),
                    desc(ResourceVersionRecord.id),
                )
                .offset(offset)
            )
            if filters:
                page_stmt = page_stmt.where(*filters)
            if page_size is not None:
                page_stmt = page_stmt.limit(page_size)

            records = session.execute(page_stmt).scalars().all()
            return [v for v in (self._to_version(r) for r in records) if v], total

    def resource_types_in_use(self) -> set[str]:
        with self._session_factory() as session:
            statement = select(ResourceVersionRecord.resource_type).distinct()
            return {row for (row,) in session.execute(statement).all()}

    def _latest_record(
        self, session: Session, resource_type: str, resource_id: str
    ) -> ResourceVersionRecord | None:
        statement = (
            select(ResourceVersionRecord)
            .where(
                ResourceVersionRecord.resource_type == resource_type,
                ResourceVersionRecord.resource_id == resource_id,
            )
            .order_by(desc(ResourceVersionRecord.last_updated), desc(ResourceVersionRecord.id))
            .limit(1)
        )
        return session.execute(statement).scalar_one_or_none()

    def _latest_records_for_type(
        self, session: Session, resource_type: str
    ) -> list[ResourceVersionRecord]:
        # Single query: subquery finds the max(id) per resource_id, then join
        # back to get the full row. Uses id as a monotonic proxy for "latest".
        subquery = (
            select(
                ResourceVersionRecord.resource_id,
                func.max(ResourceVersionRecord.id).label("max_id"),
            )
            .where(ResourceVersionRecord.resource_type == resource_type)
            .group_by(ResourceVersionRecord.resource_id)
            .subquery()
        )
        statement = select(ResourceVersionRecord).join(
            subquery, ResourceVersionRecord.id == subquery.c.max_id
        )
        return list(session.execute(statement).scalars().all())

    def _to_version(self, record: ResourceVersionRecord | None) -> ResourceVersion | None:
        if record is None:
            return None
        last_updated = record.last_updated
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=UTC)
        return ResourceVersion(
            version_id=record.version_id,
            resource=copy.deepcopy(record.content) if record.content is not None else None,
            last_updated=last_updated,
            deleted=record.deleted,
            resource_type=record.resource_type,
            resource_id=record.resource_id,
        )

    def _upsert_projection(
        self,
        session: Session,
        resource_type: str,
        resource_id: str,
        version_id: str,
        last_updated: datetime,
        resource: dict[str, Any],
    ) -> None:
        """Keep the projection row for this resource in sync with the new version."""
        projection = projection_registry.for_type(resource_type)
        if projection is None:
            return
        projection.upsert_row(
            session,
            resource_id=resource_id,
            version_id=version_id,
            last_updated=last_updated,
            resource=resource,
        )

    def _delete_projection(
        self,
        session: Session,
        resource_type: str,
        resource_id: str,
    ) -> None:
        """Remove the projection row when the resource is deleted (tombstoned)."""
        projection = projection_registry.for_type(resource_type)
        if projection is None:
            return
        projection.delete_row(session, resource_id)

    # ------------------------------------------------------------------
    # Transaction / batch support
    # ------------------------------------------------------------------

    def execute_transaction(self, operations: list[TransactionOperation]) -> list[TransactionResult]:
        """Execute *operations* atomically in a single session.

        Commits only if every operation succeeds; any exception causes a
        full rollback and is re-raised to the caller.  Cache is refreshed
        after the commit for every write operation.
        """
        with self._lock, self._session_factory() as session:
            results: list[TransactionResult] = []
            for operation in operations:
                results.append(self._execute_transaction_op(session, operation))
            session.commit()

        for operation, result in zip(operations, results, strict=True):
            if isinstance(operation, TransactionRead) or result.version is None:
                continue
            resource_id = (
                (result.version.resource or {}).get("id")      # TransactionCreate: id lives in the result
                or getattr(operation, "resource_id", None)  # TransactionUpdate/Delete: id is on the operation
            )
            if not resource_id:
                continue
            cache.cache_set(
                key=_CACHE_KEY_LATEST.format(operation.resource_type, resource_id),
                value=result.version,
                ttl=self.TTL_LATEST,
                serialize=_resource_version_to_json,
            )
            if not result.version.deleted:
                cache.cache_set(
                    key=_CACHE_KEY_VERSION.format(operation.resource_type, resource_id, result.version.version_id),
                    value=result.version,
                    ttl=self.TTL_VERSION,
                    serialize=_resource_version_to_json,
                )

        return results

    def _execute_transaction_op(self, session: Session, operation: TransactionOperation) -> TransactionResult:
        if isinstance(operation, TransactionCreate):
            return TransactionResult(version=self._create_in_session(session, operation.resource_type, operation.resource), created=True)
        if isinstance(operation, TransactionUpdate):
            version, created = self._update_in_session(session, operation.resource_type, operation.resource_id, operation.resource, if_match=operation.if_match)
            return TransactionResult(version=version, created=created)
        if isinstance(operation, TransactionDelete):
            return TransactionResult(version=self._delete_in_session(session, operation.resource_type, operation.resource_id))
        # TransactionRead
        session.flush()  # make pending writes visible within this transaction
        return TransactionResult(version=self._to_version(self._latest_record(session, operation.resource_type, operation.resource_id)))

    def _create_in_session(
        self, session: Session, resource_type: str, resource: dict[str, Any]
    ) -> ResourceVersion:
        resource_id = uuid.uuid4().hex
        prepared, last_updated = self._prepare(resource_type, resource_id, "1", resource)
        session.add(ResourceVersionRecord(
            resource_type=resource_type,
            resource_id=resource_id,
            version_id="1",
            last_updated=last_updated,
            deleted=False,
            content=prepared,
        ))
        self._upsert_projection(session, resource_type, resource_id, "1", last_updated, prepared)
        return ResourceVersion("1", prepared, last_updated, False, resource_type, resource_id)

    def _update_in_session(
        self,
        session: Session,
        resource_type: str,
        resource_id: str,
        resource: dict[str, Any],
        *,
        if_match: str | None = None,
    ) -> tuple[ResourceVersion, bool]:
        latest = self._latest_record(session, resource_type, resource_id)
        if if_match is not None:
            current_etag = (
                weak_etag(latest.version_id) if (latest is not None and not latest.deleted) else None
            )
            if current_etag is None or if_match != current_etag:
                raise VersionConflictError("If-Match did not match the current resource version")
        created = latest is None or latest.deleted
        next_version_id = str(int(latest.version_id) + 1) if latest else "1"
        prepared, last_updated = self._prepare(resource_type, resource_id, next_version_id, resource)
        session.add(ResourceVersionRecord(
            resource_type=resource_type,
            resource_id=resource_id,
            version_id=next_version_id,
            last_updated=last_updated,
            deleted=False,
            content=prepared,
        ))
        self._upsert_projection(session, resource_type, resource_id, next_version_id, last_updated, prepared)
        return ResourceVersion(next_version_id, prepared, last_updated, False, resource_type, resource_id), created

    def _delete_in_session(
        self, session: Session, resource_type: str, resource_id: str
    ) -> ResourceVersion | None:
        latest = self._latest_record(session, resource_type, resource_id)
        if latest is None or latest.deleted:
            return None
        next_version_id = str(int(latest.version_id) + 1)
        last_updated = now_utc()
        session.add(ResourceVersionRecord(
            resource_type=resource_type,
            resource_id=resource_id,
            version_id=next_version_id,
            last_updated=last_updated,
            deleted=True,
            content=None,
        ))
        self._delete_projection(session, resource_type, resource_id)
        return ResourceVersion(next_version_id, None, last_updated, True, resource_type, resource_id)

    def _prepare(
        self,
        resource_type: str,
        resource_id: str,
        version_id: str,
        resource: dict[str, Any],
    ) -> tuple[dict[str, Any], datetime]:
        prepared = copy.deepcopy(resource)
        prepared["resourceType"] = resource_type
        prepared["id"] = resource_id
        meta = dict(prepared.get("meta") or {})
        last_updated = now_utc()
        meta["versionId"] = version_id
        meta["lastUpdated"] = fhir_instant(last_updated)
        prepared["meta"] = meta
        return prepared, last_updated


store = FHIRStore()
