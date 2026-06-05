"""Integration tests for FHIR meta.security label filtering.

Requires a running FHIR server (default: https://localhost).
The session-level `client` fixture already carries an admin token;
role-specific fixtures register separate throwaway users.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

FHIR_JSON = "application/fhir+json"
SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-Confidentiality"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _label(code: str) -> dict:
    return {"system": SYSTEM, "code": code}


def _patient(code: str | None = None) -> dict:
    resource: dict = {
        "resourceType": "Patient",
        "name": [{"family": f"SecurityTest-{uuid.uuid4().hex[:6]}"}],
    }
    if code is not None:
        resource["meta"] = {"security": [_label(code)]}
    return resource


def _register_and_login(base_url: str, role: str, verify: bool) -> str:
    username = f"sl-{role}-{uuid.uuid4().hex[:8]}"
    password = uuid.uuid4().hex
    with httpx.Client(base_url=base_url, timeout=10.0, verify=verify) as http:
        reg = http.post(
            "/auth/register", json={"username": username, "password": password, "role": role}
        )
        if reg.status_code not in (201, 409):
            pytest.skip(f"Could not register {role} user: {reg.status_code}")
        login = http.post("/auth/login", json={"username": username, "password": password})
        if login.status_code != 200:
            pytest.skip(f"Could not log in as {role}: {login.status_code}")
        return login.json()["access_token"]


# ---------------------------------------------------------------------------
# Role-specific client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def viewer_client(base_url, client):
    from tests.integration.conftest import VERIFY_CERTIFICATES

    token = _register_and_login(base_url, "viewer", VERIFY_CERTIFICATES)
    if not token:
        pytest.skip("Server has no JWT_SECRET — skipping role-specific auth tests")

    transport = httpx.HTTPTransport(retries=0, verify=VERIFY_CERTIFICATES)
    with httpx.Client(
        base_url=base_url,
        timeout=10.0,
        headers={"Accept": FHIR_JSON, "Authorization": f"Bearer {token}"},
        transport=transport,
        verify=VERIFY_CERTIFICATES,
    ) as http:
        yield http


@pytest.fixture(scope="module")
def clinician_client(base_url, client):
    from tests.integration.conftest import VERIFY_CERTIFICATES

    token = _register_and_login(base_url, "clinician", VERIFY_CERTIFICATES)
    if not token:
        pytest.skip("Server has no JWT_SECRET — skipping role-specific auth tests")

    transport = httpx.HTTPTransport(retries=0, verify=VERIFY_CERTIFICATES)
    with httpx.Client(
        base_url=base_url,
        timeout=10.0,
        headers={"Accept": FHIR_JSON, "Authorization": f"Bearer {token}"},
        transport=transport,
        verify=VERIFY_CERTIFICATES,
    ) as http:
        yield http


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReadAccess:
    def test_viewer_cannot_read_very_restricted(
        self, client: httpx.Client, viewer_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = viewer_client.get(f"/Patient/{resource_id}")
        assert r.status_code == 403

    def test_viewer_cannot_read_restricted(self, client: httpx.Client, viewer_client: httpx.Client):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = viewer_client.get(f"/Patient/{resource_id}")
        assert r.status_code == 403

    def test_viewer_can_read_normal(self, client: httpx.Client, viewer_client: httpx.Client):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("N"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = viewer_client.get(f"/Patient/{resource_id}")
        assert r.status_code == 200

    def test_clinician_can_read_restricted(
        self, client: httpx.Client, clinician_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = clinician_client.get(f"/Patient/{resource_id}")
        assert r.status_code == 200

    def test_clinician_cannot_read_very_restricted(
        self, client: httpx.Client, clinician_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = clinician_client.get(f"/Patient/{resource_id}")
        assert r.status_code == 403

    def test_viewer_can_read_unlabeled(self, client: httpx.Client, viewer_client: httpx.Client):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient())
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = viewer_client.get(f"/Patient/{resource_id}")
        assert r.status_code == 200

    def test_admin_can_read_very_restricted(self, client: httpx.Client):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.get(f"/Patient/{resource_id}")
        assert r.status_code == 200


class TestVread:
    def test_viewer_cannot_vread_restricted_version(
        self, client: httpx.Client, viewer_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = viewer_client.get(f"/Patient/{resource_id}/_history/1")
        assert r.status_code == 403

    def test_clinician_can_vread_restricted(
        self, client: httpx.Client, clinician_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = clinician_client.get(f"/Patient/{resource_id}/_history/1")
        assert r.status_code == 200


class TestSearchAccess:
    def test_restricted_resource_absent_from_viewer_search(
        self, client: httpx.Client, viewer_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V"))
        assert r.status_code == 201
        restricted_id = r.json()["id"]

        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("N"))
        assert r.status_code == 201
        normal_id = r.json()["id"]

        r = viewer_client.get(f"/Patient?_id={restricted_id},{normal_id}")
        assert r.status_code == 200
        ids = {e["resource"]["id"] for e in r.json().get("entry", [])}
        assert restricted_id not in ids
        assert normal_id in ids

    def test_total_reflects_filtered_count(self, client: httpx.Client, viewer_client: httpx.Client):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V"))
        assert r.status_code == 201
        v_id = r.json()["id"]

        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("N"))
        assert r.status_code == 201
        n_id = r.json()["id"]

        r = viewer_client.get(f"/Patient?_id={v_id},{n_id}&_total=accurate")
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_clinician_post_search_also_filters(
        self, client: httpx.Client, clinician_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V"))
        assert r.status_code == 201
        restricted_id = r.json()["id"]

        r = clinician_client.post(
            "/Patient/_search",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200
        ids = {e["resource"]["id"] for e in r.json().get("entry", [])}
        assert restricted_id not in ids


class TestWriteAccess:
    def test_clinician_cannot_create_very_restricted(self, clinician_client: httpx.Client):
        r = clinician_client.post(
            "/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V")
        )
        assert r.status_code == 403

    def test_clinician_can_create_restricted(self, clinician_client: httpx.Client):
        r = clinician_client.post(
            "/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("R")
        )
        assert r.status_code == 201

    def test_clinician_cannot_update_very_restricted_resource(
        self, client: httpx.Client, clinician_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = clinician_client.put(
            f"/Patient/{resource_id}",
            headers={"Content-Type": FHIR_JSON},
            json={**_patient("V"), "id": resource_id},
        )
        assert r.status_code == 403

    def test_clinician_cannot_patch_very_restricted_resource(
        self, client: httpx.Client, clinician_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = clinician_client.patch(
            f"/Patient/{resource_id}",
            headers={"Content-Type": "application/json-patch+json"},
            json=[{"op": "add", "path": "/active", "value": True}],
        )
        assert r.status_code == 403

    def test_clinician_cannot_upgrade_label_to_very_restricted(
        self, client: httpx.Client, clinician_client: httpx.Client
    ):
        r = clinician_client.post(
            "/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("R")
        )
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = clinician_client.put(
            f"/Patient/{resource_id}",
            headers={"Content-Type": FHIR_JSON},
            json={**_patient("V"), "id": resource_id},
        )
        assert r.status_code == 403


class TestHistory:
    def test_viewer_cannot_access_instance_history_of_restricted(
        self, client: httpx.Client, viewer_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = viewer_client.get(f"/Patient/{resource_id}/_history")
        assert r.status_code == 403

    def test_type_history_filters_restricted_entries(
        self, client: httpx.Client, viewer_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V"))
        assert r.status_code == 201
        restricted_id = r.json()["id"]

        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("N"))
        assert r.status_code == 201

        r = viewer_client.get(f"/Patient/_history?_id={restricted_id}")
        assert r.status_code == 200
        ids = [
            e.get("resource", {}).get("id")
            for e in r.json().get("entry", [])
            if e.get("request", {}).get("method") != "DELETE"
        ]
        assert restricted_id not in ids


class TestEverything:
    def test_viewer_cannot_access_everything_on_restricted_anchor(
        self, client: httpx.Client, viewer_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = viewer_client.get(f"/Patient/{resource_id}/$everything")
        assert r.status_code == 403

    def test_everything_filters_restricted_linked_resources(
        self, client: httpx.Client, viewer_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("N"))
        assert r.status_code == 201
        patient_id = r.json()["id"]

        obs_v = {
            "resourceType": "Observation",
            "status": "final",
            "code": {"text": "test"},
            "subject": {"reference": f"Patient/{patient_id}"},
            "meta": {"security": [_label("V")]},
        }
        r = client.post("/Observation", headers={"Content-Type": FHIR_JSON}, json=obs_v)
        assert r.status_code == 201
        obs_id = r.json()["id"]

        r = viewer_client.get(f"/Patient/{patient_id}/$everything")
        assert r.status_code == 200
        ids = {e["resource"]["id"] for e in r.json().get("entry", [])}
        assert patient_id in ids
        assert obs_id not in ids


class TestCompartmentSearch:
    def test_viewer_compartment_search_filters_restricted_resources(
        self, client: httpx.Client, viewer_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("N"))
        assert r.status_code == 201
        patient_id = r.json()["id"]

        obs_n = {
            "resourceType": "Observation",
            "status": "final",
            "code": {"text": "normal"},
            "subject": {"reference": f"Patient/{patient_id}"},
        }
        r = client.post("/Observation", headers={"Content-Type": FHIR_JSON}, json=obs_n)
        assert r.status_code == 201
        normal_obs_id = r.json()["id"]

        obs_v = {
            "resourceType": "Observation",
            "status": "final",
            "code": {"text": "restricted"},
            "subject": {"reference": f"Patient/{patient_id}"},
            "meta": {"security": [_label("V")]},
        }
        r = client.post("/Observation", headers={"Content-Type": FHIR_JSON}, json=obs_v)
        assert r.status_code == 201
        restricted_obs_id = r.json()["id"]

        r = viewer_client.get(f"/Patient/{patient_id}/Observation")
        assert r.status_code == 200
        ids = {e["resource"]["id"] for e in r.json().get("entry", [])}
        assert normal_obs_id in ids
        assert restricted_obs_id not in ids


class TestConditionalUpdate:
    def test_clinician_cannot_conditional_update_restricted_resource(
        self, client: httpx.Client, clinician_client: httpx.Client
    ):
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V"))
        assert r.status_code == 201
        v_id = r.json()["id"]

        r = clinician_client.put(
            f"/Patient?_id={v_id}",
            headers={"Content-Type": FHIR_JSON},
            json={**_patient("V"), "id": v_id},
        )
        assert r.status_code == 403


class TestIfNoneExist:
    def test_restricted_match_invisible_to_clinician(
        self, client: httpx.Client, clinician_client: httpx.Client
    ):
        # Admin creates a V-labeled patient
        r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("V"))
        assert r.status_code == 201
        v_id = r.json()["id"]

        # Clinician conditional-create — V match is invisible (ceiling R) → creates new resource
        r = clinician_client.post(
            "/Patient",
            headers={"Content-Type": FHIR_JSON, "If-None-Exist": f"_id={v_id}"},
            json=_patient("R"),
        )
        assert r.status_code == 201

    def test_visible_match_returned_to_clinician(self, clinician_client: httpx.Client):
        # Clinician creates an R-labeled patient (within their clearance)
        r = clinician_client.post(
            "/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient("R")
        )
        assert r.status_code == 201
        existing_id = r.json()["id"]

        # Same clinician re-creates with If-None-Exist — R match is visible → 200 existing resource
        r = clinician_client.post(
            "/Patient",
            headers={"Content-Type": FHIR_JSON, "If-None-Exist": f"_id={existing_id}"},
            json=_patient("R"),
        )
        assert r.status_code == 200
        assert r.json()["id"] == existing_id
