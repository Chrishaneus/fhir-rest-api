# FHIR REST Layer

A small Python FastAPI implementation of the FHIR R5 RESTful HTTP interaction pattern from https://www.hl7.org/fhir/http.html.

This is a development-ready REST layer, not a production clinical system. Add authentication, authorization, consent checks, audit logging, and full FHIR profile validation before using it with real healthcare data.

## Project Layout

```
app/
  main.py              FastAPI app + route handlers
  store.py             FHIRStore (SQLAlchemy-backed resource versions + projection writes)
  hooks.py             ResourceHooks + HookRegistry for side effects
  db/
    base.py            Engine, sessionmaker, init_db()/reset_db()
    models.py          SQLAlchemy ORM (resource_versions table)
    projection_models.py  Per-type search projection tables (Phase B)
  projections/
    __init__.py        Auto-registration of every concrete projection
    base.py            Projection ABC + registry + dialect-aware upsert
    helpers.py         Shared extractors (first_name_part, references, dates, ...)
    patient.py, observation.py, encounter.py, ... (one per resource type)
  utils/
    constants.py       FHIR constants and patterns
    time.py            now_utc, fhir_instant, http_date, weak_etag
    outcomes.py        OperationOutcome + fhir+json response helper
    errors.py          FHIRHTTPError + exception handlers
    fhir_models.py     R5 resource type list + fhir.resources validator
    validation.py      Resource-type/id checks + request body validation
    headers.py         ETag/Last-Modified/Prefer/conditional helpers
    search.py          Search param parsing and resource matching
    jsonb_search.py    FHIR -> JSONB containment-template fallback (Phase A)
    bundles.py         Bundle (searchset / history) builder
    capability.py      CapabilityStatement builder
scripts/
  seed/                Bulk Faker-driven seeder (uses FHIRStore.bulk_create)
  rebuild_projections.py  Reproject the index tables from resource_versions
tests/
  conftest.py          Per-test DB reset + hook reset
  test_fhir_api.py     REST interaction tests
  test_hooks.py        Hook lifecycle tests
  test_projections.py  Projection write path + search routing tests
```

## Supported Interactions

- `GET /metadata` returns a FHIR `CapabilityStatement`.
- `POST /{ResourceType}` creates a resource with a server-assigned id.
- `GET /{ResourceType}/{id}` reads the latest version.
- `GET /{ResourceType}/{id}/_history/{vid}` reads a specific version.
- `PUT /{ResourceType}/{id}` updates an existing resource or creates it with a client-defined id.
- `DELETE /{ResourceType}/{id}` deletes a resource.
- `GET /{ResourceType}` and `POST /{ResourceType}/_search` return a `Bundle` of type `searchset`.
- `GET /{ResourceType}/_history`, `GET /{ResourceType}/{id}/_history`, and `GET /_history` return history bundles.
- `GET /?...` supports system search, with optional `_type`.

Responses use `application/fhir+json`, weak ETags like `W/"1"`, `Last-Modified`, `Location`, `OperationOutcome` for FHIR-aware errors, and `Bundle` resources for search/history.

## FHIR R5 Resource Models

All FHIR R5 resource types listed at https://build.fhir.org/resourcelist.html are recognised by the server (158 types). Validation is delegated to the [`fhir.resources`](https://pypi.org/project/fhir.resources/) Pydantic v2 models, so:

- `POST`/`PUT` request bodies are validated against the official R5 model for the URL resource type. Invalid payloads return `422 Unprocessable Entity` with an `OperationOutcome`.
- Unknown resource types in the URL return `404 Not Found`.
- Resource-type mismatches between URL and body return `400 Bad Request`.

The resource type list and the validator live in `app/utils/fhir_models.py`. The `validate_request_body` helper in `app/utils/validation.py` is what each route calls before invoking hooks and the store.

If you need to bypass strict validation for a particular resource type (e.g. accepting partial drafts) you can do that inside a `before_create`/`before_update` hook by catching the validation upstream — but normally the FHIR-level rules are exactly what you want here.

## SQLAlchemy Storage

Every interaction is stored in a single `resource_versions` table (current versions and deletion tombstones live alongside historical versions). Configure the database via `DATABASE_URL`:

```powershell
$env:DATABASE_URL = "sqlite:///./fhir.db"        # default
# or, e.g.
$env:DATABASE_URL = "postgresql+psycopg://user:pw@host/db"
```

Tables are created on app startup. Tests use `sqlite:///:memory:` and reset between tests.

## Search Performance

The store implements two complementary acceleration strategies and picks the
cheapest one per request.

### Phase A: JSONB GIN on the resource content

On Postgres the `resource_versions.content` column is `JSONB` with a GIN
(`jsonb_path_ops`) index. `app/utils/jsonb_search.py` maps a curated set of
FHIR search params (`family`, `gender`, `subject`, `code`, `status`, ...) to
JSON containment payloads, and `FHIRStore._sql_search` runs them as
`content @> :payload`. The Python equivalent (`_python_search`) is used on
SQLite for unit tests. Anything not in the curated map is silently dropped
(FHIR `Prefer: handling=lenient`).

