"""Integration tests for _include and _revinclude query parameters."""

from __future__ import annotations

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

FHIR_JSON = "application/fhir+json"


def _unique(prefix: str = "incl") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def patient(client: httpx.Client) -> dict:
    r = client.post(
        "/Patient",
        headers={"Content-Type": FHIR_JSON},
        json={"resourceType": "Patient", "name": [{"family": _unique("IncludePatient")}]},
    )
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def observation(client: httpx.Client, patient: dict) -> dict:
    r = client.post(
        "/Observation",
        headers={"Content-Type": FHIR_JSON},
        json={
            "resourceType": "Observation",
            "status": "final",
            "code": {"text": "Heart rate"},
            "subject": {"reference": f"Patient/{patient['id']}"},
        },
    )
    assert r.status_code == 201
    return r.json()


class TestInclude:
    def test_include_fetches_referenced_patient(
        self, client: httpx.Client, patient: dict, observation: dict
    ) -> None:
        r = client.get(f"/Observation?_id={observation['id']}&_include=Observation:subject")
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "searchset"

        match_entries = [e for e in body["entry"] if e.get("search", {}).get("mode") == "match"]
        include_entries = [e for e in body["entry"] if e.get("search", {}).get("mode") == "include"]

        assert len(match_entries) == 1
        assert match_entries[0]["resource"]["resourceType"] == "Observation"
        assert match_entries[0]["resource"]["id"] == observation["id"]

        assert len(include_entries) == 1
        assert include_entries[0]["resource"]["resourceType"] == "Patient"
        assert include_entries[0]["resource"]["id"] == patient["id"]

    def test_include_with_target_type_filter(
        self, client: httpx.Client, patient: dict, observation: dict
    ) -> None:
        r = client.get(f"/Observation?_id={observation['id']}&_include=Observation:subject:Patient")
        assert r.status_code == 200
        include_entries = [
            e for e in r.json()["entry"] if e.get("search", {}).get("mode") == "include"
        ]
        assert len(include_entries) == 1
        assert include_entries[0]["resource"]["id"] == patient["id"]

    def test_include_wrong_target_type_returns_no_includes(
        self, client: httpx.Client, observation: dict
    ) -> None:
        r = client.get(
            f"/Observation?_id={observation['id']}&_include=Observation:subject:Encounter"
        )
        assert r.status_code == 200
        include_entries = [
            e for e in r.json()["entry"] if e.get("search", {}).get("mode") == "include"
        ]
        assert len(include_entries) == 0

    def test_include_deduplicates_repeated_references(
        self, client: httpx.Client, patient: dict
    ) -> None:
        # Two observations referencing the same patient
        for _ in range(2):
            r = client.post(
                "/Observation",
                headers={"Content-Type": FHIR_JSON},
                json={
                    "resourceType": "Observation",
                    "status": "final",
                    "code": {"text": "HR"},
                    "subject": {"reference": f"Patient/{patient['id']}"},
                },
            )
            assert r.status_code == 201

        r = client.get(f"/Patient?_id={patient['id']}&_revinclude=Observation:subject")
        include_entries = [
            e for e in r.json()["entry"] if e.get("search", {}).get("mode") == "include"
        ]
        # Each included Patient should appear only once
        included_patient_ids = [
            e["resource"]["id"]
            for e in r.json()["entry"]
            if e.get("search", {}).get("mode") == "match"
        ]
        assert included_patient_ids.count(patient["id"]) == 1
        # Both observations should be included
        assert len(include_entries) >= 2


class TestRevInclude:
    def test_revinclude_appends_referencing_observations(
        self, client: httpx.Client, patient: dict, observation: dict
    ) -> None:
        r = client.get(f"/Patient?_id={patient['id']}&_revinclude=Observation:subject")
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "searchset"

        match_entries = [e for e in body["entry"] if e.get("search", {}).get("mode") == "match"]
        include_entries = [e for e in body["entry"] if e.get("search", {}).get("mode") == "include"]

        assert len(match_entries) == 1
        assert match_entries[0]["resource"]["resourceType"] == "Patient"

        obs_ids = [e["resource"]["id"] for e in include_entries]
        assert observation["id"] in obs_ids

    def test_revinclude_with_source_type_filter(
        self, client: httpx.Client, patient: dict, observation: dict
    ) -> None:
        r = client.get(f"/Patient?_id={patient['id']}&_revinclude=Observation:subject:Patient")
        assert r.status_code == 200
        include_entries = [
            e for e in r.json()["entry"] if e.get("search", {}).get("mode") == "include"
        ]
        obs_ids = [e["resource"]["id"] for e in include_entries]
        assert observation["id"] in obs_ids

    def test_revinclude_no_match_returns_no_includes(
        self, client: httpx.Client, patient: dict
    ) -> None:
        # Patient exists but no Observations reference it
        r = client.get(f"/Patient?_id={patient['id']}&_revinclude=Observation:subject")
        assert r.status_code == 200
        include_entries = [
            e for e in r.json()["entry"] if e.get("search", {}).get("mode") == "include"
        ]
        assert len(include_entries) == 0


