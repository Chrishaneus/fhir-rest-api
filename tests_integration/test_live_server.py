"""Integration tests against the live FHIR REST layer.

These tests exercise the actual HTTP interface (and, when running under the
docker-compose stack, the Postgres-backed store). Each test uses unique data
so the suite can be re-run safely against a shared server.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

FHIR_JSON = "application/fhir+json"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def test_capability_statement_is_fhir_r5(client: httpx.Client) -> None:
    response = client.get("/metadata")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(FHIR_JSON)
    body = response.json()
    assert body["resourceType"] == "CapabilityStatement"
    assert body["fhirVersion"] == "5.0.0"
    advertised = {entry["type"] for entry in body["rest"][0]["resource"]}
    for required in ("Patient", "Observation", "Encounter", "Bundle"):
        assert required in advertised


def test_unknown_resource_type_returns_404(client: httpx.Client) -> None:
    response = client.post(
        "/NotAResource",
        json={"resourceType": "NotAResource"},
        headers={"Content-Type": FHIR_JSON},
    )
    assert response.status_code == 404
    assert response.json()["resourceType"] == "OperationOutcome"


def test_invalid_fhir_payload_returns_422(client: httpx.Client) -> None:
    response = client.post(
        "/Patient",
        json={"resourceType": "Patient", "nonsenseField": "x"},
        headers={"Content-Type": FHIR_JSON},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["resourceType"] == "OperationOutcome"
    assert "nonsenseField" in body["issue"][0]["diagnostics"]


def test_patient_lifecycle_end_to_end(client: httpx.Client) -> None:
    family = _unique("Integ")
    create_response = client.post(
        "/Patient",
        headers={"Content-Type": FHIR_JSON},
        json={
            "resourceType": "Patient",
            "name": [{"use": "official", "family": family, "given": ["Test"]}],
            "gender": "unknown",
        },
    )
    assert create_response.status_code == 201
    assert create_response.headers["etag"] == 'W/"1"'
    assert "last-modified" in create_response.headers
    assert create_response.headers["location"].endswith("/_history/1")

    patient = create_response.json()
    resource_id = patient["id"]
    assert patient["meta"]["versionId"] == "1"

    read = client.get(f"/Patient/{resource_id}")
    assert read.status_code == 200
    assert read.headers["etag"] == 'W/"1"'
    assert read.json()["id"] == resource_id

    update = client.put(
        f"/Patient/{resource_id}",
        headers={"Content-Type": FHIR_JSON, "If-Match": 'W/"1"'},
        json={**patient, "active": True},
    )
    assert update.status_code == 200
    assert update.headers["etag"] == 'W/"2"'
    assert update.json()["active"] is True

    stale = client.put(
        f"/Patient/{resource_id}",
        headers={"Content-Type": FHIR_JSON, "If-Match": 'W/"1"'},
        json={**patient, "active": False},
    )
    assert stale.status_code == 412

    search = client.get(f"/Patient?family={family}")
    assert search.status_code == 200
    bundle = search.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "searchset"
    assert any(entry["resource"]["id"] == resource_id for entry in bundle.get("entry", []))

    history = client.get(f"/Patient/{resource_id}/_history")
    assert history.status_code == 200
    assert history.json()["type"] == "history"
    assert history.json()["total"] >= 2

    vread = client.get(f"/Patient/{resource_id}/_history/1")
    assert vread.status_code == 200
    assert vread.json()["meta"]["versionId"] == "1"

    delete_response = client.delete(f"/Patient/{resource_id}")
    assert delete_response.status_code == 204

    gone = client.get(f"/Patient/{resource_id}")
    assert gone.status_code == 410
    assert gone.json()["resourceType"] == "OperationOutcome"


def test_observation_requires_code(client: httpx.Client) -> None:
    response = client.post(
        "/Observation",
        headers={"Content-Type": FHIR_JSON},
        json={"resourceType": "Observation", "status": "final"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["resourceType"] == "OperationOutcome"
    assert "code" in body["issue"][0]["diagnostics"]


def test_upsert_via_put_creates_with_client_id(client: httpx.Client) -> None:
    client_id = _unique("upsert").replace("-", "")[:60]
    response = client.put(
        f"/Patient/{client_id}",
        headers={"Content-Type": FHIR_JSON},
        json={
            "resourceType": "Patient",
            "id": client_id,
            "name": [{"family": "Upsert"}],
        },
    )

    assert response.status_code == 201
    assert response.json()["id"] == client_id

    read = client.get(f"/Patient/{client_id}")
    assert read.status_code == 200
    assert read.json()["name"][0]["family"] == "Upsert"
