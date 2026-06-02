"""Unit tests for FHIR meta.security label filtering.

Tests exercise both the pure logic (user_can_see_resource / filter_for_user)
and the HTTP behaviour of every route that applies the filter.
"""

from __future__ import annotations

from datetime import UTC

from fastapi.testclient import TestClient

from app.utils.fhir.security_labels import filter_for_user, user_can_see_resource

FHIR_JSON = "application/fhir+json"
SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-Confidentiality"


class _StubUser:
    """Minimal stand-in for User — the filter functions only need `.role`."""

    def __init__(self, role: str) -> None:
        self.role = role


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _label(code: str) -> dict:
    return {"system": SYSTEM, "code": code}


def _patient(code: str | None = None) -> dict:
    resource: dict = {"resourceType": "Patient"}
    if code is not None:
        resource["meta"] = {"security": [_label(code)]}
    return resource


def _make_tokens(client: TestClient, jwt_secret: str, monkeypatch) -> dict[str, str]:
    monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
    for username, role in [("sl-admin", "admin"), ("sl-clinician", "clinician"), ("sl-viewer", "viewer")]:
        client.post("/auth/register", json={"username": username, "password": "pass", "role": role})
    tokens = {}
    for username in ("sl-admin", "sl-clinician", "sl-viewer"):
        r = client.post("/auth/login", json={"username": username, "password": "pass"})
        tokens[username] = r.json()["access_token"]
    return tokens


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": FHIR_JSON}


# ---------------------------------------------------------------------------
# Pure logic tests — no HTTP
# ---------------------------------------------------------------------------


class TestUserCanSeeResource:
    def _user(self, role: str) -> _StubUser:
        return _StubUser(role)

    def test_no_label_always_visible(self):
        for role in ("viewer", "clinician", "admin"):
            assert user_can_see_resource(self._user(role), _patient())

    def test_none_user_always_visible(self):
        assert user_can_see_resource(None, _patient("V"))

    def test_viewer_sees_normal(self):
        assert user_can_see_resource(self._user("viewer"), _patient("N"))

    def test_viewer_blocked_by_restricted(self):
        assert not user_can_see_resource(self._user("viewer"), _patient("R"))

    def test_viewer_blocked_by_very_restricted(self):
        assert not user_can_see_resource(self._user("viewer"), _patient("V"))

    def test_clinician_sees_restricted(self):
        assert user_can_see_resource(self._user("clinician"), _patient("R"))

    def test_clinician_blocked_by_very_restricted(self):
        assert not user_can_see_resource(self._user("clinician"), _patient("V"))

    def test_admin_sees_very_restricted(self):
        assert user_can_see_resource(self._user("admin"), _patient("V"))

    def test_bare_code_no_system(self):
        resource = {"resourceType": "Patient", "meta": {"security": [{"code": "V"}]}}
        assert not user_can_see_resource(self._user("viewer"), resource)

    def test_unknown_system_ignored(self):
        resource = {
            "resourceType": "Patient",
            "meta": {"security": [{"system": "http://other.example", "code": "V"}]},
        }
        assert user_can_see_resource(self._user("viewer"), resource)

    def test_unknown_role_denied(self):
        user = self._user("superuser")
        assert not user_can_see_resource(user, _patient("R"))


class TestFilterForUser:
    def _user(self, role: str) -> _StubUser:
        return _StubUser(role)

    def _version(self, code: str | None = None):
        from datetime import datetime

        from app.store import ResourceVersion

        resource = _patient(code)
        return ResourceVersion(
            version_id="1",
            resource=resource,
            last_updated=datetime.now(UTC),
            deleted=False,
        )

    def _tombstone(self):
        from datetime import datetime

        from app.store import ResourceVersion

        return ResourceVersion(
            version_id="2",
            resource=None,
            last_updated=datetime.now(UTC),
            deleted=True,
        )

    def test_filters_restricted_from_viewer(self):
        versions = [self._version("N"), self._version("R"), self._version("V")]
        result = filter_for_user(self._user("viewer"), versions)
        assert len(result) == 1

    def test_tombstones_always_pass(self):
        versions = [self._tombstone()]
        result = filter_for_user(self._user("viewer"), versions)
        assert len(result) == 1

    def test_none_user_returns_all(self):
        versions = [self._version("V"), self._version("R")]
        result = filter_for_user(None, versions)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# HTTP-level tests — TestClient against SQLite in-memory app
# ---------------------------------------------------------------------------


