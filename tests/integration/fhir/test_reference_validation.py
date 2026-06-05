"""Integration tests for reference validation hooks.

Verifies that before_create / before_update reject dangling local references
(ResourceType/id that does not exist) with 422 Unprocessable Content, while
absent fields and absolute-URL references are silently accepted.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON, assert_operation_outcome

pytestmark = pytest.mark.integration


def _unique(prefix: str = "ref") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def patient(client: httpx.Client) -> dict:
    r = client.post(
        "/Patient",
        headers={"Content-Type": FHIR_JSON},
        json={"resourceType": "Patient", "name": [{"family": _unique("RefPatient")}]},
    )
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def encounter(client: httpx.Client, patient: dict) -> dict:
    r = client.post(
        "/Encounter",
        headers={"Content-Type": FHIR_JSON},
        json={
            "resourceType": "Encounter",
            "status": "finished",
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
            "subject": {"reference": f"Patient/{patient['id']}"},
        },
    )
    assert r.status_code == 201
    return r.json()


class TestObservationReferenceValidation:
    def test_dangling_subject_returns_422(self, client: httpx.Client) -> None:
        r = client.post(
            "/Observation",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "Observation",
                "status": "final",
                "code": {"text": "HR"},
                "subject": {"reference": "Patient/does-not-exist-xyz"},
            },
        )
        assert r.status_code == 422
        body = assert_operation_outcome(r)
        assert "does-not-exist-xyz" in body["issue"][0]["diagnostics"]

    def test_dangling_encounter_returns_422(self, client: httpx.Client, patient: dict) -> None:
        r = client.post(
            "/Observation",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "Observation",
                "status": "final",
                "code": {"text": "HR"},
                "subject": {"reference": f"Patient/{patient['id']}"},
                "encounter": {"reference": "Encounter/does-not-exist-xyz"},
            },
        )
        assert r.status_code == 422
        assert_operation_outcome(r)

    def test_valid_references_accepted(
        self, client: httpx.Client, patient: dict, encounter: dict
    ) -> None:
        r = client.post(
            "/Observation",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "Observation",
                "status": "final",
                "code": {"text": "HR"},
                "subject": {"reference": f"Patient/{patient['id']}"},
                "encounter": {"reference": f"Encounter/{encounter['id']}"},
            },
        )
        assert r.status_code == 201

    def test_absent_subject_is_accepted(self, client: httpx.Client) -> None:
        r = client.post(
            "/Observation",
            headers={"Content-Type": FHIR_JSON},
            json={"resourceType": "Observation", "status": "final", "code": {"text": "HR"}},
        )
        assert r.status_code == 201

    def test_absolute_url_reference_is_skipped(self, client: httpx.Client) -> None:
        r = client.post(
            "/Observation",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "Observation",
                "status": "final",
                "code": {"text": "HR"},
                "subject": {"reference": "https://other.example.com/fhir/Patient/remote"},
            },
        )
        assert r.status_code == 201

    def test_update_with_dangling_reference_returns_422(
        self, client: httpx.Client, patient: dict
    ) -> None:
        obs = client.post(
            "/Observation",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "Observation",
                "status": "final",
                "code": {"text": "HR"},
                "subject": {"reference": f"Patient/{patient['id']}"},
            },
        ).json()

        r = client.put(
            f"/Observation/{obs['id']}",
            headers={"Content-Type": FHIR_JSON},
            json={**obs, "subject": {"reference": "Patient/ghost-id"}},
        )
        assert r.status_code == 422
        assert_operation_outcome(r)


class TestAllergyIntoleranceReferenceValidation:
    def test_dangling_patient_returns_422(self, client: httpx.Client) -> None:
        r = client.post(
            "/AllergyIntolerance",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "AllergyIntolerance",
                "patient": {"reference": "Patient/does-not-exist-xyz"},
                "code": {"text": "Penicillin"},
            },
        )
        assert r.status_code == 422
        assert_operation_outcome(r)

    def test_valid_patient_accepted(self, client: httpx.Client, patient: dict) -> None:
        r = client.post(
            "/AllergyIntolerance",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "AllergyIntolerance",
                "patient": {"reference": f"Patient/{patient['id']}"},
                "code": {"text": "Penicillin"},
            },
        )
        assert r.status_code == 201


class TestConditionReferenceValidation:
    def test_dangling_subject_returns_422(self, client: httpx.Client) -> None:
        r = client.post(
            "/Condition",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "Condition",
                "subject": {"reference": "Patient/does-not-exist-xyz"},
                "code": {"text": "Fever"},
            },
        )
        assert r.status_code == 422
        assert_operation_outcome(r)

    def test_valid_subject_accepted(self, client: httpx.Client, patient: dict) -> None:
        r = client.post(
            "/Condition",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "Condition",
                "subject": {"reference": f"Patient/{patient['id']}"},
                "code": {"text": "Fever"},
                "clinicalStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                            "code": "active",
                        }
                    ]
                },
            },
        )
        assert r.status_code == 201


class TestDeletedReferenceRejected:
    def test_reference_to_deleted_resource_returns_422(
        self, client: httpx.Client, patient: dict
    ) -> None:
        patient_id = patient["id"]
        client.delete(f"/Patient/{patient_id}")

        r = client.post(
            "/Observation",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "Observation",
                "status": "final",
                "code": {"text": "HR"},
                "subject": {"reference": f"Patient/{patient_id}"},
            },
        )
        assert r.status_code == 422
        assert_operation_outcome(r)
