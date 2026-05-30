"""Unit tests for POST /{resource_type}/$validate."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _valid_patient() -> dict:
    return {"resourceType": "Patient", "name": [{"family": "Doe"}]}


def _valid_observation() -> dict:
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
    }


class TestValidateSuccess:
    def test_valid_patient_returns_200(self, client: TestClient) -> None:
        r = client.post("/Patient/$validate", json=_valid_patient())
        assert r.status_code == 200

    def test_response_is_operation_outcome(self, client: TestClient) -> None:
        r = client.post("/Patient/$validate", json=_valid_patient())
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_response_has_informational_severity(self, client: TestClient) -> None:
        r = client.post("/Patient/$validate", json=_valid_patient())
        issue = r.json()["issue"][0]
        assert issue["severity"] == "information"
        assert issue["code"] == "informational"

    def test_valid_observation_returns_200(self, client: TestClient) -> None:
        r = client.post("/Observation/$validate", json=_valid_observation())
        assert r.status_code == 200

    def test_resourcetype_in_body_optional(self, client: TestClient) -> None:
        r = client.post("/Patient/$validate", json={"name": [{"family": "Doe"}]})
        assert r.status_code == 200

    def test_does_not_persist_resource(self, client: TestClient) -> None:
        client.post("/Patient/$validate", json=_valid_patient())
        assert client.get("/Patient?family=Doe").json()["total"] == 0


class TestValidateErrors:
    def test_invalid_observation_returns_422(self, client: TestClient) -> None:
        r = client.post("/Observation/$validate", json={"resourceType": "Observation"})
        assert r.status_code == 422

    def test_invalid_resource_body_is_operation_outcome(self, client: TestClient) -> None:
        r = client.post("/Observation/$validate", json={"resourceType": "Observation"})
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_unknown_resource_type_returns_404(self, client: TestClient) -> None:
        r = client.post("/Unicorn/$validate", json={"resourceType": "Unicorn"})
        assert r.status_code == 404

    def test_unknown_type_body_is_operation_outcome(self, client: TestClient) -> None:
        r = client.post("/Unicorn/$validate", json={"resourceType": "Unicorn"})
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_resourcetype_mismatch_returns_400(self, client: TestClient) -> None:
        r = client.post("/Patient/$validate", json={"resourceType": "Observation"})
        assert r.status_code == 400
