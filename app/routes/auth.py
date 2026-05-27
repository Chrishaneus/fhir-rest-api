"""Authentication routes: register and login."""

from __future__ import annotations

import uuid
from typing import Literal

import bcrypt
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

import app.config as config
from app.auth import rate_limit
from app.auth.tokens import create_token
from app.db.auth_models import User
from app.db.base import SessionLocal
from app.utils.errors import FHIRHTTPError
from app.utils.time import now_utc

router = APIRouter(prefix="/auth", tags=["auth"])


class _RegisterBody(BaseModel):
    username: str
    password: str
    email: EmailStr | None = None
    role: Literal["admin", "clinician", "viewer"] = "clinician"
    fhir_resource_id: str | None = None


class _LoginBody(BaseModel):
    username: str
    password: str


@router.post("/register", status_code=201)
async def register(body: _RegisterBody) -> dict:
    with SessionLocal() as session:
        if _get_user(session, body.username):
            raise FHIRHTTPError(409, f"Username '{body.username}' is already taken", "duplicate")
        if body.email and _get_user_by_email(session, body.email):
            raise FHIRHTTPError(409, f"Email '{body.email}' is already registered", "duplicate")
        hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
        now = now_utc()
        session.add(
            User(
                id=uuid.uuid4().hex,
                username=body.username,
                hashed_password=hashed,
                email=body.email,
                is_active=True,
                role=body.role,
                token_version=1,
                fhir_resource_id=body.fhir_resource_id,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return {"message": "User registered"}


@router.post("/login")
async def login(body: _LoginBody) -> JSONResponse:
    if config.JWT_SECRET is None:
        raise FHIRHTTPError(503, "JWT_SECRET is not configured on this server", "not-supported")

    expiry = rate_limit.locked_until(body.username)
    if expiry:
        raise FHIRHTTPError(423, f"Account locked until {expiry.isoformat()}", "security")

    with SessionLocal() as session:
        user = _get_user(session, body.username)
        if user is None:
            raise FHIRHTTPError(401, "Invalid credentials", "security")

        if not user.is_active:
            raise FHIRHTTPError(403, "Account is deactivated", "security")

        if not bcrypt.checkpw(body.password.encode(), user.hashed_password.encode()):
            rate_limit.record_failure(body.username)
            raise FHIRHTTPError(401, "Invalid credentials", "security")

        rate_limit.reset(body.username)
        now = now_utc()
        user.last_login_at = now
        user.updated_at = now
        session.commit()

        token = create_token(user.username, role=user.role, token_version=user.token_version)

    return JSONResponse({"access_token": token, "token_type": "bearer"})


def _get_user(session: Session, username: str) -> User | None:
    return session.query(User).filter_by(username=username).first()


def _get_user_by_email(session: Session, email: str) -> User | None:
    return session.query(User).filter_by(email=email).first()
