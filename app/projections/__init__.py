"""Per-resource-type search projections.

Phase B of the search-performance work: every supported resource type owns a
small denormalized "index" table whose columns mirror the FHIR search params
we want to be able to filter and sort by in raw SQL. The full FHIR JSON
remains the source of truth in `resource_versions`; projections are an
in-database cache that the store keeps in sync inside every write
transaction.

The framework lives in :mod:`app.projections.base`:

* :class:`~app.projections.base.Projection` is the abstract per-type contract
  (extract columns from a resource, declare which FHIR search params it can
  serve, build a SQLAlchemy query against the index).
* :data:`~app.projections.base.registry` is the lookup map populated at
  import time.

Concrete projections live in sibling modules and register themselves on
import. Importing :mod:`app.projections` (this package) loads them all.
"""

from __future__ import annotations

# Importing each concrete projection registers it into `registry`.
from app.projections import (  # noqa: F401  (import-for-side-effects)
    allergyintolerance,
    condition,
    diagnosticreport,
    encounter,
    medicationrequest,
    observation,
    organization,
    patient,
    practitioner,
    procedure,
)
from app.projections.base import Projection, ProjectionRegistry, registry

__all__ = ["Projection", "ProjectionRegistry", "registry"]