class TestSecurityLabelRead:
    def test_viewer_cannot_read_very_restricted(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("V"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.get(f"/Patient/{resource_id}", headers={"Authorization": f"Bearer {tokens['sl-viewer']}"})
        assert r.status_code == 403
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_viewer_cannot_read_restricted(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.get(f"/Patient/{resource_id}", headers={"Authorization": f"Bearer {tokens['sl-viewer']}"})
        assert r.status_code == 403

    def test_viewer_can_read_normal(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("N"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.get(f"/Patient/{resource_id}", headers={"Authorization": f"Bearer {tokens['sl-viewer']}"})
        assert r.status_code == 200

    def test_viewer_can_read_unlabeled(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient())
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.get(f"/Patient/{resource_id}", headers={"Authorization": f"Bearer {tokens['sl-viewer']}"})
        assert r.status_code == 200

    def test_clinician_can_read_restricted(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.get(f"/Patient/{resource_id}", headers={"Authorization": f"Bearer {tokens['sl-clinician']}"})
        assert r.status_code == 200

    def test_clinician_cannot_read_very_restricted(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("V"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.get(f"/Patient/{resource_id}", headers={"Authorization": f"Bearer {tokens['sl-clinician']}"})
        assert r.status_code == 403

    def test_admin_can_read_very_restricted(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("V"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.get(f"/Patient/{resource_id}", headers={"Authorization": f"Bearer {tokens['sl-admin']}"})
        assert r.status_code == 200


class TestSecurityLabelVread:
    def test_viewer_cannot_vread_restricted_version(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.get(
            f"/Patient/{resource_id}/_history/1",
            headers={"Authorization": f"Bearer {tokens['sl-viewer']}"},
        )
        assert r.status_code == 403


class TestSecurityLabelSearch:
    def test_restricted_resource_excluded_from_viewer_search(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("V"))
        assert r.status_code == 201
        restricted_id = r.json()["id"]

        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("N"))
        assert r.status_code == 201
        normal_id = r.json()["id"]

        r = client.get("/Patient", headers={"Authorization": f"Bearer {tokens['sl-viewer']}"})
        assert r.status_code == 200
        ids = {e["resource"]["id"] for e in r.json().get("entry", [])}
        assert restricted_id not in ids
        assert normal_id in ids

    def test_clinician_post_search_also_filters(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("V"))
        assert r.status_code == 201
        restricted_id = r.json()["id"]

        r = client.post(
            "/Patient/_search",
            headers={"Authorization": f"Bearer {tokens['sl-clinician']}", "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200
        ids = {e["resource"]["id"] for e in r.json().get("entry", [])}
        assert restricted_id not in ids

    def test_total_count_reflects_filtered_results(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("V"))
        client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("N"))

        r = client.get("/Patient?_total=accurate", headers={"Authorization": f"Bearer {tokens['sl-viewer']}"})
        assert r.status_code == 200
        # Total must reflect post-filter count (viewer sees only N)
        assert r.json()["total"] == 1


class TestSecurityLabelWrite:
    def test_clinician_cannot_create_very_restricted(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-clinician"]), json=_patient("V"))
        assert r.status_code == 403

    def test_clinician_can_create_restricted(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-clinician"]), json=_patient("R"))
        assert r.status_code == 201

    def test_clinician_cannot_update_very_restricted_resource(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("V"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        update_body = {**_patient("V"), "id": resource_id}
        r = client.put(
            f"/Patient/{resource_id}",
            headers=_auth(tokens["sl-clinician"]),
            json=update_body,
        )
        assert r.status_code == 403

    def test_clinician_cannot_patch_very_restricted_resource(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("V"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.patch(
            f"/Patient/{resource_id}",
            headers={
                "Authorization": f"Bearer {tokens['sl-clinician']}",
                "Content-Type": "application/json-patch+json",
            },
            json=[{"op": "add", "path": "/active", "value": True}],
        )
        assert r.status_code == 403

    def test_clinician_cannot_upgrade_label_to_very_restricted(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-clinician"]), json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        update_body = {**_patient("V"), "id": resource_id}
        r = client.put(
            f"/Patient/{resource_id}",
            headers=_auth(tokens["sl-clinician"]),
            json=update_body,
        )
        assert r.status_code == 403


class TestSecurityLabelHistory:
    def test_viewer_cannot_access_instance_history_of_restricted(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.get(
            f"/Patient/{resource_id}/_history",
            headers={"Authorization": f"Bearer {tokens['sl-viewer']}"},
        )
        assert r.status_code == 403

    def test_type_history_filters_restricted_entries(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("V"))
        assert r.status_code == 201
        restricted_id = r.json()["id"]

        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("N"))
        assert r.status_code == 201

        r = client.get("/Patient/_history", headers={"Authorization": f"Bearer {tokens['sl-viewer']}"})
        assert r.status_code == 200
        ids = [
            e.get("resource", {}).get("id")
            for e in r.json().get("entry", [])
            if e.get("request", {}).get("method") not in ("DELETE",)
        ]
        assert restricted_id not in ids


class TestSecurityLabelEverything:
    def test_viewer_cannot_access_everything_on_restricted_anchor(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("R"))
        assert r.status_code == 201
        resource_id = r.json()["id"]

        r = client.get(
            f"/Patient/{resource_id}/$everything",
            headers={"Authorization": f"Bearer {tokens['sl-viewer']}"},
        )
        assert r.status_code == 403

    def test_everything_filters_restricted_linked_resources(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("N"))
        assert r.status_code == 201
        patient_id = r.json()["id"]

        # Create a V-labeled Observation linked to the patient
        obs_body = {
            "resourceType": "Observation",
            "status": "final",
            "code": {"text": "test"},
            "subject": {"reference": f"Patient/{patient_id}"},
            "meta": {"security": [{"system": SYSTEM, "code": "V"}]},
        }
        r = client.post("/Observation", headers=_auth(tokens["sl-admin"]), json=obs_body)
        assert r.status_code == 201
        obs_id = r.json()["id"]

        r = client.get(
            f"/Patient/{patient_id}/$everything",
            headers={"Authorization": f"Bearer {tokens['sl-viewer']}"},
        )
        assert r.status_code == 200
        ids = {e["resource"]["id"] for e in r.json().get("entry", [])}
        assert patient_id in ids
        assert obs_id not in ids


class TestSecurityLabelCompartmentSearch:
    def test_viewer_compartment_search_filters_restricted_resources(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("N"))
        assert r.status_code == 201
        patient_id = r.json()["id"]

        # N-labeled observation — viewer should see this
        obs_n = {
            "resourceType": "Observation",
            "status": "final",
            "code": {"text": "normal"},
            "subject": {"reference": f"Patient/{patient_id}"},
        }
        r = client.post("/Observation", headers=_auth(tokens["sl-admin"]), json=obs_n)
        assert r.status_code == 201
        normal_obs_id = r.json()["id"]

        # V-labeled observation — viewer should NOT see this
        obs_v = {
            "resourceType": "Observation",
            "status": "final",
            "code": {"text": "restricted"},
            "subject": {"reference": f"Patient/{patient_id}"},
            "meta": {"security": [{"system": SYSTEM, "code": "V"}]},
        }
        r = client.post("/Observation", headers=_auth(tokens["sl-admin"]), json=obs_v)
        assert r.status_code == 201
        restricted_obs_id = r.json()["id"]

        r = client.get(
            f"/Patient/{patient_id}/Observation",
            headers={"Authorization": f"Bearer {tokens['sl-viewer']}"},
        )
        assert r.status_code == 200
        ids = {e["resource"]["id"] for e in r.json().get("entry", [])}
        assert normal_obs_id in ids
        assert restricted_obs_id not in ids


class TestSecurityLabelConditionalUpdate:
    def test_clinician_cannot_conditional_update_restricted_resource(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("V"))
        assert r.status_code == 201
        v_id = r.json()["id"]

        r = client.put(
            f"/Patient?_id={v_id}",
            headers=_auth(tokens["sl-clinician"]),
            json={**_patient("V"), "id": v_id},
        )
        assert r.status_code == 403

    def test_clinician_can_conditional_update_accessible_resource(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        r = client.post("/Patient", headers=_auth(tokens["sl-clinician"]), json=_patient("R"))
        assert r.status_code == 201
        r_id = r.json()["id"]

        r = client.put(
            f"/Patient?_id={r_id}",
            headers=_auth(tokens["sl-clinician"]),
            json={**_patient("R"), "id": r_id},
        )
        assert r.status_code == 200


class TestSecurityLabelIfNoneExist:
    def test_restricted_match_invisible_to_clinician(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        # Admin creates a V-labeled patient
        r = client.post("/Patient", headers=_auth(tokens["sl-admin"]), json=_patient("V"))
        assert r.status_code == 201
        v_id = r.json()["id"]

        # Clinician conditional-create — V match is invisible (ceiling R) → creates new resource
        r = client.post(
            "/Patient",
            headers={
                "Authorization": f"Bearer {tokens['sl-clinician']}",
                "Content-Type": FHIR_JSON,
                "If-None-Exist": f"_id={v_id}",
            },
            json=_patient("R"),
        )
        assert r.status_code == 201

    def test_visible_match_returned_to_clinician(self, client, jwt_secret, monkeypatch):
        tokens = _make_tokens(client, jwt_secret, monkeypatch)
        # Clinician creates an R-labeled patient (within their clearance)
        r = client.post("/Patient", headers=_auth(tokens["sl-clinician"]), json=_patient("R"))
        assert r.status_code == 201
        existing_id = r.json()["id"]

        # Same clinician re-creates with If-None-Exist — R match is visible → 200 existing resource
        r = client.post(
            "/Patient",
            headers={
                "Authorization": f"Bearer {tokens['sl-clinician']}",
                "Content-Type": FHIR_JSON,
                "If-None-Exist": f"_id={existing_id}",
            },
            json=_patient("R"),
        )
        assert r.status_code == 200
        assert r.json()["id"] == existing_id
