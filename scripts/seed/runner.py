"""CLI for the bulk FHIR R5 seeder.

Generation happens in :mod:`scripts.seed.generator`; this module just wires
`argparse` to it and pushes the resulting tuples through
:meth:`app.store.FHIRStore.bulk_create` for a fast batched write path.

Examples
--------
::

    # Default: scale 1 (~70k rows) into the docker-compose Postgres
    python -m scripts.seed

    # Quick smoke load (~700 rows)
    python -m scripts.seed --scale 0.01

    # Larger workload, custom RNG seed, wipe before loading
    python -m scripts.seed --scale 5 --seed 17 --clear

    # Only Patient + Observation (their dependencies are pulled in
    # automatically so the references resolve)
    python -m scripts.seed --only Patient --only Observation

The seeder writes directly through the SQLAlchemy store and therefore
**bypasses the FastAPI route layer**. Registered `ResourceHooks` will not
fire during seeding. Use the HTTP API if you need hook side-effects.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path

# Project root has to be importable when running `python -m scripts.seed`.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# `app.db.base` reads DATABASE_URL at import time, so default to the
# docker-compose Postgres on the host's 55432 port (host 5432 is often
# claimed by a native Postgres install on Windows).
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://fhir:fhir@localhost:55432/fhir"
)

from sqlalchemy import text  # noqa: E402

from app.db.base import engine, init_db  # noqa: E402
from app.projections import registry as projection_registry  # noqa: E402
from app.store import store  # noqa: E402
from scripts.seed.generator import (  # noqa: E402
    BulkContext,
    generate,
    known_resource_types,
)

# Tunable: how many rows we hand to the database in one batched INSERT.
# At 7 columns per row, 500 rows means ~3500 bound parameters per statement,
# well under any sane parameter-count limit, and large enough that statement
# overhead is amortized away.
_BATCH_SIZE = 500


def _flush(rows: list[tuple[str, str, dict]]) -> Counter:
    """Bulk-insert `rows` and return a per-type submission counter."""
    per_type: Counter = Counter(rt for rt, _, _ in rows)
    store.bulk_create(rows, batch_size=_BATCH_SIZE)
    return per_type


def _row_count() -> int:
    """Total rows in resource_versions (used to report exact inserted counts)."""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM resource_versions"))
        return int(result.scalar() or 0)


def _clear_db() -> None:
    """Truncate resource_versions plus every projection table."""
    projection_tables = [
        p.table.__tablename__  # type: ignore[attr-defined]
        for p in projection_registry.all()
    ]
    with engine.begin() as conn:
        # TRUNCATE on Postgres is essentially free; on SQLite fall back to
        # DELETE FROM which is also fine for unit tests.
        if engine.dialect.name == "postgresql":
            # `RESTART IDENTITY` so the id sequence starts at 1 again, which
            # matters for the `ix_resource_versions_content_gin` cardinality
            # estimates after a reseed.
            tables = ", ".join(["resource_versions", *projection_tables])
            conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY"))
        else:
            for tbl in ("resource_versions", *projection_tables):
                conn.execute(text(f"DELETE FROM {tbl}"))


def run(
    *,
    scale: float,
    seed: int,
    clear: bool,
    dry_run: bool,
    only_types: list[str] | None,
    out=sys.stdout,
) -> int:
    if scale <= 0:
        print(f"--scale must be positive (got {scale}); nothing to do.", file=sys.stderr)
        return 1

    only_set = set(only_types) if only_types else None
    if only_set:
        unknown = only_set - set(known_resource_types())
        if unknown:
            print(
                f"--only got unknown resource type(s): {sorted(unknown)}. "
                f"Known types: {known_resource_types()}",
                file=sys.stderr,
            )
            return 1

    if not dry_run:
        init_db()
        if clear:
            _clear_db()
            print("Cleared resource_versions.", file=out)

    rows_before = _row_count() if not dry_run else 0

    ctx = BulkContext(scale=scale, seed=seed)

    per_type_total: Counter = Counter()
    batch: list[tuple[str, str, dict]] = []
    start = time.perf_counter()

    for resource_type, resource_id, payload in generate(ctx, only_set):
        per_type_total[resource_type] += 1
        if dry_run:
            continue
        batch.append((resource_type, resource_id, payload))
        if len(batch) >= _BATCH_SIZE:
            _flush(batch)
            batch = []
            elapsed = time.perf_counter() - start
            generated = sum(per_type_total.values())
            rate = generated / elapsed if elapsed else 0.0
            print(
                f"  ... generated {generated:>7d} rows ({rate:>6.0f}/s)",
                file=out,
            )

    if batch and not dry_run:
        _flush(batch)

    elapsed = time.perf_counter() - start
    total = sum(per_type_total.values())
    rate = total / elapsed if elapsed else 0.0

    print(file=out)
    print(f"{'ResourceType':<30} {'count':>10}", file=out)
    print(f"{'-' * 30} {'-' * 10}", file=out)
    for rt in known_resource_types():
        count = per_type_total.get(rt, 0)
        if count:
            print(f"{rt:<30} {count:>10d}", file=out)
    print(f"{'-' * 30} {'-' * 10}", file=out)
    print(f"{'TOTAL':<30} {total:>10d}", file=out)
    if dry_run:
        print(
            f"\nDry run: validated {total} resources in {elapsed:.1f}s ({rate:.0f}/s). Nothing written.",
            file=sys.stderr,
        )
    else:
        rows_after = _row_count()
        inserted = rows_after - rows_before
        skipped = total - inserted
        skip_note = (
            f"; {skipped} skipped as duplicates (ON CONFLICT DO NOTHING)."
            if skipped > 0
            else "."
        )
        print(
            f"\nGenerated {total} resources in {elapsed:.1f}s ({rate:.0f}/s); "
            f"{inserted} inserted into resource_versions{skip_note}",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.seed",
        description=(
            "Bulk-generate Faker-driven FHIR R5 data and write it through the "
            "SQLAlchemy store. Re-runs are idempotent (ON CONFLICT DO NOTHING)."
        ),
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help=(
            "Multiplier applied to PLAN counts (default: %(default)s, "
            "approx 70k rows total)."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Integer seed for Faker + random (default: %(default)s).",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="TRUNCATE resource_versions before seeding (Postgres) / DELETE on SQLite.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and validate without writing to the database.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="ResourceType",
        help=(
            "Restrict the seed to one or more resource types (repeatable). "
            "Transitive dependencies are pulled in automatically so references "
            "still resolve."
        ),
    )
    args = parser.parse_args(argv)
    return run(
        scale=args.scale,
        seed=args.seed,
        clear=args.clear,
        dry_run=args.dry_run,
        only_types=args.only or None,
    )
