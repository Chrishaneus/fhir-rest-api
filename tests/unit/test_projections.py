"""Tests for Phase B search projections.

These exercise the projection write path (create/update/delete keep the index
in sync) and the search-routing path (FHIRStore picks the projection over
the JSONB/Python fallback when one is registered for the resource type).
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.projection_models import (
    AllergyIntoleranceIndex,
    EncounterIndex,
    ObservationIndex,
    PatientIndex,
)
from app.projections import registry
from app.store import store


def _patient(family: str, given: str, gender: str, birth: str) -> dict:
    return {
        "resourceType": "Patient",
        "name": [{"family": family, "given": [given]}],
        "gender": gender,
        "birthDate": birth,
    }


def _get_patient_row(resource_id: str) -> PatientIndex | None:
    with SessionLocal() as session:
        return session.execute(
            select(PatientIndex).where(PatientIndex.resource_id == resource_id)
        ).scalar_one_or_none()


def test_create_populates_projection_row() -> None:
    v = store.create("Patient", _patient("Smith", "Alex", "male", "1990-01-01"))
    rid = v.resource["id"]

    row = _get_patient_row(rid)
    assert row is not None
    assert row.family == "Smith"
    assert row.given == "Alex"
    assert row.gender == "male"
    assert row.birth_date.isoformat() == "1990-01-01"
    assert row.version_id == "1"


def test_update_keeps_projection_row_current() -> None:
    v = store.create("Patient", _patient("Smith", "Alex", "male", "1990-01-01"))
    rid = v.resource["id"]

    new_resource = _patient("Jones", "Alex", "male", "1990-01-01")
    store.update("Patient", rid, new_resource)

    row = _get_patient_row(rid)
    assert row is not None
    assert row.family == "Jones"
    assert row.version_id == "2"


def test_delete_removes_projection_row() -> None:
    v = store.create("Patient", _patient("Smith", "Alex", "male", "1990-01-01"))
    rid = v.resource["id"]
    assert _get_patient_row(rid) is not None

    store.delete("Patient", rid)

    # Projection row gone; resource_versions keeps the tombstone but the
    # search projection mirrors only live resources.
    assert _get_patient_row(rid) is None


def test_projection_search_filters_by_indexed_columns() -> None:
    store.create("Patient", _patient("Adams", "Mira", "female", "1985-03-12"))
    store.create("Patient", _patient("Becker", "Liam", "male", "1990-07-04"))
    store.create("Patient", _patient("Castro", "Iris", "female", "1995-11-20"))

    females = store.search("Patient", {"gender": ["female"]})
    assert len(females) == 2
    assert {p.resource["name"][0]["family"] for p in females} == {"Adams", "Castro"}

    # String params do prefix-match, case-insensitive.
    castros = store.search("Patient", {"family": ["cas"]})
    assert [p.resource["name"][0]["family"] for p in castros] == ["Castro"]


def test_projection_search_combines_multiple_params() -> None:
    store.create("Patient", _patient("Smith", "Alex", "male", "1990-01-01"))
    store.create("Patient", _patient("Smith", "Alex", "female", "1995-05-05"))
    store.create("Patient", _patient("Jones", "Alex", "female", "1990-01-01"))

    results = store.search("Patient", {"family": ["Smith"], "gender": ["female"]})
    assert len(results) == 1
    assert results[0].resource["birthDate"] == "1995-05-05"


def test_observation_projection_handles_references_and_codes() -> None:
    patient_v = store.create("Patient", _patient("Vega", "Lia", "female", "1980-02-14"))
    pid = patient_v.resource["id"]

    obs = {
        "resourceType": "Observation",
        "status": "final",
        "code": {
            "coding": [
                {"system": "http://loinc.org", "code": "8867-4", "display": "Heart rate"}
            ]
        },
        "subject": {"reference": f"Patient/{pid}"},
        "effectiveDateTime": "2025-03-01T10:15:00Z",
        "valueQuantity": {"value": 72, "unit": "bpm"},
    }
    store.create("Observation", obs)

    with SessionLocal() as session:
        row = session.execute(select(ObservationIndex)).scalar_one_or_none()
        assert row is not None
        assert row.subject_ref == f"Patient/{pid}"
        assert row.code_code == "8867-4"
        assert row.code_system == "http://loinc.org"
        assert row.status == "final"
        assert row.effective_at is not None
        # Numeric value parsed into Decimal so range filters work.
        assert int(row.value_quantity_value) == 72

    # subject= search accepts both "Patient/X" and bare "X" (resource-type-aware).
    by_full_ref = store.search("Observation", {"subject": [f"Patient/{pid}"]})
    by_short = store.search("Observation", {"subject": [pid]})
    assert len(by_full_ref) == 1
    assert len(by_short) == 1


def test_count_param_does_not_disable_projection() -> None:
    """Regression: ``_count`` is a pagination control, not a filter.

    Before the fix, ``_count`` wasn't in ``IGNORED_SEARCH_PARAMS`` and wasn't
    in any projection's supported set, so :meth:`Projection.supports` returned
    False on every paginated request (~every real client request) and the
    store silently fell back to JSONB / Python.
    """
    store.create("Patient", _patient("Adams", "Mira", "female", "1985-03-12"))
    store.create("Patient", _patient("Becker", "Liam", "male", "1990-07-04"))

    projection = registry.for_type("Patient")
    assert projection is not None
    assert projection.supports({"gender": ["male"], "_count": ["20"]}) is True


def test_uppercase_search_param_keeps_filter_applied() -> None:
    """Regression: ``supports()`` lowercased keys but ``_param_clause()``
    didn't, so ``?Gender=female`` advertised as supported but the clause was
    silently dropped, returning every Patient instead of the female ones.
    """
    store.create("Patient", _patient("Adams", "Mira", "female", "1985-03-12"))
    store.create("Patient", _patient("Becker", "Liam", "male", "1990-07-04"))
    store.create("Patient", _patient("Castro", "Iris", "female", "1995-11-20"))

    results = store.search("Patient", {"Gender": ["female"]})
    assert len(results) == 2
    assert {p.resource["gender"] for p in results} == {"female"}


def test_unsupported_param_falls_back_to_jsonb_or_python() -> None:
    """Patient has a projection but doesn't list ``identifier`` in its supported
    params. The store must fall back to the JSONB / Python path so the filter
    still applies -- silently dropping it would return wrong results.
    """
    p1 = _patient("Reyes", "Sam", "male", "1991-06-10")
    p1["identifier"] = [{"system": "urn:mrn", "value": "MRN-42"}]
    store.create("Patient", p1)

    p2 = _patient("Reyes", "Sam", "male", "1991-06-10")
    p2["identifier"] = [{"system": "urn:mrn", "value": "MRN-99"}]
    store.create("Patient", p2)

    # ``identifier`` is in jsonb_search.SEARCH_PARAM_TEMPLATES but not in
    # PatientProjection.supported_params, so the store falls through to the
    # SQLite Python path here.
    results = store.search("Patient", {"identifier": ["MRN-42"]})
    assert len(results) == 1
    assert results[0].resource["identifier"][0]["value"] == "MRN-42"


def test_every_searchable_column_has_a_backing_index() -> None:
    """Invariant: if a projection lists a column in any of its *_PARAMS maps,
    that column must be the leading column of *some* index on its table.

    Without this, a projection can advertise a fast SQL path while actually
    forcing a seq scan -- exactly the bug class Phase B exists to prevent.
    Composite indexes count only when the searchable column is *first*: a
    btree on ``(subject_ref, code_code)`` accelerates ``code_code`` queries
    only when ``subject_ref`` is also filtered, so it doesn't satisfy a
    standalone ``code=...`` search.
    """
    failures: dict[str, set[str]] = {}
    for projection in registry.all():
        table = projection.table.__table__  # type: ignore[attr-defined]

        # Columns that are the leading entry of *some* index (or PK).
        leading_indexed: set[str] = {col.name for col in table.primary_key.columns}
        for idx in table.indexes:
            cols = list(idx.columns)
            if cols:
                leading_indexed.add(cols[0].name)

        searched: set[str] = set()
        for mapping in (
            projection.TOKEN_PARAMS,
            projection.STRING_PARAMS,
            projection.DATE_PARAMS,
            projection.NUMBER_PARAMS,
        ):
            searched.update(mapping.values())
        for col_name, _default_type in projection.REFERENCE_PARAMS.values():
            searched.add(col_name)

        missing = searched - leading_indexed
        if missing:
            failures[projection.resource_type] = missing

    assert not failures, (
        "Projection columns referenced by search params but not indexed "
        f"(would cause seq scans at scale): {failures}"
    )


def test_registry_covers_expected_resource_types() -> None:
    expected = {
        "Patient",
        "Practitioner",
        "Organization",
        "Encounter",
        "Observation",
        "Condition",
        "AllergyIntolerance",
        "MedicationRequest",
        "DiagnosticReport",
        "Procedure",
    }
    assert expected.issubset(set(registry.known_types()))


def test_encounter_projection_extracts_period_and_class() -> None:
    patient_v = store.create("Patient", _patient("North", "Eli", "male", "1975-08-30"))
    pid = patient_v.resource["id"]

    enc = {
        "resourceType": "Encounter",
        "status": "completed",
        "class": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                        "code": "AMB",
                    }
                ]
            }
        ],
        "subject": {"reference": f"Patient/{pid}"},
        "actualPeriod": {
            "start": "2025-03-01T09:00:00Z",
            "end": "2025-03-01T09:45:00Z",
        },
    }
    store.create("Encounter", enc)

    with SessionLocal() as session:
        row = session.execute(select(EncounterIndex)).scalar_one_or_none()
        assert row is not None
        assert row.status == "completed"
        assert row.class_code == "AMB"
        assert row.subject_ref == f"Patient/{pid}"
        assert row.period_start is not None
        assert row.period_end is not None


def test_allergy_projection_extracts_clinical_status_and_criticality() -> None:
    patient_v = store.create("Patient", _patient("Quinn", "Sky", "female", "2000-12-01"))
    pid = patient_v.resource["id"]

    allergy = {
        "resourceType": "AllergyIntolerance",
        "patient": {"reference": f"Patient/{pid}"},
        "criticality": "high",
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                    "code": "active",
                }
            ]
        },
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "227037002"}]},
    }
    store.create("AllergyIntolerance", allergy)

    with SessionLocal() as session:
        row = session.execute(select(AllergyIntoleranceIndex)).scalar_one_or_none()
        assert row is not None
        assert row.criticality == "high"
        assert row.clinical_status == "active"
        assert row.code_code == "227037002"
        assert row.patient_ref == f"Patient/{pid}"
