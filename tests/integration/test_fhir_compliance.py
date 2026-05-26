"""FHIR protocol compliance — routing, payload validation, and id rules.

Every test here asserts both the correct HTTP status code *and* that the
error body is an OperationOutcome served as application/fhir+json, which is
required by the FHIR spec for all error responses.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration

FHIR_JSON = "application/fhir+json"


def _operation_outcome(r: httpx.Response) -> dict:
    """Assert the response is an OperationOutcome and return its body."""
    assert r.headers["content-type"].startswith(FHIR_JSON)
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"
    return body


class TestResourceTypeRouting:
    def test_unknown_type_on_post_returns_404(self, client: httpx.Client) -> None:
        r = client.post(
            "/NotAResource",
            json={"resourceType": "NotAResource"},
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 404
        _operation_outcome(r)

    def test_unknown_type_on_get_returns_404(self, client: httpx.Client) -> None:
        r = client.get("/NotAResource/some-id")
        assert r.status_code == 404
        _operation_outcome(r)


class TestPayloadValidation:
    def test_extra_unknown_field_returns_422(self, client: httpx.Client) -> None:
        r = client.post(
            "/Patient",
            json={"resourceType": "Patient", "nonsenseField": "x"},
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 422
        body = _operation_outcome(r)
        assert "nonsenseField" in body["issue"][0]["diagnostics"]

    def test_missing_required_field_returns_422(self, client: httpx.Client) -> None:
        # Observation.code is required by the R5 spec
        r = client.post(
            "/Observation",
            json={"resourceType": "Observation", "status": "final"},
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 422
        body = _operation_outcome(r)
        assert "code" in body["issue"][0]["diagnostics"]

    def test_resource_type_mismatch_returns_400(self, client: httpx.Client) -> None:
        # URL says Patient, body says Observation
        r = client.post(
            "/Patient",
            json={"resourceType": "Observation", "status": "final"},
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 400
        _operation_outcome(r)

    def test_non_object_body_returns_400(self, client: httpx.Client) -> None:
        r = client.post(
            "/Patient",
            content="[1, 2, 3]",
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 400
        _operation_outcome(r)


class TestIdValidation:
    def test_invalid_id_format_in_url_returns_400(self, client: httpx.Client) -> None:
        r = client.get("/Patient/!!invalid!!")
        assert r.status_code == 400
        _operation_outcome(r)

    def test_id_mismatch_between_url_and_body_returns_400(self, client: httpx.Client) -> None:
        r = client.put(
            "/Patient/some-id",
            json={"resourceType": "Patient", "id": "different-id"},
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 400
        _operation_outcome(r)