### Phase B: per-resource-type projection tables

For the high-volume types, the store also maintains a small denormalized
"index" table per resource type — `patient_index`, `observation_index`,
`encounter_index`, etc. The projection row stores only the search keys plus
a `(resource_id, version_id, last_updated)` back-pointer to the version row,
so reads can hit a B-tree on `(subject_ref, code_code)` or
`(family, gender, birth_date)` instead of probing JSONB.

Currently projected types (the ones that dominate clinical search traffic):

| Resource type        | Indexed columns                                                                     |
| -------------------- | ----------------------------------------------------------------------------------- |
| Patient              | family, given, gender, birth_date, active                                            |
| Practitioner         | family, given, active                                                                |
| Organization         | name, type_code, active                                                              |
| Encounter            | subject_ref, status, class_code, period_start, period_end                            |
| Observation          | subject_ref, encounter_ref, status, code_code, category_code, effective_at, value_quantity_value (composite on subject+code and subject+effective_at) |
| Condition            | subject_ref, encounter_ref, clinical_status, code_code, recorded_date                |
| AllergyIntolerance   | patient_ref, code_code, clinical_status, criticality                                 |
| MedicationRequest    | subject_ref, requester_ref, status, intent, authored_on                              |
| DiagnosticReport     | subject_ref, encounter_ref, status, code_code, effective_at                          |
| Procedure            | subject_ref, encounter_ref, status, code_code, performed_at                          |

#### Write path

Projections are infrastructure, not user-extensible hooks: every store write
(`create`, `update`, `delete`, `bulk_create`) maintains the projection in
the same transaction. On delete, the projection row is removed entirely
(it only mirrors the live state). `bulk_create` batches projection upserts
per resource type for throughput.

#### Read path

`FHIRStore.search` dispatches in this order:

1. **Projection** — used when a projection is registered for the resource type
   *and* every requested search param is listed in its supported set. The
   query joins the projection (filtered + ordered by indexed columns) back
   to `resource_versions` for the JSON content.
2. **JSONB containment** — Postgres-only, used when no projection covers the
   request. Sub-millisecond GIN scan.
3. **Python fallback** — only on SQLite, used by the unit test suite.

If a request mixes supported and unsupported params, we deliberately fall
through to (2) rather than silently dropping the unsupported filter, so
results stay correct.

#### Adding more projections

Each projection is a single small module under `app/projections/`. To add
one for, say, `ServiceRequest`:

1. Add a `ServiceRequestIndex` model in `app/db/projection_models.py` with the
   columns and indexes you want.
2. Add `app/projections/servicerequest.py` defining a `ServiceRequestProjection`
   class — implement `extract(resource_dict) -> {column: value}` and declare
   `TOKEN_PARAMS` / `STRING_PARAMS` / `REFERENCE_PARAMS` / `DATE_PARAMS` /
   `NUMBER_PARAMS` mapping FHIR search params to projection columns.
3. Import that module from `app/projections/__init__.py` so it self-registers.
4. Run `python -m scripts.rebuild_projections --only ServiceRequest` to
   backfill the new table from existing `resource_versions` rows.

#### Limitations

Multi-valued fields (e.g. `Patient.name`, `Patient.identifier`) are
projected as the *first* entry only. Full multi-row child projections
(e.g. a `patient_name_index` with one row per name) would let us match
non-primary names exactly, at the cost of more write work. Not done yet;
when an unsupported param shows up the search falls back to JSONB.

#### Rebuilding from scratch

```powershell
python -m scripts.rebuild_projections                 # rebuild every projection
python -m scripts.rebuild_projections --only Patient  # one type only
```

Useful after a schema change, a `pg_restore`, or any other path that
bypasses the store.

## Resource Hooks

`app/hooks.py` exposes a `HookRegistry` that lets you attach per-resource-type side effects:

```python
from typing import Any
from app.hooks import ResourceHooks, hooks
from app.utils.errors import FHIRHTTPError


class PatientHooks(ResourceHooks):
    def before_create(self, resource: dict[str, Any]) -> dict[str, Any]:
        if not resource.get("name"):
            raise FHIRHTTPError(422, "Patient must have a name", "business-rule")
        return resource

    def after_create(self, resource: dict[str, Any]) -> None:
        # e.g. publish to a queue, write an AuditEvent, emit a Subscription notification...
        ...

    def before_update(self, old, new):
        return new

    def after_update(self, old, new):
        ...

    def before_delete(self, resource):
        ...

    def after_delete(self, resource):
        ...


hooks.register("Patient", PatientHooks())
```

`before_*` hooks may mutate or replace the resource before it is stored, or raise `FHIRHTTPError` to reject the interaction with a FHIR `OperationOutcome`. `after_*` hooks run after the DB commit; any side effects they perform should be idempotent or run inside their own transactions.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the API:

```powershell
uvicorn app.main:app --reload
```

Run tests:

