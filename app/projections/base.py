"""Projection ABC + registry + shared search/extract helpers.

A *projection* is a denormalized index table for one FHIR resource type. It
exists to answer the question "which resource ids of type T match this set
of FHIR search params?" in pure SQL with B-tree indexes, instead of doing
a JSONB containment scan over `resource_versions`.

Contract for subclasses
-----------------------

A concrete projection must override::

    resource_type: str                # e.g. "Patient"
    table: type[BaseProjection]        # SQLAlchemy projection model

    def extract(resource: dict) -> dict[str, Any]:
        '''Return projection-column values from a FHIR resource dict.'''

And may override the optional class-attribute mappings::

    TOKEN_PARAMS: dict[str, str]   # FHIR token param -> projection column
    STRING_PARAMS: dict[str, str]  # FHIR string param -> projection column (case-insensitive)
    REFERENCE_PARAMS: dict[str, tuple[str, str]]  # param -> (column, default_type)
    DATE_PARAMS: dict[str, str]    # FHIR date param -> projection column
    NUMBER_PARAMS: dict[str, str]  # FHIR number param -> projection column

These maps drive :meth:`Projection.build_select`, which converts the request
`params` into a `SELECT` statement that joins back to
`resource_versions` to return the full FHIR JSON of the latest version.

Anything not listed in those maps counts as *unsupported*; if a request
includes an unsupported param, :meth:`Projection.supports` returns False and
the store falls back to the slower JSONB containment path. That keeps result
correctness honest -- we never silently drop a filter that the caller asked
for.
"""

from __future__ import annotations

import functools
from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from sqlalchemy import Select, and_, asc, delete, desc, func, insert, not_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, aliased

from app.db.models import ResourceVersionRecord
from app.db.projection_models import BaseProjection
from app.utils.fhir.constants import IGNORED_SEARCH_PARAMS
from app.utils.fhir.date_search import parse_date_param
from app.utils.fhir.search import parse_sort_params, split_csv_values

# Search-param prefixes supported on date / number params.
# https://hl7.org/fhir/R5/search.html#prefix
_PREFIX_OPS = {
    "eq": lambda column, value: column == value,
    "ne": lambda column, value: column != value,
    "gt": lambda column, value: column > value,
    "lt": lambda column, value: column < value,
    "ge": lambda column, value: column >= value,
    "le": lambda column, value: column <= value,
    # `sa` / `eb` (starts-after / ends-before) collapse to `gt` / `lt` for
    # point-in-time columns. Full period semantics would need two columns.
    "sa": lambda column, value: column > value,
    "eb": lambda column, value: column < value,
}


def _parse_prefix(raw: str) -> tuple[str, str]:
    """Split `ge2025-01-01` into `("ge", "2025-01-01")`.

    Defaults to `eq` if no recognised prefix is present.
    """
    if len(raw) >= 2 and raw[:2] in _PREFIX_OPS:
        return raw[:2], raw[2:]
    return "eq", raw


