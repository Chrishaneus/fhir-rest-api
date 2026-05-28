from __future__ import annotations

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration


def _post(
    client: httpx.Client,
    resource: dict,
    if_none_exist: str | None = None,
) -> httpx.Response:
    headers: dict[str, str] = {"Content-Type": FHIR_JSON}
    if if_none_exist is not None:
        headers["If-None-Exist"] = if_none_exist
    return client.post(f"/{resource['resourceType']}", headers=headers, json=resource)


def _patient(family: str = "Cond", identifier: str | None = None) -> dict:
    p: dict = {"resourceType": "Patient", "name": [{"family": family}]}
    if identifier:
        p["identifier"] = [{"system": "http://example.org/ids", "value": identifier}]
    return p


class TestConditionalCreate:
    def test_no_match_creates_resource(self, client: httpx.Client) -> None:
        r = _post(client, _patient(identifier="integ-cc-001"), "identifier=integ-cc-001")
        assert r.status_code == 201
        assert r.json()["resourceType"] == "Patient"

    def test_no_match_resource_is_retrievable(self, client: httpx.Client) -> None:
        pid = _post(client, _patient(identifier="integ-cc-002"), "identifier=integ-cc-002").json()["id"]
        assert client.get(f"/Patient/{pid}").status_code == 200

    def test_one_match_returns_200(self, client: httpx.Client) -> None:
        _post(client, _patient(identifier="integ-cc-003"))
        r = _post(client, _patient(identifier="integ-cc-003"), "identifier=integ-cc-003")
        assert r.status_code == 200

    def test_one_match_returns_existing_resource(self, client: httpx.Client) -> None:
        existing_id = _post(client, _patient("Original", "integ-cc-004")).json()["id"]
        r = _post(client, _patient("Different", "integ-cc-004"), "identifier=integ-cc-004")
        assert r.json()["id"] == existing_id

    def test_one_match_does_not_create_duplicate(self, client: httpx.Client) -> None:
        _post(client, _patient(identifier="integ-cc-005"))
        _post(client, _patient(identifier="integ-cc-005"), "identifier=integ-cc-005")
        total = client.get("/Patient?identifier=integ-cc-005").json()["total"]
        assert total == 1

    def test_one_match_response_has_etag(self, client: httpx.Client) -> None:
        _post(client, _patient(identifier="integ-cc-006"))
        r = _post(client, _patient(identifier="integ-cc-006"), "identifier=integ-cc-006")
        assert "etag" in r.headers

    def test_multiple_matches_returns_412(self, client: httpx.Client) -> None:
        _post(client, _patient("MultiA"))
        _post(client, _patient("MultiA"))
        r = _post(client, _patient("MultiA"), "name=MultiA")
        assert r.status_code == 412
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_multiple_matches_does_not_create(self, client: httpx.Client) -> None:
        _post(client, _patient(identifier="integ-cc-multi"))
        _post(client, _patient(identifier="integ-cc-multi"))
        _post(client, _patient(identifier="integ-cc-multi"), "identifier=integ-cc-multi")
        total = client.get("/Patient?identifier=integ-cc-multi").json()["total"]
        assert total == 2

    def test_empty_header_returns_400(self, client: httpx.Client) -> None:
        r = _post(client, _patient(), "")
        assert r.status_code == 400
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_capability_advertises_conditional_create(self, client: httpx.Client) -> None:
        meta = client.get("/metadata").json()
        for resource in meta["rest"][0]["resource"]:
            assert resource["conditionalCreate"] is True
