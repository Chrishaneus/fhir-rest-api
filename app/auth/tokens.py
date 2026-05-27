"""JWT creation and verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

import app.config as config

_ALGORITHM = "HS256"


def _secret() -> str:
    if config.JWT_SECRET is None:
        raise RuntimeError("JWT_SECRET is not configured")
    return config.JWT_SECRET


def create_token(username: str, *, role: str, token_version: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": username,
        "role": role,
        "tv": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=config.JWT_EXPIRY_MINUTES),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def verify_token(token: str) -> dict[str, Any]:
    """Return the full decoded payload. Raises ``jwt.PyJWTError`` if invalid."""
    return jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
