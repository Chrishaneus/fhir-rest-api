from __future__ import annotations

import uuid

import httpx
import pytest

FHIR_JSON = "application/fhir+json"


def assert_operation_outcome(r: httpx.Response) -> dict:
    """Assert the response is an OperationOutcome served as fhir+json."""
    assert r.headers["content-type"].startswith(FHIR_JSON)
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"
    return body


@pytest.fixture
def created_patient(client: httpx.Client) -> dict:
    """Create a throwaway patient and return its response body."""
    r = client.post(
        "/Patient",
        headers={"Content-Type": FHIR_JSON},
        json={
            "resourceType": "Patient",
            "name": [{"family": f"Compliance-{uuid.uuid4().hex[:8]}"}],
        },
    )
    assert r.status_code == 201
    return r.json()