```powershell
pytest
```

## Tests

There are two test suites:

- `tests/` — unit tests using the FastAPI `TestClient` against an in-memory SQLite store. Fast, no external dependencies.
- `tests_integration/` — integration tests that hit a live FHIR HTTP server (the Compose stack by default). They use unique IDs so they are safe to re-run against a shared server.

```powershell
pytest                              # unit tests only (in-memory SQLite)
pytest tests_integration            # integration tests (defaults to http://localhost:8000)
$env:FHIR_BASE_URL = "http://other-host:8000"; pytest tests_integration
```

Integration tests automatically `skip` if the server at `FHIR_BASE_URL` isn't reachable, so they're CI-safe.

## Seeding the Database

`scripts/seed/` is a bulk FHIR R5 generator built on `Faker` plus the `fhir.resources` Pydantic models. It produces realistic-looking synthetic data across 36 resource types and writes it directly through `FHIRStore.bulk_create()` (batched `INSERT ... ON CONFLICT DO NOTHING` for fast, idempotent re-runs).

### What it generates

At `--scale 1.0` the default plan emits ~67k rows spread across the clinical-workflow set: Patient, Practitioner(+Role), Organization, Location, RelatedPerson, Encounter/EpisodeOfCare/Schedule/Slot/Appointment, Condition, AllergyIntolerance, FamilyMemberHistory, Procedure, Immunization, Observation (vitals), Specimen, DiagnosticReport, ImagingStudy, the full Medication family, CarePlan/Goal/ServiceRequest/Task/NutritionOrder, DocumentReference, Composition, Coverage, Claim, Communication/CommunicationRequest. All references between resources point at other generated resources of the right type so the graph is internally consistent.

Edit the `PLAN` dict in `scripts/seed/generator.py` to change relative proportions, or add new resource types by writing a small Faker-driven generator and registering it in `PIPELINE`.

### CLI

```powershell
.\.venv\Scripts\python.exe -m scripts.seed                       # default scale 1.0, ~67k rows
.\.venv\Scripts\python.exe -m scripts.seed --scale 0.01          # smoke load, ~675 rows
.\.venv\Scripts\python.exe -m scripts.seed --scale 10 --seed 17  # ~670k rows, custom RNG
.\.venv\Scripts\python.exe -m scripts.seed --dry-run             # validate every model, write nothing
.\.venv\Scripts\python.exe -m scripts.seed --clear               # TRUNCATE first
.\.venv\Scripts\python.exe -m scripts.seed --only Patient --only Observation  # subset (deps auto-included)
```

Reproducibility: a single integer `--seed` controls both Faker and the `random.Random` instance, so two runs with the same seed produce byte-identical data. Resource ids are `bulk-<type>-NNNNNN`; re-running without `--clear` is a no-op (ON CONFLICT DO NOTHING).

### DB URL

The default `DATABASE_URL` points at the docker-compose Postgres on `localhost:55432`:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://user:pw@host:port/db"
.\.venv\Scripts\python.exe -m scripts.seed
```

### Throughput

On a local laptop, scale 1.0 (~67k rows) lands in ~30 seconds end-to-end (Pydantic validation + batched INSERTs at ~2.3k rows/sec). Scale 10 (~670k rows) takes ~5 min. The bottleneck is Pydantic validation, not the database — batched `INSERT...VALUES (...)` keeps the DB layer well under 1ms/row.

### Trade-off

The seeder writes through the SQLAlchemy store and bypasses the FastAPI route layer, so registered `ResourceHooks` are NOT invoked during seeding. If you need hooks to fire for seed data, run the server and POST/PUT the resources instead (e.g. wrap the generator in an `httpx` loop). Search projections are still updated in the seeder's transactions — they live in the store, not in user hooks.

## Docker Compose (App + Postgres)

A `Dockerfile` and `docker-compose.yml` are included. The Compose stack runs the FHIR app against a Postgres 16 container.

```powershell
docker compose up -d --build
docker compose logs -f app
```

* App: http://localhost:8000 (CapabilityStatement at `/metadata`)
* Postgres: `localhost:55432`, user `fhir`, password `fhir`, db `fhir` (the host-side port is `55432` to avoid colliding with a native Postgres install on `5432`; inside the compose network the app still uses `db:5432`).

The `DATABASE_URL` is set inside the compose file to `postgresql+psycopg://fhir:fhir@db:5432/fhir`. Tables are created on app startup via `init_db()`. Data persists in the `fhir_postgres_data` named volume.

Tear it down:

```powershell
docker compose down            # keep the database volume
docker compose down -v         # also delete the database volume
```

## Configuration

| Variable               | Default                 | Description                                            |
| ---------------------- | ----------------------- | ------------------------------------------------------ |
| `DATABASE_URL`         | `sqlite:///./fhir.db`   | SQLAlchemy URL.                                        |
| `FHIR_RESOURCE_TYPES`  | all FHIR R5 types       | Comma-separated subset to advertise in the CapabilityStatement. Unknown names are dropped. |
