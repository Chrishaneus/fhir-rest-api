"""Integration tests for `_summary` and `_elements` response shaping.

FHIR R5 §3.3.1.1 (_summary) and §3.3.1.2 (_elements).
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration

_SUBSETTED_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ObservationValue"


def _patient(suffix: str | None = None) -> dict:
    family = f"ShapeTest-{suffix or uuid.uuid4().hex[:8]}"
    return {
        "resourceType": "Patient",
        "name": [{"family": family, "given": ["Alice"]}],
        "birthDate": "1990-01-01",
        "text": {"status": "generated", "div": "<div>narrative</div>"},
    }


def _create(client: httpx.Client) -> dict:
    r = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient())
    assert r.status_code == 201, r.text
    return r.json()


def _has_subsetted_tag(resource: dict) -> bool:
    tags = (resource.get("meta") or {}).get("tag") or []
    return any(t.get("code") == "SUBSETTED" for t in tags)


class TestSummaryTrue:
    def test_read_returns_only_mandatory_and_text(self, client: httpx.Client) -> None:
        created = _create(client)
        rid = created["id"]
        r = client.get(f"/Patient/{rid}?_summary=true")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) <= {"id", "meta", "resourceType", "text"}
        assert "name" not in body
        assert "birthDate" not in body

    def test_read_adds_subsetted_tag(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient/{created['id']}?_summary=true")
        assert _has_subsetted_tag(r.json())

    def test_search_shapes_entry_resources(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient?_id={created['id']}&_summary=true")
        assert r.status_code == 200
        entries = r.json().get("entry", [])
        assert entries, "expected at least one entry"
        resource = entries[0]["resource"]
        assert set(resource.keys()) <= {"id", "meta", "resourceType", "text"}
        assert _has_subsetted_tag(resource)


class TestSummaryText:
    def test_read_returns_text_and_mandatory(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient/{created['id']}?_summary=text")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) <= {"id", "meta", "resourceType", "text"}
        assert "name" not in body

    def test_read_adds_subsetted_tag(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient/{created['id']}?_summary=text")
        assert _has_subsetted_tag(r.json())


class TestSummaryData:
    def test_read_removes_text_keeps_rest(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient/{created['id']}?_summary=data")
        assert r.status_code == 200
        body = r.json()
        assert "text" not in body
        assert "name" in body
        assert "birthDate" in body

    def test_read_adds_subsetted_tag(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient/{created['id']}?_summary=data")
        assert _has_subsetted_tag(r.json())

    def test_search_removes_text_from_entries(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient?_id={created['id']}&_summary=data")
        assert r.status_code == 200
        entries = r.json().get("entry", [])
        assert entries
        assert "text" not in entries[0]["resource"]


class TestSummaryCount:
    def test_search_returns_no_entries(self, client: httpx.Client) -> None:
        _create(client)
        r = client.get("/Patient?_summary=count")
        assert r.status_code == 200
        body = r.json()
        assert body["resourceType"] == "Bundle"
        assert "entry" not in body

    def test_search_still_has_total(self, client: httpx.Client) -> None:
        _create(client)
        r = client.get("/Patient?_summary=count")
        body = r.json()
        assert "total" in body
        assert body["total"] >= 1


class TestSummaryFalse:
    def test_read_returns_full_resource(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient/{created['id']}?_summary=false")
        assert r.status_code == 200
        body = r.json()
        assert "name" in body
        assert "birthDate" in body
        assert not _has_subsetted_tag(body)


class TestElements:
    def test_read_returns_only_requested_plus_mandatory(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient/{created['id']}?_elements=name,birthDate")
        assert r.status_code == 200
        body = r.json()
        assert "name" in body
        assert "birthDate" in body
        assert "id" in body
        assert "resourceType" in body
        assert "meta" in body
        assert "text" not in body

    def test_read_adds_subsetted_tag(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient/{created['id']}?_elements=name")
        assert _has_subsetted_tag(r.json())

    def test_search_shapes_entry_resources(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient?_id={created['id']}&_elements=name")
        assert r.status_code == 200
        entries = r.json().get("entry", [])
        assert entries
        resource = entries[0]["resource"]
        assert "name" in resource
        assert "birthDate" not in resource
        assert _has_subsetted_tag(resource)

    def test_comma_separated_values(self, client: httpx.Client) -> None:
        created = _create(client)
        r = client.get(f"/Patient/{created['id']}?_elements=name,birthDate,gender")
        body = r.json()
        assert "name" in body
        assert "birthDate" in body
