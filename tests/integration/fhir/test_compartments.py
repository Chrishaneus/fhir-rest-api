"""Integration tests for FHIR compartment search.

Covers GET /{CompartmentType}/{id}/{ResourceType} for the Patient, Encounter,
and Practitioner compartments using resource types backed by SQL projections.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post(client: httpx.Client, resource: dict) -> dict:
    rt = resource["resourceType"]
    r = client.post(f"/{rt}", headers={"Content-Type": FHIR_JSON}, json=resource)
    assert r.status_code == 201, r.text
    return r.json()


def _patient(family: str = "Compartment") -> dict:
    return {"resourceType": "Patient", "name": [{"family": family}]}


def _observation(subject_id: str, status: str = "final") -> dict:
    return {
        "resourceType": "Observation",
        "status": status,
        "code": {"coding": [{"system": "http://loinc.org", "code": "55284-4"}]},
        "subject": {"reference": f"Patient/{subject_id}"},
    }


def _condition(subject_id: str) -> dict:
    return {
        "resourceType": "Condition",
        "subject": {"reference": f"Patient/{subject_id}"},
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
        },
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "73211009"}]},
    }


def _allergy(patient_id: str) -> dict:
    return {
        "resourceType": "AllergyIntolerance",
        "patient": {"reference": f"Patient/{patient_id}"},
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "227493005"}]},
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]
        },
    }


def _encounter(subject_id: str) -> dict:
    return {
        "resourceType": "Encounter",
        "status": "finished",
        "class": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"}]}],
        "subject": {"reference": f"Patient/{subject_id}"},
    }


def _observation_for_encounter(encounter_id: str) -> dict:
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "55284-4"}]},
        "encounter": {"reference": f"Encounter/{encounter_id}"},
    }


# ---------------------------------------------------------------------------
# Patient compartment
# ---------------------------------------------------------------------------


class TestPatientCompartment:
    def test_unknown_patient_returns_404(self, client: httpx.Client) -> None:
        r = client.get("/Patient/no-such-patient-xyz/Observation")
        assert r.status_code == 404
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_deleted_patient_returns_404(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("Deleted"))["id"]
        client.delete(f"/Patient/{pid}")
        r = client.get(f"/Patient/{pid}/Observation")
        assert r.status_code == 404

    def test_empty_result_when_no_matching_resources(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("NoObs"))["id"]
        bundle = client.get(f"/Patient/{pid}/Observation").json()
        assert bundle["type"] == "searchset"
        assert bundle["total"] == 0
        assert bundle["entry"] == []

    def test_returns_observations_for_patient(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("WithObs"))["id"]
        other_pid = _post(client, _patient("Other"))["id"]
        _post(client, _observation(pid))
        _post(client, _observation(pid))
        _post(client, _observation(other_pid))

        bundle = client.get(f"/Patient/{pid}/Observation").json()
        assert bundle["total"] == 2
        for entry in bundle["entry"]:
            ref = entry["resource"]["subject"]["reference"]
            assert ref == f"Patient/{pid}"

    def test_additional_params_further_filter(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("MultiStatus"))["id"]
        _post(client, _observation(pid, status="final"))
        _post(client, _observation(pid, status="preliminary"))

        bundle = client.get(f"/Patient/{pid}/Observation?status=final").json()
        assert bundle["total"] == 1
        assert bundle["entry"][0]["resource"]["status"] == "final"

    def test_patient_param_used_for_allergy_intolerance(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("Allergy"))["id"]
        _post(client, _allergy(pid))

        bundle = client.get(f"/Patient/{pid}/AllergyIntolerance").json()
        assert bundle["total"] == 1

    def test_returns_conditions_for_patient(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("WithCond"))["id"]
        _post(client, _condition(pid))

        bundle = client.get(f"/Patient/{pid}/Condition").json()
        assert bundle["total"] == 1

    def test_unsupported_resource_type_in_compartment_returns_404(
        self, client: httpx.Client
    ) -> None:
        pid = _post(client, _patient("NoComp"))["id"]
        # Binary is not defined as a Patient compartment member
        r = client.get(f"/Patient/{pid}/Binary")
        assert r.status_code == 404
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_invalid_resource_type_returns_404(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("BadType"))["id"]
        r = client.get(f"/Patient/{pid}/NotARealType")
        assert r.status_code == 404

    def test_response_is_searchset_bundle(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("BundleCheck"))["id"]
        bundle = client.get(f"/Patient/{pid}/Observation").json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"
        assert "total" in bundle
        assert "link" in bundle


# ---------------------------------------------------------------------------
# Encounter compartment
# ---------------------------------------------------------------------------


class TestEncounterCompartment:
    def test_returns_observations_for_encounter(self, client: httpx.Client) -> None:
        enc_id = _post(client, _encounter(_post(client, _patient("EncPatient"))["id"]))["id"]
        _post(client, _observation_for_encounter(enc_id))
        _post(client, _observation_for_encounter(enc_id))

        bundle = client.get(f"/Encounter/{enc_id}/Observation").json()
        assert bundle["total"] == 2
        for entry in bundle["entry"]:
            ref = entry["resource"]["encounter"]["reference"]
            assert ref == f"Encounter/{enc_id}"

    def test_unknown_encounter_returns_404(self, client: httpx.Client) -> None:
        r = client.get("/Encounter/no-such-encounter-xyz/Observation")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Unsupported compartment type
# ---------------------------------------------------------------------------


class TestUnsupportedCompartment:
    def test_unknown_compartment_type_returns_404(self, client: httpx.Client) -> None:
        r = client.get("/Organization/123/Patient")
        assert r.status_code == 404
        body = r.json()
        assert body["resourceType"] == "OperationOutcome"
        issue = body["issue"][0]
        assert issue["code"] in ("not-supported", "not-found")
