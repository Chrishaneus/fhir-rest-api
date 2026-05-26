from __future__ import annotations

import uuid

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration


class TestResponseHeaders:
    def test_create_response_includes_location_etag_last_modified(
        self, client: httpx.Client
    ) -> None:
        r = client.post(
            "/Patient",
            headers={"Content-Type": FHIR_JSON},
            json={"resourceType": "Patient", "name": [{"family": f"Hdr-{uuid.uuid4().hex[:8]}"}]},
        )
        assert r.status_code == 201
        assert r.headers["etag"] == 'W/"1"'
        assert "last-modified" in r.headers
        assert "/_history/1" in r.headers["location"]

    def test_read_response_includes_etag_and_last_modified(
        self, client: httpx.Client, created_patient: dict
    ) -> None:
        r = client.get(f"/Patient/{created_patient['id']}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith(FHIR_JSON)
        assert "etag" in r.headers
        assert "last-modified" in r.headers

    def test_error_responses_are_fhir_json(self, client: httpx.Client) -> None:
        for r in [
            client.get("/Patient/does-not-exist"),
            client.get("/NotAType/some-id"),
            client.post("/Patient", json={"resourceType": "Patient", "bad": 1}, headers={"Content-Type": FHIR_JSON}),
        ]:
            assert r.headers["content-type"].startswith(FHIR_JSON)


class TestConditionalRead:
    def test_if_none_match_current_etag_returns_304(
        self, client: httpx.Client, created_patient: dict
    ) -> None:
        r = client.get(
            f"/Patient/{created_patient['id']}",
            headers={"If-None-Match": 'W/"1"'},
        )
        assert r.status_code == 304
        assert r.content == b""

    def test_if_none_match_stale_etag_returns_200(
        self, client: httpx.Client, created_patient: dict
    ) -> None:
        r = client.get(
            f"/Patient/{created_patient['id']}",
            headers={"If-None-Match": 'W/"99"'},
        )
        assert r.status_code == 200
        assert r.json()["id"] == created_patient["id"]

    def test_if_modified_since_future_date_returns_304(
        self, client: httpx.Client, created_patient: dict
    ) -> None:
        r = client.get(
            f"/Patient/{created_patient['id']}",
            headers={"If-Modified-Since": "Thu, 01 Jan 2099 00:00:00 GMT"},
        )
        assert r.status_code == 304
        assert r.content == b""


class TestPreferHeader:
    def test_return_minimal_gives_empty_body(self, client: httpx.Client) -> None:
        r = client.post(
            "/Patient",
            headers={"Content-Type": FHIR_JSON, "Prefer": "return=minimal"},
            json={"resourceType": "Patient", "name": [{"family": f"Min-{uuid.uuid4().hex[:8]}"}]},
        )
        assert r.status_code == 201
        assert r.content == b""
        assert "etag" in r.headers
        assert "location" in r.headers

    def test_return_operation_outcome_on_create(self, client: httpx.Client) -> None:
        r = client.post(
            "/Patient",
            headers={"Content-Type": FHIR_JSON, "Prefer": "return=OperationOutcome"},
            json={"resourceType": "Patient", "name": [{"family": f"Oo-{uuid.uuid4().hex[:8]}"}]},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["severity"] == "information"