def _parse_date(value: str) -> datetime | date | None:
    """Parse a FHIR date / dateTime to the most precise Python type we can.

    Returns `date` for `YYYY-MM-DD` and `datetime` for full instants.
    Returns `None` on parse failure so the caller can drop the filter.
    """
    if not value:
        return None
    try:
        if "T" in value:
            # FHIR allows trailing Z; Python <3.11 didn't, but we do here.
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_number(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _last_updated_clause(column: Any, value: str) -> Any:
    """Build a SQLAlchemy WHERE clause for one `_lastUpdated` value.

    Uses FHIR period semantics: partial dates expand to their implied period
    so `eq2024-01-15` matches any instant within that day, not just midnight.
    Returns `None` if the value cannot be parsed.
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


class Projection(ABC):
    """Base class for a per-resource-type search projection."""

    # Override on the subclass.
    resource_type: ClassVar[str] = ""
    table: ClassVar[type[BaseProjection]]

    # Search-param -> projection-column maps. Empty by default.
    TOKEN_PARAMS: ClassVar[dict[str, str]] = {}
    STRING_PARAMS: ClassVar[dict[str, str]] = {}
    REFERENCE_PARAMS: ClassVar[dict[str, tuple[str, str]]] = {}
    DATE_PARAMS: ClassVar[dict[str, str]] = {}
    NUMBER_PARAMS: ClassVar[dict[str, str]] = {}
    # Boolean columns need string→bool conversion ("true"/"false" query params
    # map to Python True/False before the SQL comparison).
    BOOL_PARAMS: ClassVar[dict[str, str]] = {}

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @abstractmethod
    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        """Return the column values to upsert into the projection table.

        Keys must match the projection model's column names. Missing keys
        default to `None` on insert.
        """

    # ------------------------------------------------------------------
    # Search support
    # ------------------------------------------------------------------

    @functools.cached_property
    def supported_params(self) -> set[str]:
        """Set of FHIR search params this projection can answer, lower-cased.

        Always includes `_id` and `_lastupdated` -- those are served by
        the primary key / `last_updated` columns that every projection has.

        Computed once per instance (projection singletons) and cached.
        """
        params: set[str] = {"_id", "_lastupdated"}
        params.update(param.lower() for param in self.TOKEN_PARAMS)
        params.update(param.lower() for param in self.STRING_PARAMS)
        params.update(param.lower() for param in self.REFERENCE_PARAMS)
        params.update(param.lower() for param in self.DATE_PARAMS)
        params.update(param.lower() for param in self.NUMBER_PARAMS)
        params.update(param.lower() for param in self.BOOL_PARAMS)
        return params

    def supports(self, params: dict[str, list[str]]) -> bool:
        """True iff every non-control param in `params` is supported.

        Control params like `_count` and `_format` (see
        :data:`app.utils.constants.IGNORED_SEARCH_PARAMS`) are skipped, so they
        never disqualify the projection.
        """
        supported = self.supported_params
        for key, raw_values in params.items():
            if key in IGNORED_SEARCH_PARAMS:
                continue
            if not split_csv_values(raw_values):
                continue
            if key.lower() not in supported:
                return False
        return True

    def build_select(self, params: dict[str, list[str]]) -> Select[Any]:
        """Build a SELECT that returns the matching `ResourceVersionRecord` rows.

        Joins the projection (filtered by `params`) back to
        `resource_versions` on `(resource_type, resource_id, version_id)`
        so the caller can fetch the full FHIR JSON. Results are ordered
        according to `_sort` params; falls back to `last_updated DESC`
        when `_sort` is absent or names only unknown columns.
        """
        projection_alias = aliased(self.table)
        projection_table = projection_alias.__table__
        clauses: list[Any] = []

        for key, raw_values in params.items():
            if key in IGNORED_SEARCH_PARAMS:
                continue
            values = split_csv_values(raw_values)
            if not values:
                continue
            clause = self._param_clause(projection_table, key, values)
            if clause is not None:
                clauses.append(clause)

        # _lastUpdated is in IGNORED_SEARCH_PARAMS (to keep it out of the JSONB
        # containment loop and resource_matches), so it must be handled here
        # explicitly to push the filter into SQL against the indexed column.
        for last_updated_value in split_csv_values(params.get("_lastUpdated", [])):
            clause = _last_updated_clause(projection_table.c.last_updated, last_updated_value)
            if clause is not None:
                clauses.append(clause)

        resource_version = aliased(ResourceVersionRecord)
        statement = (
            select(resource_version)
            .select_from(
                projection_table.join(
                    resource_version,
                    and_(
                        resource_version.resource_type == self.resource_type,
                        resource_version.resource_id == projection_table.c.resource_id,
                        resource_version.version_id == projection_table.c.version_id,
                    ),
                )
            )
            .where(*clauses, resource_version.deleted.is_(False))
            .order_by(*self._sort_columns(projection_table, params))
        )
        return statement

    def _sort_columns(self, projection_table: Any, params: dict[str, list[str]]) -> list[Any]:
        """Map `_sort` params to SQL ORDER BY expressions on the projection table.

        Known FHIR params are mapped to their projection columns. Unknown params
        are silently skipped. Falls back to `last_updated DESC` when no params
        are present or none can be mapped.  Always appends `resource_id ASC`
        as a stable tiebreaker.
        """
        sort_fields = parse_sort_params(params)

        # Build a flat param→column name lookup across all param type maps.
        sortable: dict[str, str] = {"_id": "resource_id", "_lastupdated": "last_updated"}
        for param, column_name in self.TOKEN_PARAMS.items():
            sortable[param.lower()] = column_name
        for param, column_name in self.STRING_PARAMS.items():
            sortable[param.lower()] = column_name
        for param, column_name in self.DATE_PARAMS.items():
            sortable[param.lower()] = column_name
        for param, column_name in self.NUMBER_PARAMS.items():
            sortable[param.lower()] = column_name
        for param, column_name in self.BOOL_PARAMS.items():
            sortable[param.lower()] = column_name
        for param, (column_name, _) in self.REFERENCE_PARAMS.items():
            sortable[param.lower()] = column_name

        if not sort_fields:
            return [projection_table.c.last_updated.desc(), projection_table.c.resource_id.asc()]

        order: list[Any] = []
        for field, ascending in sort_fields:
            col_name: str | None = sortable.get(field.lower())
            if col_name is None:
                continue
            column = projection_table.c[col_name]
            order.append(asc(column) if ascending else desc(column))

        if not order:
            # None of the requested fields are sortable in SQL; use default.
            return [projection_table.c.last_updated.desc(), projection_table.c.resource_id.asc()]

        order.append(projection_table.c.resource_id.asc())
        return order

    # ------------------------------------------------------------------
    # Write path helpers (called by FHIRStore in the same transaction)
    # ------------------------------------------------------------------

    def upsert_row(
        self,
        session: Session,
        *,
        resource_id: str,
        version_id: str,
        last_updated: datetime,
        resource: dict[str, Any],
    ) -> None:
        """Insert or update one projection row for the given resource."""
        extracted_columns = self.extract(resource)
        extracted_columns.update(
            resource_id=resource_id,
            version_id=version_id,
            last_updated=last_updated,
        )
        self._upsert_rows(session, [extracted_columns])

    def bulk_upsert_rows(
        self,
        session: Session,
        rows: list[dict[str, Any]],
    ) -> None:
        """Insert / update many projection rows in one statement.

        Each row dict must already contain `resource_id`, `version_id`,
        `last_updated` plus the extracted columns. See
        :meth:`FHIRStore.bulk_create`.
        """
        if rows:
            self._upsert_rows(session, rows)

    def delete_row(self, session: Session, resource_id: str) -> None:
        statement = delete(self.table).where(self.table.resource_id == resource_id)
        session.execute(statement)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _upsert_rows(
        self,
        session: Session,
        rows: list[dict[str, Any]],
    ) -> None:
        """Dialect-aware ON CONFLICT DO UPDATE on `resource_id`.

        Each branch is intentionally self-contained -- the dialect-specific
        `Insert` subclasses (`postgresql.dml.Insert` vs
        `sqlite.dml.Insert`) carry their own `excluded` / `on_conflict_*`
        APIs and can't be unified into a single typed local variable.
        """
        dialect = session.bind.dialect.name if session.bind else ""
        columns = self.table.__table__.columns

        if dialect == "postgresql":
            pg_statement = pg_insert(self.table).values(rows)
            session.execute(
                pg_statement.on_conflict_do_update(
                    index_elements=["resource_id"],
                    set_={
                        column_def.name: pg_statement.excluded[column_def.name]
                        for column_def in columns
                        if column_def.name != "resource_id"
                    },
                )
            )
            return

        if dialect == "sqlite":
            sqlite_statement = sqlite_insert(self.table).values(rows)
            session.execute(
                sqlite_statement.on_conflict_do_update(
                    index_elements=["resource_id"],
                    set_={
                        column_def.name: sqlite_statement.excluded[column_def.name]
                        for column_def in columns
                        if column_def.name != "resource_id"
                    },
                )
            )
            return

        # Generic fallback: delete-then-insert. Not concurrency-safe but only
        # hit on exotic dialects we don't ship with.
        resource_ids = [row["resource_id"] for row in rows]
        session.execute(delete(self.table).where(self.table.resource_id.in_(resource_ids)))
        session.execute(insert(self.table).values(rows))

    def _param_clause(
        self,
        projection_table: Any,
        key: str,
        values: list[str],
    ) -> Any:
        """Translate one search param into a SQLAlchemy boolean clause.

        Lookups are done against the lower-cased key to match the contract of
        :meth:`supports` (which also lower-cases): a request like
        `?Gender=male` should hit the same projection column as
        `?gender=male`. All concrete projections declare their param keys
        in lower case so this is safe.

        Returns `None` when the param is irrelevant to this projection;
        :meth:`supports` will already have rejected truly-unknown params, so
        the only return-None case is genuinely unsupported control params.
        """
        lower = key.lower()

        if lower == "_id":
            return projection_table.c.resource_id.in_(values)

        if lower == "_lastupdated":
            return self._range_clause(projection_table.c.last_updated, values, _parse_date)

        if lower in self.TOKEN_PARAMS:
            column = projection_table.c[self.TOKEN_PARAMS[lower]]
            return column.in_(values)

        if lower in self.STRING_PARAMS:
            column = projection_table.c[self.STRING_PARAMS[lower]]
            # FHIR string semantics: "starts with, case-insensitive".
            return or_(
                *[func.lower(column).like(search_value.lower() + "%") for search_value in values]
            )

        if lower in self.REFERENCE_PARAMS:
            column_name, default_type = self.REFERENCE_PARAMS[lower]
            column = projection_table.c[column_name]
            return self._reference_clause(column, values, default_type)

        if lower in self.DATE_PARAMS:
            column = projection_table.c[self.DATE_PARAMS[lower]]
            return self._range_clause(column, values, _parse_date)

        if lower in self.NUMBER_PARAMS:
            column = projection_table.c[self.NUMBER_PARAMS[lower]]
            return self._range_clause(column, values, _parse_number)

        if lower in self.BOOL_PARAMS:
            column = projection_table.c[self.BOOL_PARAMS[lower]]
            _BOOL_MAP = {"true": True, "false": False}
            bool_vals = [
                _BOOL_MAP[search_value.lower()]
                for search_value in values
                if search_value.lower() in _BOOL_MAP
            ]
            if not bool_vals:
                return column != column
            return column.in_(bool_vals)

        return None

    @staticmethod
    def _reference_clause(column: Any, values: list[str], default_type: str) -> Any:
        """Build a reference equality clause that accepts `Type/id` or bare `id`."""
        normalized: list[str] = []
        for value in values:
            if "/" in value:
                normalized.append(value)
            else:
                normalized.append(f"{default_type}/{value}")
        return column.in_(normalized)

    @staticmethod
    def _range_clause(column: Any, values: list[str], parser: Any) -> Any:
        """Build an AND of prefix comparisons (`geX`, `ltY`, etc.) over `column`."""
        parts: list[Any] = []
        for raw in values:
            prefix, body = _parse_prefix(raw)
            parsed = parser(body)
            if parsed is None:
                continue
            comparison_operator = _PREFIX_OPS[prefix]
            parts.append(comparison_operator(column, parsed))
        if not parts:
            # Every value failed to parse. Emit an always-false SQL clause so
            # the query returns no rows rather than silently widening results.
            # `column != column` is always false: for non-NULL values any value
            # equals itself; for NULL, `NULL != NULL` yields NULL which is
            # falsy in a WHERE clause.
            return column != column
        return and_(*parts)


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------


class ProjectionRegistry:
    """Maps FHIR resource types to their :class:`Projection` instance."""

    def __init__(self) -> None:
        self._projections: dict[str, Projection] = {}

    def register(self, projection: Projection) -> None:
        if not projection.resource_type:
            raise ValueError(f"{type(projection).__name__} has no resource_type set")
        self._projections[projection.resource_type] = projection

    def for_type(self, resource_type: str) -> Projection | None:
        return self._projections.get(resource_type)

    def known_types(self) -> list[str]:
        return sorted(self._projections)

    def all(self) -> list[Projection]:
        return list(self._projections.values())


registry = ProjectionRegistry()
