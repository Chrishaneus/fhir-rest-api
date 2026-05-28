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


def _patient(family: str = "Lu") -> dict:
    return {"resourceType": "Patient", "name": [{"family": family}]}


class TestLastUpdatedSearch:
    def test_ge_includes_resource(self, client: httpx.Client) -> None:
        created = _post(client, _patient("GeTest"))
        ts = created["meta"]["lastUpdated"]
        date_only = ts[:10]
        r = client.get(f"/Patient?_lastUpdated=ge{date_only}")
        assert r.status_code == 200
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert created["id"] in ids

    def test_future_ge_returns_empty(self, client: httpx.Client) -> None:
        _post(client, _patient("FutureGe"))
        r = client.get("/Patient?_lastUpdated=ge2099-01-01")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_past_lt_returns_empty(self, client: httpx.Client) -> None:
        _post(client, _patient("PastLt"))
        r = client.get("/Patient?_lastUpdated=lt2000-01-01")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_eq_day_matches(self, client: httpx.Client) -> None:
        created = _post(client, _patient("EqDay"))
        ts = created["meta"]["lastUpdated"]
        date_only = ts[:10]
        r = client.get(f"/Patient?_lastUpdated=eq{date_only}")
        assert r.status_code == 200
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert created["id"] in ids

    def test_range_and_semantics(self, client: httpx.Client) -> None:
        created = _post(client, _patient("Range"))
        ts = created["meta"]["lastUpdated"]
        date_only = ts[:10]
        r = client.get(f"/Patient?_lastUpdated=ge{date_only}&_lastUpdated=le{date_only}")
        assert r.status_code == 200
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert created["id"] in ids

    def test_combined_with_name_param(self, client: httpx.Client) -> None:
        created = _post(client, _patient("CombinedParam"))
        ts = created["meta"]["lastUpdated"]
        date_only = ts[:10]
        r = client.get(f"/Patient?name=CombinedParam&_lastUpdated=ge{date_only}")
        assert r.status_code == 200
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert created["id"] in ids

    def test_system_search_respects_last_updated(self, client: httpx.Client) -> None:
        created = _post(client, _patient("SystemLu"))
        ts = created["meta"]["lastUpdated"]
        date_only = ts[:10]
        r = client.get(f"/?_lastUpdated=ge{date_only}")
        assert r.status_code == 200
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert created["id"] in ids

    def test_ne_excludes_resource_created_today(self, client: httpx.Client) -> None:
        created = _post(client, _patient("NeToday"))
        ts = created["meta"]["lastUpdated"]
        date_only = ts[:10]
        r = client.get(f"/Patient?_lastUpdated=ne{date_only}")
        assert r.status_code == 200
        ids = [e["resource"]["id"] for e in r.json().get("entry", [])]
        assert created["id"] not in ids
