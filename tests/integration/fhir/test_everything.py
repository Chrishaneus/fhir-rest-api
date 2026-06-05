from __future__ import annotations

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration


def _post(client: httpx.Client, resource: dict) -> dict:
    rt = resource["resourceType"]
    r = client.post(f"/{rt}", headers={"Content-Type": FHIR_JSON}, json=resource)
    assert r.status_code == 201
    return r.json()


def _patient(family: str = "Everything") -> dict:
    return {"resourceType": "Patient", "name": [{"family": family}]}


def _observation(patient_id: str) -> dict:
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "55284-4"}]},
        "subject": {"reference": f"Patient/{patient_id}"},
    }


def _standalone_observation() -> dict:
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "55284-4"}]},
    }


class TestPatientEverything:
    def test_unknown_patient_returns_404(self, client: httpx.Client) -> None:
        r = client.get("/Patient/no-such-patient-xyz/$everything")
        assert r.status_code == 404
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_deleted_patient_returns_410(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("Deleted"))["id"]
        client.delete(f"/Patient/{pid}")
        assert client.get(f"/Patient/{pid}/$everything").status_code == 410

    def test_patient_alone_returns_total_1(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("Alone"))["id"]
        bundle = client.get(f"/Patient/{pid}/$everything").json()
        assert bundle["type"] == "searchset"
        assert bundle["total"] == 1
        assert bundle["entry"][0]["resource"]["id"] == pid
        assert bundle["entry"][0]["search"]["mode"] == "match"

    def test_linked_observations_included(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("WithObs"))["id"]
        _post(client, _observation(pid))
        _post(client, _observation(pid))
        bundle = client.get(f"/Patient/{pid}/$everything").json()
        assert bundle["total"] == 3
        types = {e["resource"]["resourceType"] for e in bundle["entry"]}
        assert "Patient" in types
        assert "Observation" in types

    def test_linked_entries_have_include_mode(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("IncludeMode"))["id"]
        _post(client, _observation(pid))
        bundle = client.get(f"/Patient/{pid}/$everything").json()
        obs_entry = next(
            e for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"
        )
        assert obs_entry["search"]["mode"] == "include"

    def test_only_this_patients_resources_returned(self, client: httpx.Client) -> None:
        pid1 = _post(client, _patient("Alice"))["id"]
        pid2 = _post(client, _patient("Bob"))["id"]
        _post(client, _observation(pid1))
        _post(client, _observation(pid2))
        bundle = client.get(f"/Patient/{pid1}/$everything").json()
        obs_entries = [e for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"]
        assert len(obs_entries) == 1
        assert f"Patient/{pid1}" in obs_entries[0]["resource"]["subject"]["reference"]

    def test_pagination_with_count(self, client: httpx.Client) -> None:
        pid = _post(client, _patient("Paged"))["id"]
        for _ in range(4):
            _post(client, _observation(pid))
        bundle = client.get(f"/Patient/{pid}/$everything?_count=2").json()
        assert len(bundle["entry"]) == 2
        assert bundle["total"] == 5
        assert any(link["relation"] == "next" for link in bundle["link"])


class TestGenericEverything:
    """Verify the route works for any resource type, not just Patient."""

    def test_anchor_returns_200_searchset(self, client: httpx.Client) -> None:
        oid = _post(client, _standalone_observation())["id"]
        r = client.get(f"/Observation/{oid}/$everything")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"

    def test_anchor_is_match_entry_with_total_1(self, client: httpx.Client) -> None:
        oid = _post(client, _standalone_observation())["id"]
        bundle = client.get(f"/Observation/{oid}/$everything").json()
        assert bundle["total"] == 1
        match_entry = next(e for e in bundle["entry"] if e["search"]["mode"] == "match")
        assert match_entry["resource"]["id"] == oid
        assert match_entry["resource"]["resourceType"] == "Observation"

    def test_unknown_resource_returns_404(self, client: httpx.Client) -> None:
        r = client.get("/Observation/no-such-obs-xyz/$everything")
        assert r.status_code == 404
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_deleted_resource_returns_410(self, client: httpx.Client) -> None:
        oid = _post(client, _standalone_observation())["id"]
        client.delete(f"/Observation/{oid}")
        assert client.get(f"/Observation/{oid}/$everything").status_code == 410

    def test_capability_advertises_everything_for_all_types(self, client: httpx.Client) -> None:
        meta = client.get("/metadata").json()
        for resource in meta["rest"][0]["resource"]:
            ops = [op["name"] for op in resource.get("operation", [])]
            assert "$everything" in ops, f"$everything missing from {resource['type']}"
