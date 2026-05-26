"""Integration tests for the /metadata CapabilityStatement endpoint."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration

FHIR_JSON = "application/fhir+json"


def test_returns_fhir_r5_capability_statement(client: httpx.Client) -> None:
    r = client.get("/metadata")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith(FHIR_JSON)
    body = r.json()
    assert body["resourceType"] == "CapabilityStatement"
    assert body["fhirVersion"] == "5.0.0"


def test_advertises_core_resource_types(client: httpx.Client) -> None:
    r = client.get("/metadata")

    advertised = {entry["type"] for entry in r.json()["rest"][0]["resource"]}
    for rt in ("Patient", "Observation", "Encounter", "Condition", "Bundle"):
        assert rt in advertised
