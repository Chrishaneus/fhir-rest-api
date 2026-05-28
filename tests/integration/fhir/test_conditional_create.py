from __future__ import annotations

import uuid

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration


def _uid() -> str:
    return uuid.uuid4().hex[:12]


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
        uid = _uid()
        r = _post(client, _patient(identifier=uid), f"identifier={uid}")
        assert r.status_code == 201
        assert r.json()["resourceType"] == "Patient"

    def test_no_match_resource_is_retrievable(self, client: httpx.Client) -> None:
        uid = _uid()
        pid = _post(client, _patient(identifier=uid), f"identifier={uid}").json()["id"]
        assert client.get(f"/Patient/{pid}").status_code == 200

    def test_one_match_returns_200(self, client: httpx.Client) -> None:
        uid = _uid()
        _post(client, _patient(identifier=uid))
        r = _post(client, _patient(identifier=uid), f"identifier={uid}")
        assert r.status_code == 200

    def test_one_match_returns_existing_resource(self, client: httpx.Client) -> None:
        uid = _uid()
        existing_id = _post(client, _patient("Original", uid)).json()["id"]
        r = _post(client, _patient("Different", uid), f"identifier={uid}")
        assert r.json()["id"] == existing_id

    def test_one_match_does_not_create_duplicate(self, client: httpx.Client) -> None:
        uid = _uid()
        _post(client, _patient(identifier=uid))
        _post(client, _patient(identifier=uid), f"identifier={uid}")
        total = client.get(f"/Patient?identifier={uid}").json()["total"]
        assert total == 1

    def test_one_match_response_has_etag(self, client: httpx.Client) -> None:
        uid = _uid()
        _post(client, _patient(identifier=uid))
        r = _post(client, _patient(identifier=uid), f"identifier={uid}")
        assert "etag" in r.headers

    def test_multiple_matches_returns_412(self, client: httpx.Client) -> None:
        family = "MultiA-" + _uid()
        _post(client, _patient(family))
        _post(client, _patient(family))
        r = _post(client, _patient(family), f"name={family}")
        assert r.status_code == 412
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_multiple_matches_does_not_create(self, client: httpx.Client) -> None:
        uid = _uid()
        _post(client, _patient(identifier=uid))
        _post(client, _patient(identifier=uid))
        _post(client, _patient(identifier=uid), f"identifier={uid}")
        total = client.get(f"/Patient?identifier={uid}").json()["total"]
        assert total == 2

    def test_empty_header_returns_400(self, client: httpx.Client) -> None:
        r = _post(client, _patient(), "")
        assert r.status_code == 400
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_capability_advertises_conditional_create(self, client: httpx.Client) -> None:
        meta = client.get("/metadata").json()
        for resource in meta["rest"][0]["resource"]:
            assert resource["conditionalCreate"] is True
