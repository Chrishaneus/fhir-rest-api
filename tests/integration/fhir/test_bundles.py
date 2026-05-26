from __future__ import annotations

import uuid

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration


class TestSearchBundle:
    def test_search_returns_well_formed_bundle(self, client: httpx.Client) -> None:
        r = client.get("/Patient")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"
        assert "total" in bundle
        assert "timestamp" in bundle
        assert any(link["relation"] == "self" for link in bundle["link"])

    def test_pagination_next_link_present_when_results_exceed_count(
        self, client: httpx.Client
    ) -> None:
        for _ in range(2):
            client.post(
                "/Patient",
                headers={"Content-Type": FHIR_JSON},
                json={"resourceType": "Patient", "name": [{"family": f"Page-{uuid.uuid4().hex[:8]}"}]},
            )
        r = client.get("/Patient?_count=1")
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["total"] >= 2
        assert any(link["relation"] == "next" for link in bundle["link"])


class TestHistoryBundle:
    def test_system_history_returns_history_bundle(self, client: httpx.Client) -> None:
        r = client.get("/_history")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "history"
        assert any(link["relation"] == "self" for link in bundle["link"])

    def test_resource_history_returns_history_bundle(
        self, client: httpx.Client, created_patient: dict
    ) -> None:
        r = client.get(f"/Patient/{created_patient['id']}/_history")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["type"] == "history"
        assert bundle["total"] >= 1
