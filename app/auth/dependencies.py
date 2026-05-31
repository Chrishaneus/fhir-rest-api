"""FastAPI dependency for JWT bearer-token authentication and role-based access control."""

from __future__ import annotations

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import app.config as config
from app.auth.tokens import verify_token
from app.db.auth_models import User
from app.enums import UserRole
from app.db.base import SessionLocal
from app.utils.audit import set_actor
from app.utils.errors import FHIRHTTPError

_bearer = HTTPBearer(auto_error=False)

# Methods each role is permitted to use on FHIR resource routes.
_ROLE_ALLOWED_METHODS: dict[UserRole, frozenset[str]] = {
    UserRole.VIEWER:    frozenset({"GET", "HEAD", "OPTIONS"}),
    UserRole.CLINICIAN: frozenset({"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH"}),
    UserRole.ADMIN:     frozenset({"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}),
}


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User | None:
    """Verify the Bearer token and return the authenticated User.

    Returns None (and skips all checks) when JWT_SECRET is not configured,
    so the FHIR layer can run unauthenticated in development.
    Raises FHIRHTTPError(401) on any auth failure.
    """
    if config.JWT_SECRET is None:
        request.state.current_user = None
        set_actor(None)
        return None

    if credentials is None:
        raise FHIRHTTPError(401, "Missing Authorization: Bearer <token> header", "security")

    try:
        payload = verify_token(credentials.credentials)
    except jwt.PyJWTError:
        raise FHIRHTTPError(401, "Invalid or expired token", "security") from None

    username = payload.get("sub")
    token_version = payload.get("tv")
    with SessionLocal() as session:
        user: User | None = session.query(User).filter_by(username=username).first()

    if user is None or not user.is_active:
        raise FHIRHTTPError(401, "Account not found or deactivated", "security")
    if user.token_version != token_version:
        raise FHIRHTTPError(401, "Token has been revoked", "security")

    request.state.current_user = user
    set_actor(user.username)
    return user


async def require_permission(
    request: Request,
    user: User | None = Depends(require_auth),
) -> None:
    """Enforce role-based access control on top of authentication.

    Raises FHIRHTTPError(403) when the authenticated user's role does not
    permit the HTTP method being used.  Skips the check in dev mode (when
    require_auth returns None because JWT_SECRET is not configured).
    """
    if user is None:
        return
    try:
        role = UserRole(user.role)
    except ValueError:
        raise FHIRHTTPError(403, f"Unknown role '{user.role}'", "forbidden") from None
    allowed = _ROLE_ALLOWED_METHODS.get(role, frozenset())
    if request.method not in allowed:
        raise FHIRHTTPError(
            403,
            f"Role '{user.role}' is not permitted to perform {request.method} requests",
            "forbidden",
        )
