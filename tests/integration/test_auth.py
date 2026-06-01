"""Integration tests for the /auth endpoints and JWT-protected routes.

Requires a running server with both Postgres and Redis available.
If JWT_SECRET is not configured on the server (login returns 503) the
entire module is skipped automatically via the auth_token fixture.

Run with:
    pytest tests/integration/test_auth.py -m integration
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest

from tests.integration.conftest import VERIFY_CERTIFICATES

pytestmark = pytest.mark.integration

FHIR_JSON = "application/fhir+json"


def _unique(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def registered_user(client: httpx.Client) -> dict[str, str]:
    """Register a unique user once for the session and return its credentials."""
    username = _unique("integ")
    password = "Integration!123"
    r = client.post(
        "/auth/register",
        json={"username": username, "password": password, "role": "clinician"},
    )
    assert r.status_code == 201, f"Registration failed: {r.text}"
    return {"username": username, "password": password}


@pytest.fixture(scope="session")
def auth_token(client: httpx.Client, registered_user: dict[str, str]) -> str:
    """Log in with the session user and return the bearer token.

    Skips the entire session if the server has JWT disabled.
    """
    r = client.post("/auth/login", json=registered_user)
    if r.status_code == 503:
        pytest.skip("Server has JWT_SECRET disabled — auth tests skipped")
    assert r.status_code == 200, f"Login failed: {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def auth_client(client: httpx.Client, auth_token: str):
    """An httpx.Client pre-loaded with the session bearer token."""
    with httpx.Client(
        base_url=str(client.base_url),
        timeout=10.0,
        headers={
            "Accept": FHIR_JSON,
            "Authorization": f"Bearer {auth_token}",
        },
        verify=VERIFY_CERTIFICATES,
    ) as http_client:
        yield http_client


@pytest.fixture(scope="session")
def unauth_client(client: httpx.Client):
    """An httpx.Client with no Authorization header — used to test 401 responses."""
    with httpx.Client(
        base_url=str(client.base_url),
        timeout=10.0,
        headers={"Accept": FHIR_JSON},
        verify=VERIFY_CERTIFICATES,
    ) as http_client:
        yield http_client


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_returns_201(self, client: httpx.Client) -> None:
        r = client.post(
            "/auth/register",
            json={"username": _unique(), "password": "Secure!Pass1"},
        )
        assert r.status_code == 201
        assert r.json()["message"] == "User registered"

    def test_register_with_email_and_role(self, client: httpx.Client) -> None:
        r = client.post(
            "/auth/register",
            json={
                "username": _unique(),
                "password": "Secure!Pass1",
                "email": f"{_unique()}@example.com",
                "role": "admin",
            },
        )
        assert r.status_code == 201

    def test_duplicate_username_returns_409(self, client: httpx.Client) -> None:
        username = _unique()
        client.post("/auth/register", json={"username": username, "password": "pass"})
        r = client.post("/auth/register", json={"username": username, "password": "other"})
        assert r.status_code == 409
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_duplicate_email_returns_409(self, client: httpx.Client) -> None:
        email = f"{_unique()}@example.com"
        client.post(
            "/auth/register",
            json={"username": _unique(), "password": "pass", "email": email},
        )
        r = client.post(
            "/auth/register",
            json={"username": _unique(), "password": "pass", "email": email},
        )
        assert r.status_code == 409
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_register_is_accessible_without_token(self, client: httpx.Client) -> None:
        """Register must never require authentication."""
        r = client.post(
            "/auth/register",
            json={"username": _unique(), "password": "pass"},
        )
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLogin:
    def test_login_returns_bearer_token(self, auth_token: str) -> None:
        assert isinstance(auth_token, str)
        assert len(auth_token) > 10

    def test_login_response_shape(
        self, client: httpx.Client, registered_user: dict[str, str], auth_token: str
    ) -> None:
        r = client.post("/auth/login", json=registered_user)
        assert r.status_code == 200
        body = r.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_wrong_password_returns_401(
        self, client: httpx.Client, auth_token: str
    ) -> None:
        # Use a dedicated user so failures don't accumulate against the session user.
        username = _unique("wrongpw")
        client.post("/auth/register", json={"username": username, "password": "correct"})
        r = client.post("/auth/login", json={"username": username, "password": "wrong"})
        assert r.status_code == 401
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_unknown_user_returns_401(
        self, client: httpx.Client, auth_token: str
    ) -> None:
        r = client.post(
            "/auth/login",
            json={"username": _unique("ghost"), "password": "pass"},
        )
        assert r.status_code == 401

    def test_login_is_accessible_without_token(
        self, client: httpx.Client, registered_user: dict[str, str]
    ) -> None:
        """Login must never require authentication."""
        r = client.post("/auth/login", json=registered_user)
        assert r.status_code in (200, 503)  # 200 = JWT on, 503 = JWT off

    def test_successful_login_resets_failure_counter(
        self, client: httpx.Client, auth_token: str
    ) -> None:
        """Four bad attempts followed by a good login should not lock the account."""
        username = _unique("reset")
        client.post("/auth/register", json={"username": username, "password": "correct"})
        for _ in range(4):
            client.post("/auth/login", json={"username": username, "password": "wrong"})
        r = client.post("/auth/login", json={"username": username, "password": "correct"})
        assert r.status_code == 200
        # Counter was reset — one more wrong attempt should not lock.
        r = client.post("/auth/login", json={"username": username, "password": "wrong"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Protected routes
# ---------------------------------------------------------------------------


class TestProtectedRoutes:
    def test_authenticated_request_succeeds(self, auth_client: httpx.Client) -> None:
        r = auth_client.get("/Patient")
        assert r.status_code == 200

    def test_request_without_token_returns_401(
        self, unauth_client: httpx.Client, auth_token: str
    ) -> None:
        r = unauth_client.get("/Patient")
        assert r.status_code == 401
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_invalid_token_returns_401(
        self, unauth_client: httpx.Client, auth_token: str
    ) -> None:
        r = unauth_client.get(
            "/Patient", headers={"Authorization": "Bearer this.is.invalid"}
        )
        assert r.status_code == 401

    def test_health_accessible_without_token(self, unauth_client: httpx.Client) -> None:
        assert unauth_client.get("/health").status_code == 200

    def test_metadata_accessible_without_token(self, client: httpx.Client) -> None:
        assert client.get("/metadata").status_code == 200


# ---------------------------------------------------------------------------
# Account lockout
# ---------------------------------------------------------------------------


class TestLockout:
    def test_account_locks_after_five_bad_attempts(
        self, client: httpx.Client, auth_token: str
    ) -> None:
        username = _unique("lock")
        client.post("/auth/register", json={"username": username, "password": "correct"})
        for _ in range(5):
            client.post("/auth/login", json={"username": username, "password": "wrong"})
        r = client.post("/auth/login", json={"username": username, "password": "wrong"})
        assert r.status_code == 423
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_lockout_response_includes_expiry(
        self, client: httpx.Client, auth_token: str
    ) -> None:
        username = _unique("lockexp")
        client.post("/auth/register", json={"username": username, "password": "correct"})
        for _ in range(5):
            client.post("/auth/login", json={"username": username, "password": "wrong"})
        r = client.post("/auth/login", json={"username": username, "password": "wrong"})
        assert r.status_code == 423
        diag = r.json()["issue"][0]["diagnostics"]
        # Diagnostics should contain a parseable ISO-8601 timestamp.
        expiry_str = diag.split("until ")[-1]
        expiry = datetime.fromisoformat(expiry_str)
        assert expiry > datetime.now(UTC)

    def test_correct_password_during_lockout_still_returns_423(
        self, client: httpx.Client, auth_token: str
    ) -> None:
        username = _unique("lock2")
        client.post("/auth/register", json={"username": username, "password": "correct"})
        for _ in range(5):
            client.post("/auth/login", json={"username": username, "password": "wrong"})
        r = client.post("/auth/login", json={"username": username, "password": "correct"})
        assert r.status_code == 423
