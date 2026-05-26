from __future__ import annotations

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON, assert_operation_outcome

pytestmark = pytest.mark.integration


class TestPayloadValidation:
    def test_extra_unknown_field_returns_422(self, client: httpx.Client) -> None:
        r = client.post(
            "/Patient",
            json={"resourceType": "Patient", "nonsenseField": "x"},
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 422
        body = assert_operation_outcome(r)
        assert "nonsenseField" in body["issue"][0]["diagnostics"]

    def test_missing_required_field_returns_422(self, client: httpx.Client) -> None:
        # Observation.code is required by the R5 spec
        r = client.post(
            "/Observation",
            json={"resourceType": "Observation", "status": "final"},
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 422
        body = assert_operation_outcome(r)
        assert "code" in body["issue"][0]["diagnostics"]

    def test_resource_type_mismatch_returns_400(self, client: httpx.Client) -> None:
        # URL says Patient, body says Observation
        r = client.post(
            "/Patient",
            json={"resourceType": "Observation", "status": "final"},
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 400
        assert_operation_outcome(r)

    def test_non_object_body_returns_400(self, client: httpx.Client) -> None:
        r = client.post(
            "/Patient",
            content="[1, 2, 3]",
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 400
        assert_operation_outcome(r)


class TestIdValidation:
    def test_invalid_id_format_in_url_returns_400(self, client: httpx.Client) -> None:
        r = client.get("/Patient/!!invalid!!")
        assert r.status_code == 400
        assert_operation_outcome(r)

    def test_id_mismatch_between_url_and_body_returns_400(self, client: httpx.Client) -> None:
        r = client.put(
            "/Patient/some-id",
            json={"resourceType": "Patient", "id": "different-id"},
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 400
        assert_operation_outcome(r)


class TestOperationOutcomeStructure:
    def test_error_issue_has_severity_code_and_diagnostics(self, client: httpx.Client) -> None:
        r = client.post(
            "/Patient",
            json={"resourceType": "Patient", "bad": True},
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 422
        issue = r.json()["issue"][0]
        assert "severity" in issue
        assert "code" in issue
        assert "diagnostics" in issue

    def test_404_issue_has_not_found_code(self, client: httpx.Client) -> None:
        r = client.get("/Patient/definitely-does-not-exist-xyz123")
        assert r.status_code == 404
        body = assert_operation_outcome(r)
        assert body["issue"][0]["code"] == "not-found"
