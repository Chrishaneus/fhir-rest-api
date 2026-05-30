"""Unit tests for _lastUpdated search parameter."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

_UTC = UTC


def _patient(family: str = "Lu") -> dict:
    return {"resourceType": "Patient", "name": [{"family": family}]}


def _observation(patient_id: str) -> dict:
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
        "subject": {"reference": f"Patient/{patient_id}"},
    }


# ---------------------------------------------------------------------------
# parse_date_param unit tests (pure function, no HTTP)
# ---------------------------------------------------------------------------


class TestParseDateParam:
    def test_bare_year(self) -> None:
        from app.utils.fhir.date_search import parse_date_param

        prefix, start, end = parse_date_param("2024")
        assert prefix == "eq"
        assert start == datetime(2024, 1, 1, tzinfo=_UTC)
        assert end == datetime(2024, 12, 31, 23, 59, 59, 999999, tzinfo=_UTC)

    def test_year_month(self) -> None:
        from app.utils.fhir.date_search import parse_date_param

        prefix, start, end = parse_date_param("2024-03")
        assert prefix == "eq"
        assert start == datetime(2024, 3, 1, tzinfo=_UTC)
        assert end == datetime(2024, 3, 31, 23, 59, 59, 999999, tzinfo=_UTC)

    def test_date_only(self) -> None:
        from app.utils.fhir.date_search import parse_date_param

        prefix, start, end = parse_date_param("2024-01-15")
        assert prefix == "eq"
        assert start == datetime(2024, 1, 15, tzinfo=_UTC)
        assert end == datetime(2024, 1, 15, 23, 59, 59, 999999, tzinfo=_UTC)

    def test_full_datetime(self) -> None:
        from app.utils.fhir.date_search import parse_date_param

        prefix, start, end = parse_date_param("2024-06-01T12:00:00Z")
        assert prefix == "eq"
        assert start == end
        assert start == datetime(2024, 6, 1, 12, 0, 0, tzinfo=_UTC)

    def test_ge_prefix(self) -> None:
        from app.utils.fhir.date_search import parse_date_param

        prefix, start, _ = parse_date_param("ge2024-01-01")
        assert prefix == "ge"
        assert start == datetime(2024, 1, 1, tzinfo=_UTC)

    def test_lt_prefix(self) -> None:
        from app.utils.fhir.date_search import parse_date_param

        prefix, start, _ = parse_date_param("lt2024-06-01")
        assert prefix == "lt"
        assert start == datetime(2024, 6, 1, tzinfo=_UTC)

    def test_invalid_raises(self) -> None:
        from app.utils.fhir.date_search import parse_date_param

        with pytest.raises(ValueError):
            parse_date_param("not-a-date")


# ---------------------------------------------------------------------------
# matches_last_updated unit tests (pure function, no HTTP)
# ---------------------------------------------------------------------------


class TestMatchesLastUpdated:
    def _ts(self, iso: str) -> datetime:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))

    def test_eq_within_day(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = self._ts("2024-05-10T14:30:00Z")
        assert matches_last_updated(ts, "eq2024-05-10") is True

    def test_eq_outside_day(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = self._ts("2024-05-11T00:00:00Z")
        assert matches_last_updated(ts, "eq2024-05-10") is False

    def test_ne_outside_day(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = self._ts("2024-05-11T00:00:00Z")
        assert matches_last_updated(ts, "ne2024-05-10") is True

    def test_gt_after_day_end(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = self._ts("2024-05-11T00:00:00Z")
        assert matches_last_updated(ts, "gt2024-05-10") is True

    def test_gt_same_day(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = self._ts("2024-05-10T14:30:00Z")
        assert matches_last_updated(ts, "gt2024-05-10") is False

    def test_ge_same_day(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = self._ts("2024-05-10T14:30:00Z")
        assert matches_last_updated(ts, "ge2024-05-10") is True

    def test_lt_before_day_start(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = self._ts("2024-05-09T23:59:59Z")
        assert matches_last_updated(ts, "lt2024-05-10") is True

    def test_lt_same_day(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = self._ts("2024-05-10T14:30:00Z")
        assert matches_last_updated(ts, "lt2024-05-10") is False

    def test_le_same_day(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = self._ts("2024-05-10T23:59:00Z")
        assert matches_last_updated(ts, "le2024-05-10") is True

    def test_sa_alias_gt(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = self._ts("2024-05-11T00:00:00Z")
        assert matches_last_updated(ts, "sa2024-05-10") is True

    def test_eb_alias_lt(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = self._ts("2024-05-09T23:59:59Z")
        assert matches_last_updated(ts, "eb2024-05-10") is True

    def test_naive_datetime_treated_as_utc(self) -> None:
        from app.utils.fhir.date_search import matches_last_updated

        ts = datetime(2024, 5, 10, 14, 30, 0)  # naive
        assert matches_last_updated(ts, "eq2024-05-10") is True


# ---------------------------------------------------------------------------
# HTTP search endpoint tests
# ---------------------------------------------------------------------------


class TestLastUpdatedSearch:
    def test_ge_returns_matching_resources(self, client: TestClient) -> None:
        client.post("/Patient", json=_patient("Old"))
        pid = client.post("/Patient", json=_patient("New")).json()["id"]
        new_ts = client.get(f"/Patient/{pid}").json()["meta"]["lastUpdated"]
        r = client.get(f"/Patient?_lastUpdated=ge{new_ts}")
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert pid in ids

    def test_lt_excludes_recent_resources(self, client: TestClient) -> None:
        r1 = client.post("/Patient", json=_patient("Early")).json()
        pid = r1["id"]
        ts = r1["meta"]["lastUpdated"]
        client.post("/Patient", json=_patient("Later"))
        r = client.get(f"/Patient?_lastUpdated=lt{ts}")
        ids = [e["resource"]["id"] for e in r.json().get("entry", [])]
        assert pid not in ids

    def test_eq_day_matches_resource_created_that_day(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient("SameDay")).json()["id"]
        ts = client.get(f"/Patient/{pid}").json()["meta"]["lastUpdated"]
        date_only = ts[:10]
        r = client.get(f"/Patient?_lastUpdated=eq{date_only}")
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert pid in ids

    def test_range_and_semantics(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient("Range")).json()["id"]
        ts = client.get(f"/Patient/{pid}").json()["meta"]["lastUpdated"]
        date_only = ts[:10]
        r = client.get(f"/Patient?_lastUpdated=ge{date_only}&_lastUpdated=le{date_only}")
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert pid in ids

    def test_future_ge_returns_no_results(self, client: TestClient) -> None:
        client.post("/Patient", json=_patient("Past"))
        r = client.get("/Patient?_lastUpdated=ge2099-01-01")
        assert r.json()["total"] == 0

    def test_past_lt_returns_no_results(self, client: TestClient) -> None:
        client.post("/Patient", json=_patient("Recent"))
        r = client.get("/Patient?_lastUpdated=lt2000-01-01")
        assert r.json()["total"] == 0

    def test_combined_with_other_param(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient("LuCombined")).json()["id"]
        ts = client.get(f"/Patient/{pid}").json()["meta"]["lastUpdated"]
        date_only = ts[:10]
        r = client.get(f"/Patient?name=LuCombined&_lastUpdated=ge{date_only}")
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert pid in ids

    def test_last_updated_on_system_search(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient("Sys")).json()["id"]
        ts = client.get(f"/Patient/{pid}").json()["meta"]["lastUpdated"]
        date_only = ts[:10]
        r = client.get(f"/?_lastUpdated=ge{date_only}")
        assert r.status_code == 200
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert pid in ids

    def test_id_and_last_updated_combined(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient("IdLu")).json()["id"]
        r = client.get(f"/Patient?_id={pid}&_lastUpdated=ge2099-01-01")
        assert r.json()["total"] == 0

    def test_id_and_last_updated_match(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=_patient("IdLuMatch")).json()["id"]
        ts = client.get(f"/Patient/{pid}").json()["meta"]["lastUpdated"]
        date_only = ts[:10]
        r = client.get(f"/Patient?_id={pid}&_lastUpdated=ge{date_only}")
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert pid in ids
