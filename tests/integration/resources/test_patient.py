"""Integration tests for Patient CRUD, search, history, and upsert."""

from __future__ import annotations

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

FHIR_JSON = "application/fhir+json"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def patient(client: httpx.Client) -> dict:
    r = client.post(
        "/Patient",
        headers={"Content-Type": FHIR_JSON},
        json={
            "resourceType": "Patient",
            "name": [{"use": "official", "family": _unique("Integ"), "given": ["Test"]}],
            "gender": "unknown",
        },
    )
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def updated_patient(client: httpx.Client, patient: dict) -> dict:
    r = client.put(
        f"/Patient/{patient['id']}",
        headers={"Content-Type": FHIR_JSON, "If-Match": 'W/"1"'},
        json={**patient, "active": True},
    )
    assert r.status_code == 200
    return r.json()


class TestCreate:
    def test_returns_201_with_version_headers(self, client: httpx.Client) -> None:
        r = client.post(
            "/Patient",
            headers={"Content-Type": FHIR_JSON},
            json={"resourceType": "Patient", "name": [{"family": _unique("Create")}]},
        )
        assert r.status_code == 201
        assert r.headers["etag"] == 'W/"1"'
        assert "last-modified" in r.headers
        assert r.headers["location"].endswith("/_history/1")
        assert r.json()["meta"]["versionId"] == "1"


class TestRead:
    def test_returns_current_version(self, client: httpx.Client, patient: dict) -> None:
        r = client.get(f"/Patient/{patient['id']}")
        assert r.status_code == 200
        assert r.headers["etag"] == 'W/"1"'
        assert r.json()["id"] == patient["id"]

    def test_missing_resource_returns_404(self, client: httpx.Client) -> None:
        r = client.get("/Patient/does-not-exist-xyz")
        assert r.status_code == 404


class TestUpdate:
    def test_increments_version_and_etag(self, client: httpx.Client, patient: dict) -> None:
        r = client.put(
            f"/Patient/{patient['id']}",
            headers={"Content-Type": FHIR_JSON, "If-Match": 'W/"1"'},
            json={**patient, "active": True},
        )
        assert r.status_code == 200
        assert r.headers["etag"] == 'W/"2"'
        assert r.json()["meta"]["versionId"] == "2"
        assert r.json()["active"] is True

    def test_stale_etag_returns_412(self, client: httpx.Client, updated_patient: dict) -> None:
        r = client.put(
            f"/Patient/{updated_patient['id']}",
            headers={"Content-Type": FHIR_JSON, "If-Match": 'W/"1"'},
            json={**updated_patient, "active": False},
        )
        assert r.status_code == 412


class TestSearch:
    def test_search_by_family_returns_match(self, client: httpx.Client, patient: dict) -> None:
        family = patient["name"][0]["family"]
        r = client.get(f"/Patient?family={family}")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"
        ids = [e["resource"]["id"] for e in bundle.get("entry", [])]
        assert patient["id"] in ids


class TestHistory:
    def test_history_grows_after_update(self, client: httpx.Client, updated_patient: dict) -> None:
        r = client.get(f"/Patient/{updated_patient['id']}/_history")
        assert r.status_code == 200
        assert r.json()["type"] == "history"
        assert r.json()["total"] >= 2

    def test_vread_returns_original_version(
        self, client: httpx.Client, updated_patient: dict
    ) -> None:
        r = client.get(f"/Patient/{updated_patient['id']}/_history/1")
        assert r.status_code == 200
        assert r.json()["meta"]["versionId"] == "1"
        assert "active" not in r.json()


class TestDelete:
    def test_returns_204(self, client: httpx.Client, patient: dict) -> None:
        r = client.delete(f"/Patient/{patient['id']}")
        assert r.status_code == 204

    def test_read_after_delete_returns_410(self, client: httpx.Client, patient: dict) -> None:
        client.delete(f"/Patient/{patient['id']}")
        r = client.get(f"/Patient/{patient['id']}")
        assert r.status_code == 410
        assert r.json()["resourceType"] == "OperationOutcome"


class TestUpsert:
    def test_put_creates_with_client_id(self, client: httpx.Client) -> None:
        client_id = _unique("upsert").replace("-", "")[:60]
        r = client.put(
            f"/Patient/{client_id}",
            headers={"Content-Type": FHIR_JSON},
            json={"resourceType": "Patient", "id": client_id, "name": [{"family": "Upsert"}]},
        )
        assert r.status_code == 201
        assert r.json()["id"] == client_id

    def test_upserted_resource_is_readable(self, client: httpx.Client) -> None:
        client_id = _unique("upsert").replace("-", "")[:60]
        client.put(
            f"/Patient/{client_id}",
            headers={"Content-Type": FHIR_JSON},
            json={"resourceType": "Patient", "id": client_id, "name": [{"family": "Upsert"}]},
        )
        r = client.get(f"/Patient/{client_id}")
        assert r.status_code == 200
        assert r.json()["name"][0]["family"] == "Upsert"
