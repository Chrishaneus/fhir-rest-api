"""Tests for PatientHooks (app/hooks/patient.py)."""

import pytest
from fastapi.testclient import TestClient

from app.hooks import hooks
from app.hooks.patient import _CREATED_AT_URL, PatientHooks


@pytest.fixture(autouse=True)
def _register_patient_hooks():
    hooks.register("Patient", PatientHooks())


@pytest.fixture
def base_patient(client: TestClient) -> dict:
    return client.post(
        "/Patient", json={"resourceType": "Patient", "name": [{"family": "Test"}]}
    ).json()


@pytest.fixture
def patient_with_birthdate(client: TestClient) -> dict:
    return client.post(
        "/Patient",
        json={"resourceType": "Patient", "name": [{"family": "DOB"}], "birthDate": "1990-01-01"},
    ).json()


@pytest.fixture
def deceased_patient(client: TestClient) -> dict:
    return client.post(
        "/Patient",
        json={"resourceType": "Patient", "name": [{"family": "Gone"}], "deceasedBoolean": True},
    ).json()


# ---------------------------------------------------------------------------
# before_create
# ---------------------------------------------------------------------------

class TestBeforeCreate:
    def test_stamps_created_at_extension(self, base_patient: dict) -> None:
        ext_urls = {e["url"] for e in base_patient.get("extension", [])}
        assert _CREATED_AT_URL in ext_urls

    def test_created_at_carries_instant_value(self, base_patient: dict) -> None:
        exts = {e["url"]: e for e in base_patient.get("extension", [])}
        assert "valueInstant" in exts[_CREATED_AT_URL]


# ---------------------------------------------------------------------------
# before_update — birthDate immutability
# ---------------------------------------------------------------------------

class TestBeforeUpdateBirthDate:
    def test_change_rejected(self, client: TestClient, patient_with_birthdate: dict) -> None:
        resp = client.put(
            f"/Patient/{patient_with_birthdate['id']}",
            json={**patient_with_birthdate, "birthDate": "2000-12-31"},
        )
        assert resp.status_code == 409
        assert "birthDate" in resp.json()["issue"][0]["diagnostics"]

    def test_same_value_allowed(self, client: TestClient, patient_with_birthdate: dict) -> None:
        resp = client.put(
            f"/Patient/{patient_with_birthdate['id']}",
            json={**patient_with_birthdate, "active": True},
        )
        assert resp.status_code == 200

    def test_setting_for_first_time_allowed(self, client: TestClient, base_patient: dict) -> None:
        resp = client.put(
            f"/Patient/{base_patient['id']}",
            json={**base_patient, "birthDate": "1985-06-15"},
        )
        assert resp.status_code == 200
        assert resp.json()["birthDate"] == "1985-06-15"


# ---------------------------------------------------------------------------
# before_update — created-at extension preservation
# ---------------------------------------------------------------------------

class TestBeforeUpdateExtensionPreservation:
    def test_re_attaches_extension_when_client_omits_it(
        self, client: TestClient, base_patient: dict
    ) -> None:
        original_instant = next(
            e["valueInstant"] for e in base_patient["extension"] if e["url"] == _CREATED_AT_URL
        )
        body = {k: v for k, v in base_patient.items() if k != "extension"}

        updated = client.put(f"/Patient/{base_patient['id']}", json=body).json()

        exts = {e["url"]: e for e in updated.get("extension", [])}
        assert _CREATED_AT_URL in exts
        assert exts[_CREATED_AT_URL]["valueInstant"] == original_instant

    def test_does_not_duplicate_when_client_sends_extension(
        self, client: TestClient, base_patient: dict
    ) -> None:
        updated = client.put(f"/Patient/{base_patient['id']}", json=base_patient).json()

        count = sum(1 for e in updated.get("extension", []) if e["url"] == _CREATED_AT_URL)
        assert count == 1


# ---------------------------------------------------------------------------
# before_delete — deceased protection
# ---------------------------------------------------------------------------

class TestBeforeDelete:
    def test_blocked_for_deceased_boolean(
        self, client: TestClient, deceased_patient: dict
    ) -> None:
        resp = client.delete(f"/Patient/{deceased_patient['id']}")
        assert resp.status_code == 409
        assert "Deceased" in resp.json()["issue"][0]["diagnostics"]

    def test_blocked_for_deceased_datetime(self, client: TestClient) -> None:
        patient = client.post(
            "/Patient",
            json={
                "resourceType": "Patient",
                "name": [{"family": "Gone"}],
                "deceasedDateTime": "2023-03-15T10:00:00Z",
            },
        ).json()

        resp = client.delete(f"/Patient/{patient['id']}")
        assert resp.status_code == 409

    def test_allowed_for_living_patient(self, client: TestClient, base_patient: dict) -> None:
        resp = client.delete(f"/Patient/{base_patient['id']}")
        assert resp.status_code == 204
