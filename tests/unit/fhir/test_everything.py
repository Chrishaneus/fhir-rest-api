"""Unit tests for GET /{resource_type}/{id}/$everything."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _patient(family: str = "Everything") -> dict:
    return {"resourceType": "Patient", "name": [{"family": family}]}


def _observation(patient_id: str) -> dict:
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
        "subject": {"reference": f"Patient/{patient_id}"},
    }


def _condition(patient_id: str) -> dict:
    return {
        "resourceType": "Condition",
        "subject": {"reference": f"Patient/{patient_id}"},
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "73211009"}]},
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "active",
                }
            ]
        },
    }


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestPatientEverythingErrors:
    def test_unknown_patient_returns_404(self, client: TestClient) -> None:
        r = client.get("/Patient/no-such-patient/$everything")
        assert r.status_code == 404
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_deleted_patient_returns_410(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient()).json()["id"]
        client.delete(f"/Patient/{pid}")
        r = client.get(f"/Patient/{pid}/$everything")
        assert r.status_code == 410

    def test_invalid_id_returns_404(self, client: TestClient) -> None:
        r = client.get("/Patient/../../etc/$everything")
        assert r.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Bundle structure
# ---------------------------------------------------------------------------


class TestPatientEverythingBundle:
    def test_returns_searchset_bundle(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient()).json()["id"]
        r = client.get(f"/Patient/{pid}/$everything")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"

    def test_patient_only_when_no_linked_resources(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient()).json()["id"]
        bundle = client.get(f"/Patient/{pid}/$everything").json()
        assert bundle["total"] == 1
        resources = [e["resource"] for e in bundle["entry"] if "resource" in e]
        assert len(resources) == 1
        assert resources[0]["resourceType"] == "Patient"
        assert resources[0]["id"] == pid

    def test_patient_entry_has_match_search_mode(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient()).json()["id"]
        bundle = client.get(f"/Patient/{pid}/$everything").json()
        patient_entry = next(
            e for e in bundle["entry"] if e.get("resource", {}).get("resourceType") == "Patient"
        )
        assert patient_entry["search"]["mode"] == "match"

    def test_linked_resources_included_in_bundle(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient()).json()["id"]
        client.post("/Observation", json=_observation(pid))
        bundle = client.get(f"/Patient/{pid}/$everything").json()
        assert bundle["total"] == 2
        types = {e["resource"]["resourceType"] for e in bundle["entry"] if "resource" in e}
        assert "Patient" in types
        assert "Observation" in types

    def test_linked_entries_have_include_search_mode(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient()).json()["id"]
        client.post("/Observation", json=_observation(pid))
        bundle = client.get(f"/Patient/{pid}/$everything").json()
        obs_entry = next(
            e for e in bundle["entry"] if e.get("resource", {}).get("resourceType") == "Observation"
        )
        assert obs_entry["search"]["mode"] == "include"

    def test_multiple_resource_types_all_included(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient()).json()["id"]
        client.post("/Observation", json=_observation(pid))
        client.post("/Condition", json=_condition(pid))
        bundle = client.get(f"/Patient/{pid}/$everything").json()
        assert bundle["total"] == 3
        types = {e["resource"]["resourceType"] for e in bundle["entry"] if "resource" in e}
        assert types == {"Patient", "Observation", "Condition"}

    def test_only_resources_for_this_patient_included(self, client: TestClient) -> None:
        pid1 = client.post("/Patient", json=_patient("Alice")).json()["id"]
        pid2 = client.post("/Patient", json=_patient("Bob")).json()["id"]
        client.post("/Observation", json=_observation(pid1))
        client.post("/Observation", json=_observation(pid2))
        bundle = client.get(f"/Patient/{pid1}/$everything").json()
        # Total = patient1 + 1 observation for patient1 only
        assert bundle["total"] == 2
        obs_entries = [
            e for e in bundle["entry"] if e.get("resource", {}).get("resourceType") == "Observation"
        ]
        assert len(obs_entries) == 1
        assert obs_entries[0]["resource"]["subject"]["reference"] == f"Patient/{pid1}"

    def test_has_self_link(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient()).json()["id"]
        bundle = client.get(f"/Patient/{pid}/$everything").json()
        assert any(link["relation"] == "self" for link in bundle["link"])


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPatientEverythingPagination:
    def test_count_limits_entries_returned(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient()).json()["id"]
        for _ in range(3):
            client.post("/Observation", json=_observation(pid))
        bundle = client.get(f"/Patient/{pid}/$everything?_count=2").json()
        assert len(bundle["entry"]) == 2
        assert bundle["total"] == 4  # patient + 3 observations

    def test_next_link_present_when_paginated(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient()).json()["id"]
        for _ in range(3):
            client.post("/Observation", json=_observation(pid))
        bundle = client.get(f"/Patient/{pid}/$everything?_count=2").json()
        assert any(link["relation"] == "next" for link in bundle["link"])


# ---------------------------------------------------------------------------
# Generic route — non-Patient anchor
# ---------------------------------------------------------------------------


class TestGenericEverything:
    """Verify the route works for any resource type, not just Patient."""

    def _obs(self) -> dict:
        return {
            "resourceType": "Observation",
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
        }

    def test_anchor_returns_200_searchset(self, client: TestClient) -> None:
        oid = client.post("/Observation", json=self._obs()).json()["id"]
        r = client.get(f"/Observation/{oid}/$everything")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"

    def test_anchor_is_match_entry(self, client: TestClient) -> None:
        oid = client.post("/Observation", json=self._obs()).json()["id"]
        bundle = client.get(f"/Observation/{oid}/$everything").json()
        match_entry = next(e for e in bundle["entry"] if e["search"]["mode"] == "match")
        assert match_entry["resource"]["id"] == oid
        assert match_entry["resource"]["resourceType"] == "Observation"

    def test_anchor_alone_has_total_1(self, client: TestClient) -> None:
        oid = client.post("/Observation", json=self._obs()).json()["id"]
        bundle = client.get(f"/Observation/{oid}/$everything").json()
        assert bundle["total"] == 1

    def test_unknown_resource_returns_404(self, client: TestClient) -> None:
        r = client.get("/Observation/no-such-obs/$everything")
        assert r.status_code == 404
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_deleted_resource_returns_410(self, client: TestClient) -> None:
        oid = client.post("/Observation", json=self._obs()).json()["id"]
        client.delete(f"/Observation/{oid}")
        assert client.get(f"/Observation/{oid}/$everything").status_code == 410

    def test_unsupported_type_returns_404(self, client: TestClient) -> None:
        r = client.get("/Unicorn/some-id/$everything")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Capability statement
# ---------------------------------------------------------------------------


class TestEverythingCapability:
    def test_operation_advertised_for_all_resource_types(self, client: TestClient) -> None:
        meta = client.get("/metadata").json()
        for resource in meta["rest"][0]["resource"]:
            op_names = [op["name"] for op in resource.get("operation", [])]
            assert "$everything" in op_names, f"$everything missing from {resource['type']}"

    def test_operation_definition_url_correct(self, client: TestClient) -> None:
        meta = client.get("/metadata").json()
        patient_resource = next(r for r in meta["rest"][0]["resource"] if r["type"] == "Patient")
        everything_op = next(
            op for op in patient_resource["operation"] if op["name"] == "$everything"
        )
        assert (
            everything_op["definition"]
            == "http://hl7.org/fhir/OperationDefinition/Resource-everything"
        )
