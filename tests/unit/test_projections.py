"""Tests for Phase B search projections.

These exercise the projection write path (create/update/delete keep the index
in sync) and the search-routing path (FHIRStore picks the projection over
the JSONB/Python fallback when one is registered for the resource type).
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import Table, select

from app.db.base import SessionLocal
from app.db.projection_models import (
    AllergyIntoleranceProjectionSchema,
    EncounterProjectionSchema,
    ObservationProjectionSchema,
    PatientProjectionSchema,
)
from app.projections import registry
from app.store import store


def resource_payload(version: Any) -> dict[str, Any]:
    """Return the resource payload from a ResourceVersion, asserting it is present.

    `ResourceVersion.resource` is typed `dict[str, Any] | None` because
    deletion tombstones carry no JSON content — only the version metadata
    (resource_id, version_id, deleted=True) survives in the DB row.

    In these tests we only call `resource_payload()` on versions produced by
    `store.create` or `store.update`, where a non-None payload is
    guaranteed. The assertion both enforces that invariant at runtime and
    narrows the type so Pylance does not flag every downstream `[key]`
    access as potentially indexing None.
    """
    assert version.resource is not None, (
        f"Expected a live resource payload but got a deletion tombstone "
        f"(resource_id={getattr(version, 'resource_id', '?')!r}, "
        f"version_id={getattr(version, 'version_id', '?')!r})"
    )
    return version.resource


def _patient(family: str, given: str, gender: str, birth: str) -> dict:
    return {
        "resourceType": "Patient",
        "name": [{"family": family, "given": [given]}],
        "gender": gender,
        "birthDate": birth,
    }


def _get_patient_row(resource_id: str) -> PatientProjectionSchema | None:
    with SessionLocal() as session:
        return session.execute(
            select(PatientProjectionSchema).where(
                PatientProjectionSchema.resource_id == resource_id
            )
        ).scalar_one_or_none()


class TestWritePath:
    def test_create_populates_row(self) -> None:
        v = store.create("Patient", _patient("Smith", "Alex", "male", "1990-01-01"))
        rid = resource_payload(v)["id"]

        row = _get_patient_row(rid)
        assert row is not None
        assert row.family == "Smith"
        assert row.given == "Alex"
        assert row.gender == "male"
        assert row.birth_date is not None
        assert row.birth_date.isoformat() == "1990-01-01"
        assert row.version_id == "1"

    def test_update_keeps_row_current(self) -> None:
        v = store.create("Patient", _patient("Smith", "Alex", "male", "1990-01-01"))
        rid = resource_payload(v)["id"]
        store.update("Patient", rid, _patient("Jones", "Alex", "male", "1990-01-01"))

        row = _get_patient_row(rid)
        assert row is not None
        assert row.family == "Jones"
        assert row.version_id == "2"

    def test_delete_removes_row(self) -> None:
        v = store.create("Patient", _patient("Smith", "Alex", "male", "1990-01-01"))
        rid = resource_payload(v)["id"]
        assert _get_patient_row(rid) is not None

        store.delete("Patient", rid)

        # Projection row gone; resource_versions keeps the tombstone but the
        # search projection mirrors only live resources.
        assert _get_patient_row(rid) is None


class TestSearch:
    def test_filters_by_indexed_columns(self) -> None:
        store.create("Patient", _patient("Adams", "Mira", "female", "1985-03-12"))
        store.create("Patient", _patient("Becker", "Liam", "male", "1990-07-04"))
        store.create("Patient", _patient("Castro", "Iris", "female", "1995-11-20"))

        females = store.search("Patient", {"gender": ["female"]})
        assert len(females) == 2
        assert {resource_payload(p)["name"][0]["family"] for p in females} == {"Adams", "Castro"}

        # String params do prefix-match, case-insensitive.
        castros = store.search("Patient", {"family": ["cas"]})
        assert [resource_payload(p)["name"][0]["family"] for p in castros] == ["Castro"]

    def test_combines_multiple_params(self) -> None:
        store.create("Patient", _patient("Smith", "Alex", "male", "1990-01-01"))
        store.create("Patient", _patient("Smith", "Alex", "female", "1995-05-05"))
        store.create("Patient", _patient("Jones", "Alex", "female", "1990-01-01"))

        results = store.search("Patient", {"family": ["Smith"], "gender": ["female"]})
        assert len(results) == 1
        assert resource_payload(results[0])["birthDate"] == "1995-05-05"

    def test_count_param_does_not_disable_projection(self) -> None:
        """Regression: `_count` is a pagination control, not a filter.

        Before the fix, `_count` wasn't in `IGNORED_SEARCH_PARAMS` and wasn't
        in any projection's supported set, so :meth:`Projection.supports` returned
        False on every paginated request (~every real client request) and the
        store silently fell back to JSONB / Python.
        """
        projection = registry.for_type("Patient")
        assert projection is not None
        assert projection.supports({"gender": ["male"], "_count": ["20"]}) is True

    def test_uppercase_param_keeps_filter_applied(self) -> None:
        """Regression: `supports()` lowercased keys but `_param_clause()`
        didn't, so `?Gender=female` advertised as supported but the clause was
        silently dropped, returning every Patient instead of the female ones.
        """
        store.create("Patient", _patient("Adams", "Mira", "female", "1985-03-12"))
        store.create("Patient", _patient("Becker", "Liam", "male", "1990-07-04"))
        store.create("Patient", _patient("Castro", "Iris", "female", "1995-11-20"))

        results = store.search("Patient", {"Gender": ["female"]})
        assert len(results) == 2
        assert {resource_payload(p)["gender"] for p in results} == {"female"}

    def test_unsupported_param_falls_back(self) -> None:
        """Patient has a projection but doesn't list `identifier` in its supported
        params. The store must fall back to the JSONB / Python path so the filter
        still applies -- silently dropping it would return wrong results.
        """
        p1 = {
            **_patient("Reyes", "Sam", "male", "1991-06-10"),
            "identifier": [{"system": "urn:mrn", "value": "MRN-42"}],
        }
        p2 = {
            **_patient("Reyes", "Sam", "male", "1991-06-10"),
            "identifier": [{"system": "urn:mrn", "value": "MRN-99"}],
        }
        store.create("Patient", p1)
        store.create("Patient", p2)

        results = store.search("Patient", {"identifier": ["MRN-42"]})
        assert len(results) == 1
        assert resource_payload(results[0])["identifier"][0]["value"] == "MRN-42"


class TestRegistry:
    def test_covers_expectedresource_payloadource_types(self) -> None:
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

    def test_every_searchable_column_has_a_backing_index(self) -> None:
        """Invariant: if a projection lists a column in any of its *_PARAMS maps,
        that column must be the leading column of *some* index on its table.

        Without this, a projection can advertise a fast SQL path while actually
        forcing a seq scan -- exactly the bug class Phase B exists to prevent.
        Composite indexes count only when the searchable column is *first*: a
        btree on `(subject_reference, code_code)` accelerates `code_code` queries
        only when `subject_reference` is also filtered, so it doesn't satisfy a
        standalone `code=...` search.
        """
        failures: dict[str, set[str]] = {}
        for projection in registry.all():
            table = cast(Table, projection.table.__table__)

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


class TestObservationProjectionSchema:
    def test_extracts_references_and_codes(self) -> None:
        patient_v = store.create("Patient", _patient("Vega", "Lia", "female", "1980-02-14"))
        pid = resource_payload(patient_v)["id"]

        store.create(
            "Observation",
            {
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
            },
        )

        with SessionLocal() as session:
            row = session.execute(select(ObservationProjectionSchema)).scalar_one_or_none()
            assert row is not None
            assert row.subject_reference == f"Patient/{pid}"
            assert row.code_code == "8867-4"
            assert row.code_system == "http://loinc.org"
            assert row.status == "final"
            assert row.effective_at is not None
            assert row.value_quantity_value is not None
            assert int(row.value_quantity_value) == 72

        by_full_ref = store.search("Observation", {"subject": [f"Patient/{pid}"]})
        by_short = store.search("Observation", {"subject": [pid]})
        assert len(by_full_ref) == 1
        assert len(by_short) == 1


class TestEncounterProjectionSchema:
    def test_extracts_period_and_class(self) -> None:
        patient_v = store.create("Patient", _patient("North", "Eli", "male", "1975-08-30"))
        pid = resource_payload(patient_v)["id"]

        store.create(
            "Encounter",
            {
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
                "actualPeriod": {"start": "2025-03-01T09:00:00Z", "end": "2025-03-01T09:45:00Z"},
            },
        )

        with SessionLocal() as session:
            row = session.execute(select(EncounterProjectionSchema)).scalar_one_or_none()
            assert row is not None
            assert row.status == "completed"
            assert row.class_code == "AMB"
            assert row.subject_reference == f"Patient/{pid}"
            assert row.period_start is not None
            assert row.period_end is not None


class TestAllergyIntoleranceProjectionSchema:
    def test_extracts_clinical_status_and_criticality(self) -> None:
        patient_v = store.create("Patient", _patient("Quinn", "Sky", "female", "2000-12-01"))
        pid = resource_payload(patient_v)["id"]

        store.create(
            "AllergyIntolerance",
            {
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
            },
        )

        with SessionLocal() as session:
            row = session.execute(select(AllergyIntoleranceProjectionSchema)).scalar_one_or_none()
            assert row is not None
            assert row.criticality == "high"
            assert row.clinical_status == "active"
            assert row.code_code == "227037002"
            assert row.patient_reference == f"Patient/{pid}"
