"""Integration tests for Prefer: handling=lenient/strict on search endpoints."""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration


def _unique(prefix: str = "Handling") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def patient(client: httpx.Client) -> dict:
    response = client.post(
        "/Patient",
        headers={"Content-Type": FHIR_JSON},
        json={"resourceType": "Patient", "name": [{"family": _unique()}]},
    )
    assert response.status_code == 201
    return response.json()


class TestStrictMode:
    def test_unknown_underscore_param_returns_400(self, client: httpx.Client) -> None:
        response = client.get(
            "/Patient?_unknownparam=x",
            headers={"Prefer": "handling=strict"},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert "_unknownparam" in body["issue"][0]["diagnostics"]

    def test_multiple_unknown_params_all_listed(self, client: httpx.Client) -> None:
        response = client.get(
            "/Patient?_foo=1&_bar=2",
            headers={"Prefer": "handling=strict"},
        )
        assert response.status_code == 400
        diagnostics = response.json()["issue"][0]["diagnostics"]
        assert "_bar" in diagnostics
        assert "_foo" in diagnostics

    def test_known_control_params_accepted(self, client: httpx.Client, patient: dict) -> None:
        response = client.get(
            f"/Patient?_id={patient['id']}&_count=5&_sort=family&_summary=false",
            headers={"Prefer": "handling=strict"},
        )
        assert response.status_code == 200
        assert response.json()["resourceType"] == "Bundle"

    def test_non_underscore_params_accepted(self, client: httpx.Client) -> None:
        response = client.get(
            "/Patient?family=Smith",
            headers={"Prefer": "handling=strict"},
        )
        assert response.status_code == 200

    def test_post_search_unknown_param_returns_400(self, client: httpx.Client) -> None:
        response = client.post(
            "/Patient/_search",
            headers={"Prefer": "handling=strict"},
            data={"_unknownparam": "x"},
        )
        assert response.status_code == 400
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_mixed_prefer_tokens_parsed_correctly(self, client: httpx.Client) -> None:
        response = client.get(
            "/Patient?_unknownparam=x",
            headers={"Prefer": "return=minimal, handling=strict"},
        )
        assert response.status_code == 400

    def test_system_search_unknown_param_returns_400(self, client: httpx.Client) -> None:
        response = client.get(
            "/?_type=Patient&_unknownparam=x",
            headers={"Prefer": "handling=strict"},
        )
        assert response.status_code == 400
        assert response.json()["resourceType"] == "OperationOutcome"


class TestLenientMode:
    def test_unknown_param_silently_ignored(self, client: httpx.Client, patient: dict) -> None:
        response = client.get(
            f"/Patient?_id={patient['id']}&_unknownparam=x",
            headers={"Prefer": "handling=lenient"},
        )
        assert response.status_code == 200
        assert response.json()["total"] >= 1

    def test_no_prefer_header_defaults_to_lenient(self, client: httpx.Client) -> None:
        response = client.get("/Patient?_unknownparam=x")
        assert response.status_code == 200
