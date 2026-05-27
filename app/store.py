"""SQLAlchemy-backed FHIR resource store."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from sqlalchemy import ColumnElement, bindparam, desc, func, insert, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.db.base import SessionLocal, engine
from app.db.models import ResourceVersionRecord
from app.db.search import containment_payloads
from app.projections import registry as projection_registry
from app.utils.fhir.constants import IGNORED_SEARCH_PARAMS
from app.utils.fhir.search import parse_sort_params, resource_matches, split_csv_values
from app.utils.time import fhir_instant, now_utc, weak_etag


def _sort_key_for_field(version: "ResourceVersion", field: str) -> Any:
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
    versions: list["ResourceVersion"],
    params: dict[str, list[str]],
) -> list["ResourceVersion"]:
    """Re-sort ResourceVersions by ``_sort`` params using Python comparison.

    Applies each sort field in reverse order so the first field in ``_sort``
    takes highest priority (Python's stable sort preserves previous orderings).
    """
    sort_fields = parse_sort_params(params)
    if not sort_fields:
        return versions
    result = list(versions)
    for field, ascending in reversed(sort_fields):
        result.sort(
            key=lambda v, f=field: _sort_key_for_field(v, f),
            reverse=not ascending,
        )
    return result


class VersionConflictError(Exception):
    """Raised by FHIRStore.update() when If-Match fails inside the write lock."""


@dataclass(frozen=True)
class ResourceVersion:
    """In-memory representation of a single FHIR resource version."""

    version_id: str
    resource: dict[str, Any] | None
    last_updated: datetime
    deleted: bool = False


class FHIRStore:
    """FHIR resource store backed by SQLAlchemy.

    A single ``resource_versions`` table holds every historical version, with
    deletion tombstones marked by ``deleted=True`` and ``content=None``.
    """

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or SessionLocal
        self._lock = RLock()
        self._is_postgres = engine.dialect.name == "postgresql"

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
            return ResourceVersion("1", prepared, last_updated, False)

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
            return ResourceVersion(next_version_id, prepared, last_updated, False), created

    def delete(self, resource_type: str, resource_id: str) -> ResourceVersion | None:
        with self._lock, self._session_factory() as session:
            latest = self._latest_record(session, resource_type, resource_id)
            if latest is None or latest.deleted:
                return None
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
            return ResourceVersion(next_version_id, None, last_updated, True)

    def bulk_create(
        self,
        rows: Iterable[tuple[str, str, dict[str, Any]]],
        *,
        batch_size: int = 500,
    ) -> int:
        """Bulk insert version 1 records using batched INSERTs.

        Each ``rows`` item is ``(resource_type, resource_id, resource_dict)``.
        The store fills in ``versionId="1"``, ``meta.lastUpdated``, and the
        ``last_updated`` column. Duplicates (same resource_type/resource_id/
        version_id) are silently skipped via ``ON CONFLICT DO NOTHING``, which
        keeps the bulk seeder idempotent without requiring the caller to
        truncate first.

        Returns the number of rows submitted to the database (i.e. the count
        of ``rows`` consumed -- not necessarily the count actually inserted,
        because psycopg's ``rowcount`` is unreliable for multi-row
        ``INSERT ... ON CONFLICT`` statements). Callers that need an exact
        "inserted vs skipped" breakdown should ``SELECT COUNT(*)`` before and
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

            def flush(b: list[dict[str, Any]]) -> None:
                if not b:
                    return
                # Each branch builds and executes its own statement: the
                # dialect-specific Insert subclasses can't share a typed local.
                if dialect == "postgresql":
                    session.execute(
                        pg_insert(ResourceVersionRecord)
                        .values(b)
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
                        .values(b)
                        .on_conflict_do_nothing(
                            index_elements=[
                                "resource_type",
                                "resource_id",
                                "version_id",
                            ]
                        )
                    )
                else:
                    session.execute(insert(ResourceVersionRecord).values(b))

            def flush_projections() -> None:
                for rt, proj_rows in projection_batches.items():
                    projection = projection_registry.for_type(rt)
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
                    cols = projection.extract(prepared)
                    cols.update(
                        resource_id=resource_id,
                        version_id="1",
                        last_updated=last_updated,
                    )
                    projection_batches.setdefault(resource_type, []).append(cols)
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

    def latest(self, resource_type: str, resource_id: str) -> ResourceVersion | None:
        with self._session_factory() as session:
            record = self._latest_record(session, resource_type, resource_id)
            return self._to_version(record)

    def version(
        self, resource_type: str, resource_id: str, version_id: str
    ) -> ResourceVersion | None:
        with self._session_factory() as session:
            stmt = select(ResourceVersionRecord).where(
                ResourceVersionRecord.resource_type == resource_type,
                ResourceVersionRecord.resource_id == resource_id,
                ResourceVersionRecord.version_id == version_id,
            )
            return self._to_version(session.execute(stmt).scalar_one_or_none())

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
                stmt = select(ResourceVersionRecord.resource_type).distinct()
                resource_types = [row for (row,) in session.execute(stmt).all()]

            matches: list[ResourceVersion] = []
            for resource_type in resource_types:
                matches.extend(
                    self._dispatch_search(session, resource_type, params, system_search=True)
                )
            return matches

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
          1. Projection-table SQL search if one exists for ``resource_type``
             *and* it covers every requested param. Hits B-tree indexes only.
          2. JSONB containment over ``resource_versions`` (Postgres only).
          3. Python fallback (SQLite, used by unit tests).

        When a projection partially covers a request, we deliberately fall
        through instead of mixing strategies: a JSONB filter on the unsupported
        param plus a projection JOIN would either need a second JOIN or an
        ``IN (subquery)`` rewrite, and the cost of the JSONB scan dominates
        either way. Cleaner to let the JSONB path own those requests.
        """
        # Projection fast path: works for both direct and system search because
        # _dispatch_search is already called once per resource_type, and each
        # projection's build_select filters on its own resource_type in the JOIN.
        projection = projection_registry.for_type(resource_type)
        if projection is not None and projection.supports(
            {k: v for k, v in params.items() if k != "_type"}
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
        """Run a projection-backed query and materialize to ``ResourceVersion``s."""
        # Strip system-search-only params before passing to the projection.
        proj_params = {k: v for k, v in params.items() if k != "_type"}
        stmt = projection.build_select(proj_params)
        records = session.execute(stmt).scalars().all()
        return [v for v in (self._to_version(r) for r in records) if v]

    def _sql_search(
        self,
        session: Session,
        resource_type: str,
        params: dict[str, list[str]],
        *,
        system_search: bool = False,
    ) -> list[ResourceVersion]:
        """Indexed search on Postgres using JSONB ``@>`` against the GIN index.

        Builds a single statement that:
          1. picks the latest version per ``resource_id`` via ``DISTINCT ON``;
          2. filters by ``resource_type`` and ``deleted = false``;
          3. applies one containment clause per supported search parameter,
             OR'ing across alternative paths/values and AND'ing across params.

        Unknown search parameters are ignored (FHIR lenient handling), which
        is the right behavior for an unindexed filter — full table scans
        triggered by an unknown URL param would be a denial-of-service vector.
        """
        latest = (
            select(ResourceVersionRecord)
            .where(ResourceVersionRecord.resource_type == resource_type)
            .order_by(
                ResourceVersionRecord.resource_id,
                ResourceVersionRecord.last_updated.desc(),
                ResourceVersionRecord.id.desc(),
            )
            .distinct(ResourceVersionRecord.resource_id)
            .subquery()
        )
        LatestRV = aliased(ResourceVersionRecord, latest)

        # ``filters`` mixes simple comparisons (``is_``, ``in_``) and combined
        # boolean expressions (``or_``); declare the widest common return type
        # so mypy doesn't narrow the list to ``BinaryExpression`` from the
        # first append.
        filters: list[ColumnElement[bool]] = [LatestRV.deleted.is_(False)]

        id_values = split_csv_values(params.get("_id", []))
        if id_values:
            filters.append(LatestRV.resource_id.in_(id_values))

        for key, raw_values in params.items():
            # ``_count`` is now in IGNORED_SEARCH_PARAMS; ``_id`` was already
            # consumed above and ``containment_payloads`` returns [] for it
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

        stmt = (
            select(LatestRV)
            .where(*filters)
            .order_by(LatestRV.last_updated.desc(), LatestRV.id.desc())
        )
        records = session.execute(stmt).scalars().all()
        versions = [v for v in (self._to_version(r) for r in records) if v]
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
            k: v
            for k, v in params.items()
            if k not in IGNORED_SEARCH_PARAMS and k != "_id"
        }
        if id_values and not other_params:
            results: list[ResourceVersion] = []
            for rid in id_values:
                rec = self._latest_record(session, resource_type, rid)
                if rec and not rec.deleted:
                    v = self._to_version(rec)
                    if v:
                        results.append(v)
            return results

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
        return _apply_python_sort(matches, params)

    def history(
        self,
        resource_type: str | None = None,
        resource_id: str | None = None,
        *,
        limit: int | None = None,
    ) -> list[ResourceVersion]:
        with self._session_factory() as session:
            stmt = select(ResourceVersionRecord).order_by(
                desc(ResourceVersionRecord.last_updated),
                desc(ResourceVersionRecord.id),
            )
            if resource_type is not None:
                stmt = stmt.where(ResourceVersionRecord.resource_type == resource_type)
            if resource_id is not None:
                stmt = stmt.where(ResourceVersionRecord.resource_id == resource_id)
            if limit is not None:
                stmt = stmt.limit(limit)
            records = session.execute(stmt).scalars().all()
            return [v for v in (self._to_version(record) for record in records) if v]

    def resource_types_in_use(self) -> set[str]:
        with self._session_factory() as session:
            stmt = select(ResourceVersionRecord.resource_type).distinct()
            return {row for (row,) in session.execute(stmt).all()}

    def _latest_record(
        self, session: Session, resource_type: str, resource_id: str
    ) -> ResourceVersionRecord | None:
        stmt = (
            select(ResourceVersionRecord)
            .where(
                ResourceVersionRecord.resource_type == resource_type,
                ResourceVersionRecord.resource_id == resource_id,
            )
            .order_by(desc(ResourceVersionRecord.last_updated), desc(ResourceVersionRecord.id))
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()

    def _latest_records_for_type(
        self, session: Session, resource_type: str
    ) -> list[ResourceVersionRecord]:
        # Single query: subquery finds the max(id) per resource_id, then join
        # back to get the full row. Uses id as a monotonic proxy for "latest".
        subq = (
            select(
                ResourceVersionRecord.resource_id,
                func.max(ResourceVersionRecord.id).label("max_id"),
            )
            .where(ResourceVersionRecord.resource_type == resource_type)
            .group_by(ResourceVersionRecord.resource_id)
            .subquery()
        )
        stmt = select(ResourceVersionRecord).join(
            subq, ResourceVersionRecord.id == subq.c.max_id
        )
        return list(session.execute(stmt).scalars().all())

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
