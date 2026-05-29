"""Integration tests for POST /{resource_type}/$validate."""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration


def _valid_patient(family: str | None = None) -> dict:
    return {
        "resourceType": "Patient",
        "name": [{"family": family or f"ValidateTest-{uuid.uuid4().hex[:8]}"}],
    }


def _valid_observation() -> dict:
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
    }


class TestValidateSuccess:
    def test_valid_patient_returns_200(self, client: httpx.Client) -> None:
        r = client.post("/Patient/$validate", headers={"Content-Type": FHIR_JSON}, json=_valid_patient())
        assert r.status_code == 200

    def test_response_is_informational_operation_outcome(self, client: httpx.Client) -> None:
        r = client.post("/Patient/$validate", headers={"Content-Type": FHIR_JSON}, json=_valid_patient())
        body = r.json()
        assert body["resourceType"] == "OperationOutcome"
        issue = body["issue"][0]
        assert issue["severity"] == "information"
        assert issue["code"] == "informational"

    def test_valid_observation_returns_200(self, client: httpx.Client) -> None:
        r = client.post("/Observation/$validate", headers={"Content-Type": FHIR_JSON}, json=_valid_observation())
        assert r.status_code == 200

    def test_does_not_persist_resource(self, client: httpx.Client) -> None:
        family = f"ValidateOnly-{uuid.uuid4().hex[:8]}"
        r = client.post("/Patient/$validate", headers={"Content-Type": FHIR_JSON}, json=_valid_patient(family))
        assert r.status_code == 200
        search = client.get(f"/Patient?family={family}")
        assert search.json()["total"] == 0

    def test_response_content_type_is_fhir_json(self, client: httpx.Client) -> None:
        r = client.post("/Patient/$validate", headers={"Content-Type": FHIR_JSON}, json=_valid_patient())
        assert r.headers["content-type"].startswith(FHIR_JSON)


class TestValidateErrors:
    def test_invalid_observation_returns_422(self, client: httpx.Client) -> None:
        r = client.post(
            "/Observation/$validate",
            headers={"Content-Type": FHIR_JSON},
            json={"resourceType": "Observation"},
        )
        assert r.status_code == 422
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_unknown_resource_type_returns_404(self, client: httpx.Client) -> None:
        r = client.post(
            "/Unicorn/$validate",
            headers={"Content-Type": FHIR_JSON},
            json={"resourceType": "Unicorn"},
        )
        assert r.status_code == 404
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_resourcetype_mismatch_returns_400(self, client: httpx.Client) -> None:
        r = client.post(
            "/Patient/$validate",
            headers={"Content-Type": FHIR_JSON},
            json={"resourceType": "Observation"},
        )
        assert r.status_code == 400
        assert r.json()["resourceType"] == "OperationOutcome"


class TestValidateCapability:
    def test_capability_statement_advertises_validate_for_all_types(self, client: httpx.Client) -> None:
        meta = client.get("/metadata").json()
        for resource in meta["rest"][0]["resource"]:
            ops = [op["name"] for op in resource.get("operation", [])]
            assert "$validate" in ops, f"$validate missing from {resource['type']} operations"

    def test_patient_capability_still_has_everything(self, client: httpx.Client) -> None:
        meta = client.get("/metadata").json()
        patient_entry = next(r for r in meta["rest"][0]["resource"] if r["type"] == "Patient")
        ops = [op["name"] for op in patient_entry.get("operation", [])]
        assert "$everything" in ops
