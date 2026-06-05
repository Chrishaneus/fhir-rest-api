from fastapi.testclient import TestClient


class TestAuth:
    def test_register_creates_user(self, client: TestClient) -> None:
        r = client.post("/auth/register", json={"username": "alice", "password": "pass123"})
        assert r.status_code == 201
        assert r.json()["message"] == "User registered"

    def test_duplicate_register_returns_409(self, client: TestClient) -> None:
        client.post("/auth/register", json={"username": "bob", "password": "pass"})
        r = client.post("/auth/register", json={"username": "bob", "password": "other"})
        assert r.status_code == 409
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_login_returns_bearer_token(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        client.post("/auth/register", json={"username": "carol", "password": "secret"})
        r = client.post("/auth/login", json={"username": "carol", "password": "secret"})
        assert r.status_code == 200
        body = r.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_login_without_jwt_secret_returns_503(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", None)
        client.post("/auth/register", json={"username": "e", "password": "pass"})
        r = client.post("/auth/login", json={"username": "e", "password": "pass"})
        assert r.status_code == 503

    def test_login_wrong_password_returns_401(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        client.post("/auth/register", json={"username": "dave", "password": "correct"})
        r = client.post("/auth/login", json={"username": "dave", "password": "wrong"})
        assert r.status_code == 401
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_login_unknown_user_returns_401(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        r = client.post("/auth/login", json={"username": "nobody", "password": "pass"})
        assert r.status_code == 401

    def test_register_with_email(self, client: TestClient) -> None:
        r = client.post(
            "/auth/register",
            json={"username": "alice", "password": "pass", "email": "alice@example.com"},
        )
        assert r.status_code == 201

    def test_duplicate_email_returns_409(self, client: TestClient) -> None:
        client.post(
            "/auth/register",
            json={"username": "alice", "password": "pass", "email": "alice@example.com"},
        )
        r = client.post(
            "/auth/register",
            json={"username": "alice2", "password": "pass", "email": "alice@example.com"},
        )
        assert r.status_code == 409
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_register_with_role(self, client: TestClient) -> None:
        r = client.post(
            "/auth/register",
            json={"username": "admin1", "password": "pass", "role": "admin"},
        )
        assert r.status_code == 201

    def test_account_lockout_after_max_attempts(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        client.post("/auth/register", json={"username": "lockme", "password": "correct"})
        for _ in range(5):
            client.post("/auth/login", json={"username": "lockme", "password": "wrong"})
        r = client.post("/auth/login", json={"username": "lockme", "password": "wrong"})
        assert r.status_code == 423
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_correct_password_after_lockout_still_locked(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        client.post("/auth/register", json={"username": "lockme2", "password": "correct"})
        for _ in range(5):
            client.post("/auth/login", json={"username": "lockme2", "password": "wrong"})
        r = client.post("/auth/login", json={"username": "lockme2", "password": "correct"})
        assert r.status_code == 423


class TestJWTMiddleware:
    def test_no_secret_configured_allows_all(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", None)
        r = client.get("/Patient")
        assert r.status_code == 200

    def test_missing_token_returns_401(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        r = client.get("/Patient")
        assert r.status_code == 401
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_invalid_token_returns_401(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        r = client.get("/Patient", headers={"Authorization": "Bearer notavalidtoken"})
        assert r.status_code == 401

    def test_valid_token_allows_request(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        client.post("/auth/register", json={"username": "jwt-user", "password": "pass"})
        token = client.post(
            "/auth/login", json={"username": "jwt-user", "password": "pass"}
        ).json()["access_token"]
        r = client.get("/Patient", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    def test_health_exempt_when_auth_enabled(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        assert client.get("/health").status_code == 200

    def test_metadata_exempt_when_auth_enabled(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        assert client.get("/metadata").status_code == 200

    def test_register_exempt_when_auth_enabled(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        r = client.post("/auth/register", json={"username": "newuser", "password": "pass"})
        assert r.status_code == 201

    def test_login_exempt_when_auth_enabled(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        client.post("/auth/register", json={"username": "loginuser", "password": "pass"})
        r = client.post("/auth/login", json={"username": "loginuser", "password": "pass"})
        assert r.status_code == 200


class TestTokenVersionMiddleware:
    def test_token_version_mismatch_returns_401(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        client.post("/auth/register", json={"username": "tv-user", "password": "pass"})
        token = client.post("/auth/login", json={"username": "tv-user", "password": "pass"}).json()[
            "access_token"
        ]

        from app.db.auth_models import User
        from app.db.base import SessionLocal

        with SessionLocal() as session:
            user = session.query(User).filter_by(username="tv-user").first()
            assert user is not None
            user.token_version += 1
            session.commit()

        r = client.get("/Patient", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_inactive_user_token_returns_401(
        self, client: TestClient, monkeypatch, jwt_secret: str
    ) -> None:
        monkeypatch.setattr("app.config.JWT_SECRET", jwt_secret)
        client.post("/auth/register", json={"username": "inactive-user", "password": "pass"})
        token = client.post(
            "/auth/login", json={"username": "inactive-user", "password": "pass"}
        ).json()["access_token"]

        from app.db.auth_models import User
        from app.db.base import SessionLocal

        with SessionLocal() as session:
            user = session.query(User).filter_by(username="inactive-user").first()
            assert user is not None
            user.is_active = False
            session.commit()

        r = client.get("/Patient", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
