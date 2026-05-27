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
        r = client.get(
            f"/Observation?_id={observation['id']}&_include=Observation:subject"
        )
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
        r = client.get(
            f"/Observation?_id={observation['id']}&_include=Observation:subject:Patient"
        )
        assert r.status_code == 200
        include_entries = [
            e for e in r.json()["entry"]
            if e.get("search", {}).get("mode") == "include"
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
            e for e in r.json()["entry"]
            if e.get("search", {}).get("mode") == "include"
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

        r = client.get(
            f"/Patient?_id={patient['id']}&_revinclude=Observation:subject"
        )
        include_entries = [
            e for e in r.json()["entry"]
            if e.get("search", {}).get("mode") == "include"
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
        r = client.get(
            f"/Patient?_id={patient['id']}&_revinclude=Observation:subject"
        )
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
        r = client.get(
            f"/Patient?_id={patient['id']}&_revinclude=Observation:subject:Patient"
        )
        assert r.status_code == 200
        include_entries = [
            e for e in r.json()["entry"]
            if e.get("search", {}).get("mode") == "include"
        ]
        obs_ids = [e["resource"]["id"] for e in include_entries]
        assert observation["id"] in obs_ids

    def test_revinclude_no_match_returns_no_includes(
        self, client: httpx.Client, patient: dict
    ) -> None:
        # Patient exists but no Observations reference it
        r = client.get(
            f"/Patient?_id={patient['id']}&_revinclude=Observation:subject"
        )
        assert r.status_code == 200
        include_entries = [
            e for e in r.json()["entry"]
            if e.get("search", {}).get("mode") == "include"
        ]
        assert len(include_entries) == 0


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
            e for e in r.json()["entry"]
            if e.get("search", {}).get("mode") == "include"
        ]
        assert len(include_entries) == 0