class TestIncludeIterate:
    def test_include_iterate_follows_two_level_chain(self, client: httpx.Client) -> None:
        """_include:iterate fetches Observation → Patient → Organization."""
        org = client.post(
            "/Organization",
            headers={"Content-Type": FHIR_JSON},
            json={"resourceType": "Organization", "name": _unique("Org")},
        )
        assert org.status_code == 201
        org_id = org.json()["id"]

        pat = client.post(
            "/Patient",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "Patient",
                "name": [{"family": _unique("IterPat")}],
                "managingOrganization": {"reference": f"Organization/{org_id}"},
            },
        )
        assert pat.status_code == 201
        pat_id = pat.json()["id"]

        obs = client.post(
            "/Observation",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "Observation",
                "status": "final",
                "code": {"text": "HR"},
                "subject": {"reference": f"Patient/{pat_id}"},
            },
        )
        assert obs.status_code == 201
        obs_id = obs.json()["id"]

        r = client.get(
            f"/Observation?_id={obs_id}"
            "&_include:iterate=Observation:subject"
            "&_include:iterate=Patient:managingOrganization"
        )
        assert r.status_code == 200
        entries = r.json()["entry"]
        included = [e for e in entries if e.get("search", {}).get("mode") == "include"]
        included_ids = {e["resource"]["id"] for e in included}
        assert pat_id in included_ids, "Patient not included at level 1"
        assert org_id in included_ids, "Organization not included at level 2"

    def test_include_iterate_stops_when_no_further_references(
        self, client: httpx.Client, patient: dict, observation: dict
    ) -> None:
        """_include:iterate with a one-level chain returns exactly one included resource."""
        r = client.get(f"/Observation?_id={observation['id']}&_include:iterate=Observation:subject")
        assert r.status_code == 200
        included = [e for e in r.json()["entry"] if e.get("search", {}).get("mode") == "include"]
        assert len(included) == 1
        assert included[0]["resource"]["id"] == patient["id"]


class TestRevIncludeIterate:
    def test_revinclude_iterate_follows_two_level_chain(self, client: httpx.Client) -> None:
        """_revinclude:iterate fetches Patient ← Encounter ← DiagnosticReport."""
        pat = client.post(
            "/Patient",
            headers={"Content-Type": FHIR_JSON},
            json={"resourceType": "Patient", "name": [{"family": _unique("RevIterPat")}]},
        )
        assert pat.status_code == 201
        pat_id = pat.json()["id"]

        enc = client.post(
            "/Encounter",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "Encounter",
                "status": "finished",
                "subject": {"reference": f"Patient/{pat_id}"},
            },
        )
        assert enc.status_code == 201
        enc_id = enc.json()["id"]

        dr = client.post(
            "/DiagnosticReport",
            headers={"Content-Type": FHIR_JSON},
            json={
                "resourceType": "DiagnosticReport",
                "status": "final",
                "code": {"text": "Labs"},
                "encounter": {"reference": f"Encounter/{enc_id}"},
            },
        )
        assert dr.status_code == 201
        dr_id = dr.json()["id"]

        r = client.get(
            f"/Patient?_id={pat_id}"
            "&_revinclude:iterate=Encounter:subject"
            "&_revinclude:iterate=DiagnosticReport:encounter"
        )
        assert r.status_code == 200
        entries = r.json()["entry"]
        included = [e for e in entries if e.get("search", {}).get("mode") == "include"]
        included_ids = {e["resource"]["id"] for e in included}
        assert enc_id in included_ids, "Encounter not included at level 1"
        assert dr_id in included_ids, "DiagnosticReport not included at level 2"

    def test_revinclude_iterate_stops_when_no_further_back_references(
        self, client: httpx.Client, patient: dict, observation: dict
    ) -> None:
        """_revinclude:iterate with a one-level chain returns exactly the direct referrers."""
        r = client.get(f"/Patient?_id={patient['id']}&_revinclude:iterate=Observation:subject")
        assert r.status_code == 200
        included = [e for e in r.json()["entry"] if e.get("search", {}).get("mode") == "include"]
        obs_ids = {e["resource"]["id"] for e in included}
        assert observation["id"] in obs_ids


class TestSearchMode:
    def test_plain_search_entries_have_match_mode(
        self, client: httpx.Client, patient: dict
    ) -> None:
        family = patient["name"][0]["family"]
        r = client.get(f"/Patient?family={family}")
        assert r.status_code == 200
        for entry in r.json().get("entry", []):
            assert entry.get("search", {}).get("mode") == "match"

    def test_search_without_include_has_no_include_mode_entries(
        self, client: httpx.Client, patient: dict
    ) -> None:
        r = client.get(f"/Patient?_id={patient['id']}")
        assert r.status_code == 200
        include_entries = [
            e for e in r.json()["entry"] if e.get("search", {}).get("mode") == "include"
        ]
        assert len(include_entries) == 0
