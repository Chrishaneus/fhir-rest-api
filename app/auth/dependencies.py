"""FastAPI dependency for JWT bearer-token authentication."""

from __future__ import annotations

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import app.config as config
from app.auth.tokens import verify_token
from app.db.auth_models import User
from app.db.base import SessionLocal
from app.utils.errors import FHIRHTTPError

_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User | None:
    """Verify the Bearer token and return the authenticated User.

    Returns None (and skips all checks) when JWT_SECRET is not configured,
    so the FHIR layer can run unauthenticated in development.
    Raises FHIRHTTPError(401) on any auth failure.
    """
    if config.JWT_SECRET is None:
        return None

    if credentials is None:
        raise FHIRHTTPError(401, "Missing Authorization: Bearer <token> header", "security")

    try:
        payload = verify_token(credentials.credentials)
    except jwt.PyJWTError:
        raise FHIRHTTPError(401, "Invalid or expired token", "security")

    username = payload.get("sub")
    token_version = payload.get("tv")
    with SessionLocal() as session:
        user: User | None = session.query(User).filter_by(username=username).first()

    if user is None or not user.is_active:
        raise FHIRHTTPError(401, "Account not found or deactivated", "security")
    if user.token_version != token_version:
        raise FHIRHTTPError(401, "Token has been revoked", "security")

    return user
