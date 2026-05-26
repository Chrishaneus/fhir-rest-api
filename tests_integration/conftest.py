"""Integration test fixtures targeting a running FHIR HTTP server.

Set ``FHIR_BASE_URL`` to point at a different host; defaults to
``http://localhost:8000`` (the docker compose default).
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("FHIR_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def client(base_url: str):
    transport = httpx.HTTPTransport(retries=0)
    with httpx.Client(
        base_url=base_url,
        timeout=10.0,
        headers={"Accept": "application/fhir+json"},
        transport=transport,
    ) as c:
        try:
            response = c.get("/metadata")
        except httpx.HTTPError as exc:
            pytest.skip(f"FHIR server at {base_url} not reachable: {exc}")
        if response.status_code != 200:
            pytest.skip(
                f"FHIR server at {base_url} returned {response.status_code} on /metadata"
            )
        yield c
