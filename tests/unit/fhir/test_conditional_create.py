"""Unit tests for conditional create (If-None-Exist header)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import Response


def _patient(family: str = "Cond", identifier: str | None = None) -> dict:
    p: dict = {"resourceType": "Patient", "name": [{"family": family}]}
    if identifier:
        p["identifier"] = [{"system": "http://example.org/ids", "value": identifier}]
    return p


def _post(client: TestClient, resource: dict, if_none_exist: str | None = None) -> Response:
    headers = {}
    if if_none_exist is not None:
        headers["If-None-Exist"] = if_none_exist
    return client.post(f"/{resource['resourceType']}", json=resource, headers=headers)


# ---------------------------------------------------------------------------
# No header — normal create behaviour unchanged
# ---------------------------------------------------------------------------


class TestNoHeader:
    def test_creates_and_returns_201(self, client: TestClient) -> None:
        r = _post(client, _patient())
        assert r.status_code == 201

    def test_response_body_is_patient(self, client: TestClient) -> None:
        r = _post(client, _patient("Normal"))
        assert r.json()["resourceType"] == "Patient"
        assert r.json()["name"][0]["family"] == "Normal"


# ---------------------------------------------------------------------------
# 0 matches — create proceeds normally
# ---------------------------------------------------------------------------


class TestZeroMatches:
    def test_creates_when_no_match(self, client: TestClient) -> None:
        r = _post(client, _patient(identifier="unique-001"), "identifier=unique-001")
        assert r.status_code == 201

    def test_created_resource_is_retrievable(self, client: TestClient) -> None:
        r = _post(client, _patient(identifier="unique-002"), "identifier=unique-002")
        pid = r.json()["id"]
        assert client.get(f"/Patient/{pid}").status_code == 200

    def test_location_header_present(self, client: TestClient) -> None:
        r = _post(client, _patient(identifier="unique-003"), "identifier=unique-003")
        assert "location" in r.headers


# ---------------------------------------------------------------------------
# 1 match — return existing, no new resource created
# ---------------------------------------------------------------------------


class TestOneMatch:
    def test_returns_200_when_one_match(self, client: TestClient) -> None:
        client.post("/Patient", json=_patient(identifier="dup-001"))
        r = _post(client, _patient(identifier="dup-001"), "identifier=dup-001")
        assert r.status_code == 200

    def test_returns_existing_resource(self, client: TestClient) -> None:
        existing_id = client.post("/Patient", json=_patient("Original", "dup-002")).json()["id"]
        r = _post(client, _patient("Different", "dup-002"), "identifier=dup-002")
        assert r.json()["id"] == existing_id

    def test_does_not_create_duplicate(self, client: TestClient) -> None:
        client.post("/Patient", json=_patient(identifier="dup-003"))
        _post(client, _patient(identifier="dup-003"), "identifier=dup-003")
        total = client.get("/Patient?identifier=dup-003").json()["total"]
        assert total == 1

    def test_etag_header_present(self, client: TestClient) -> None:
        client.post("/Patient", json=_patient(identifier="dup-004"))
        r = _post(client, _patient(identifier="dup-004"), "identifier=dup-004")
        assert "etag" in r.headers


# ---------------------------------------------------------------------------
# 2+ matches — 412
# ---------------------------------------------------------------------------


class TestMultipleMatches:
    def test_returns_412_when_multiple_matches(self, client: TestClient) -> None:
        client.post("/Patient", json=_patient("Alice"))
        client.post("/Patient", json=_patient("Alice"))
        r = _post(client, _patient("Alice"), "name=Alice")
        assert r.status_code == 412

    def test_412_response_is_operation_outcome(self, client: TestClient) -> None:
        client.post("/Patient", json=_patient("Bob"))
        client.post("/Patient", json=_patient("Bob"))
        r = _post(client, _patient("Bob"), "name=Bob")
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_no_resource_created_on_412(self, client: TestClient) -> None:
        client.post("/Patient", json=_patient(identifier="multi-001"))
        client.post("/Patient", json=_patient(identifier="multi-001"))
        _post(client, _patient(identifier="multi-001"), "identifier=multi-001")
        total = client.get("/Patient?identifier=multi-001").json()["total"]
        assert total == 2  # only the two pre-existing, no new one


# ---------------------------------------------------------------------------
# Invalid header
# ---------------------------------------------------------------------------


class TestInvalidHeader:
    def test_empty_header_returns_400(self, client: TestClient) -> None:
        r = _post(client, _patient(), "")
        assert r.status_code == 400

    def test_400_is_operation_outcome(self, client: TestClient) -> None:
        r = _post(client, _patient(), "")
        assert r.json()["resourceType"] == "OperationOutcome"


# ---------------------------------------------------------------------------
# Capability statement
# ---------------------------------------------------------------------------


class TestCapability:
    def test_conditional_create_advertised(self, client: TestClient) -> None:
        meta = client.get("/metadata").json()
        for resource in meta["rest"][0]["resource"]:
            assert resource["conditionalCreate"] is True
