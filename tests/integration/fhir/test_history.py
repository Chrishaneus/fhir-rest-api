"""Integration tests for history bundle pagination."""

from __future__ import annotations

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration


def _post(client: httpx.Client, resource: dict) -> dict:
    resource_type = resource["resourceType"]
    r = client.post(f"/{resource_type}", headers={"Content-Type": FHIR_JSON}, json=resource)
    assert r.status_code == 201
    return r.json()


def _patient(family: str = "HistPag") -> dict:
    return {"resourceType": "Patient", "name": [{"family": family}]}


def _make_versions(client: httpx.Client, count: int) -> dict:
    """Create a patient then PUT it `count - 1` more times to build `count` versions."""
    body = _post(client, _patient())
    for i in range(count - 1):
        r = client.put(
            f"/Patient/{body['id']}",
            headers={"Content-Type": FHIR_JSON},
            json={**body, "active": bool(i % 2)},
        )
        assert r.status_code == 200
    return body


class TestInstanceHistoryPagination:
    def test_count_limits_returned_entries(self, client: httpx.Client) -> None:
        body = _make_versions(client, 4)
        r = client.get(f"/Patient/{body['id']}/_history?_count=2")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["total"] == 4
        assert len(bundle["entry"]) == 2

    def test_next_link_present_when_more_pages(self, client: httpx.Client) -> None:
        body = _make_versions(client, 4)
        bundle = client.get(f"/Patient/{body['id']}/_history?_count=2").json()
        assert any(link["relation"] == "next" for link in bundle["link"])

    def test_offset_skips_earlier_entries(self, client: httpx.Client) -> None:
        body = _make_versions(client, 4)
        pid = body["id"]
        all_versions = [
            e["resource"]["meta"]["versionId"]
            for e in client.get(f"/Patient/{pid}/_history").json()["entry"]
            if e.get("resource")
        ]
        page = client.get(f"/Patient/{pid}/_history?_count=2&_offset=2").json()
        assert page["total"] == 4
        assert len(page["entry"]) == 2
        page_versions = [
            e["resource"]["meta"]["versionId"] for e in page["entry"] if e.get("resource")
        ]
        assert page_versions == all_versions[2:4]

    def test_total_unchanged_regardless_of_count(self, client: httpx.Client) -> None:
        body = _make_versions(client, 3)
        pid = body["id"]
        assert client.get(f"/Patient/{pid}/_history?_count=1").json()["total"] == 3
        assert client.get(f"/Patient/{pid}/_history?_count=100").json()["total"] == 3

    def test_no_next_link_on_last_page(self, client: httpx.Client) -> None:
        body = _make_versions(client, 2)
        pid = body["id"]
        bundle = client.get(f"/Patient/{pid}/_history?_count=10").json()
        assert not any(link["relation"] == "next" for link in bundle["link"])


class TestTypeHistoryPagination:
    def test_count_limits_entries(self, client: httpx.Client) -> None:
        for i in range(3):
            _post(client, _patient(f"TypePage{i}"))
        r = client.get("/Patient/_history?_count=2")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["total"] >= 3
        assert len(bundle["entry"]) == 2

    def test_next_link_present(self, client: httpx.Client) -> None:
        for i in range(3):
            _post(client, _patient(f"TypeNext{i}"))
        bundle = client.get("/Patient/_history?_count=1").json()
        assert any(link["relation"] == "next" for link in bundle["link"])


class TestSystemHistoryPagination:
    def test_count_limits_entries(self, client: httpx.Client) -> None:
        for i in range(3):
            _post(client, _patient(f"SysPage{i}"))
        r = client.get("/_history?_count=2")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["total"] >= 3
        assert len(bundle["entry"]) == 2

    def test_next_link_present(self, client: httpx.Client) -> None:
        for i in range(3):
            _post(client, _patient(f"SysNext{i}"))
        bundle = client.get("/_history?_count=1").json()
        assert any(link["relation"] == "next" for link in bundle["link"])
