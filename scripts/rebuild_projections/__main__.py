"""Rebuild every projection table from `resource_versions`.

Use this command after:

* Adding a new projection column (the projection schema changed but old rows
  still carry the old shape).
* Manual SQL writes that bypassed the store (e.g. `COPY` from a backup).
* A bulk seed run from an older version of the code that didn't keep the
  projections in sync (none currently — :mod:`scripts.seed` writes them
  inside its transactions — but the option is useful belt-and-braces).

The rebuild reads :class:`~app.db.models.ResourceVersionRecord` in chunks so
it works on tables of any size. For each projection-managed type we pull the
*latest non-deleted* version per `resource_id` and upsert the corresponding
projection row. Deleted resources are skipped, which is the right behavior:
projections only mirror the current state.

Usage::

    python -m scripts.rebuild_projections                 # all types
    python -m scripts.rebuild_projections --only Patient  # one type
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Make `app` importable when invoked via `python -m scripts.rebuild_projections`.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://fhir:fhir@localhost:55432/fhir")

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.base import SessionLocal, engine, init_db  # noqa: E402
from app.db.models import ResourceVersionRecord  # noqa: E402
from app.projections import registry as projection_registry  # noqa: E402
from app.projections.base import Projection  # noqa: E402

# Big enough to amortize round-trips, small enough that the in-flight Python
# dicts don't blow up RSS even at billions of rows.
_BATCH_SIZE = 1000


def _latest_versions_query(resource_type: str):
    """Latest non-deleted version per `resource_id` for one resource type.

    On Postgres we lean on `DISTINCT ON (resource_id)` + an `ORDER BY` that
    pushes the highest `id` first per resource. SQLite doesn't have
    `DISTINCT ON`; the fallback is a `GROUP BY ... HAVING MAX(id)` join,
    which produces the same row set with one extra query plan node.
    """
    if engine.dialect.name == "postgresql":
        return (
            select(ResourceVersionRecord)
            .where(ResourceVersionRecord.resource_type == resource_type)
            .order_by(
                ResourceVersionRecord.resource_id,
                ResourceVersionRecord.last_updated.desc(),
                ResourceVersionRecord.id.desc(),
            )
            .distinct(ResourceVersionRecord.resource_id)
            .execution_options(stream_results=True)
        )

    # SQLite path: subquery for max(id) per resource_id, then join back.
    from sqlalchemy import func

    subq = (
        select(
            ResourceVersionRecord.resource_id,
            func.max(ResourceVersionRecord.id).label("max_id"),
        )
        .where(ResourceVersionRecord.resource_type == resource_type)
        .group_by(ResourceVersionRecord.resource_id)
        .subquery()
    )
    return select(ResourceVersionRecord).join(subq, ResourceVersionRecord.id == subq.c.max_id)


def _rebuild_one(session: Session, projection: Projection) -> tuple[int, int]:
    """Repopulate the projection table for one resource type.

    Returns `(scanned, upserted)`. Tombstones (`deleted = True`) are
    scanned but never upserted; the projection should not mirror a deleted
    resource.
    """
    # Clear-and-rebuild semantics so columns dropped from `extract` don't
    # leak old values forward. Cheaper than per-row upsert when most rows
    # actually change.
    session.execute(delete(projection.table))

    stmt = _latest_versions_query(projection.resource_type)
    batch: list[dict] = []
    scanned = 0
    upserted = 0

    for record in session.execute(stmt).scalars().yield_per(_BATCH_SIZE):
        scanned += 1
        if record.deleted or record.content is None:
            continue
        cols = projection.extract(record.content)
        cols.update(
            resource_id=record.resource_id,
            version_id=record.version_id,
            last_updated=record.last_updated,
        )
        batch.append(cols)
        if len(batch) >= _BATCH_SIZE:
            projection.bulk_upsert_rows(session, batch)
            upserted += len(batch)
            batch = []

    if batch:
        projection.bulk_upsert_rows(session, batch)
        upserted += len(batch)

    session.commit()
    return scanned, upserted


def rebuild(only: list[str] | None = None, out=sys.stdout) -> int:
    init_db()

    targets: list[Projection] = []
    if only:
        for rt in only:
            projection = projection_registry.for_type(rt)
            if projection is None:
                print(
                    f"No projection registered for {rt!r}. "
                    f"Known: {projection_registry.known_types()}",
                    file=sys.stderr,
                )
                return 1
            targets.append(projection)
    else:
        targets = projection_registry.all()

    grand_scanned = 0
    grand_upserted = 0
    started = time.perf_counter()

    for projection in targets:
        rt_start = time.perf_counter()
        with SessionLocal() as session:
            scanned, upserted = _rebuild_one(session, projection)
        grand_scanned += scanned
        grand_upserted += upserted
        rt_elapsed = time.perf_counter() - rt_start
        rate = upserted / rt_elapsed if rt_elapsed else 0.0
        print(
            f"{projection.resource_type:<25} scanned={scanned:>8d} "
            f"upserted={upserted:>8d} ({rate:>6.0f}/s)",
            file=out,
        )

    elapsed = time.perf_counter() - started
    print(
        f"\nRebuild complete: {grand_upserted} projection rows across "
        f"{len(targets)} type(s) in {elapsed:.1f}s",
        file=out,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.rebuild_projections",
        description=(
            "Repopulate every projection table from the current state of resource_versions."
        ),
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="ResourceType",
        help="Restrict the rebuild to one or more resource types (repeatable).",
    )
    args = parser.parse_args(argv)
    return rebuild(only=args.only or None)


if __name__ == "__main__":
    raise SystemExit(main())
