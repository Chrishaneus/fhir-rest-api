"""Integration test fixtures targeting a running FHIR HTTP server.

Set ``FHIR_BASE_URL`` to point at a different host; defaults to
``http://localhost:8000`` (the docker compose default).

When the server has JWT_SECRET configured, the fixture auto-registers and logs
in a throwaway test user, then attaches ``Authorization: Bearer <token>`` to
every request. Override with ``FHIR_JWT_TOKEN`` to supply a pre-existing token.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("FHIR_BASE_URL", "http://localhost:8000")

_TEST_USERNAME = f"integration-test-{uuid.uuid4().hex[:8]}"
_TEST_PASSWORD = "integration-test-pw-" + uuid.uuid4().hex[:8]


def _acquire_token(base_url: str) -> str:
    """Register a throwaway user and return a JWT Bearer token.

    Returns ``""`` if the server has no JWT_SECRET configured (503 on /auth/login).
    Honours ``FHIR_JWT_TOKEN`` env var to skip the registration step entirely.
    """
    explicit = os.environ.get("FHIR_JWT_TOKEN", "")
    if explicit:
        return explicit

    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        reg = c.post("/auth/register", json={
            "username": _TEST_USERNAME,
            "password": _TEST_PASSWORD,
        })
        if reg.status_code not in (201, 409):
            return ""

        login = c.post("/auth/login", json={
            "username": _TEST_USERNAME,
            "password": _TEST_PASSWORD,
        })
        if login.status_code != 200:
            return ""
        return login.json().get("access_token", "")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def client(base_url: str):
    transport = httpx.HTTPTransport(retries=0)

    # Probe metadata first (always public) to confirm the server is up.
    with httpx.Client(base_url=base_url, timeout=10.0, transport=transport) as probe:
        try:
            response = probe.get("/metadata")
        except httpx.HTTPError as exc:
            pytest.skip(f"FHIR server at {base_url} not reachable: {exc}")
        if response.status_code != 200:
            pytest.skip(
                f"FHIR server at {base_url} returned {response.status_code} on /metadata"
            )

    headers: dict[str, str] = {"Accept": "application/fhir+json"}
    token = _acquire_token(base_url)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(
        base_url=base_url,
        timeout=10.0,
        headers=headers,
        transport=transport,
    ) as c:
        yield c
