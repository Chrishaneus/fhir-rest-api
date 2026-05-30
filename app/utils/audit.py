"""Audit log writer for FHIR resource mutations."""

from __future__ import annotations

from contextvars import ContextVar

from app.db.audit_models import AuditLog
from app.db.base import SessionLocal
from app.utils.time import now_utc

_current_actor: ContextVar[str | None] = ContextVar("current_actor", default=None)


def set_actor(username: str | None) -> None:
    """Set the actor for all audit entries written in this request context."""
    _current_actor.set(username)


def write_audit_entry(
    resource_type: str,
    resource_id: str,
    version_id: str,
    action: str,
) -> None:
    """Persist one audit log row for a completed create/update/delete."""
    with SessionLocal() as session:
        session.add(
            AuditLog(
                resource_type=resource_type,
                resource_id=resource_id,
                version_id=version_id,
                action=action,
                actor=_current_actor.get(),
                timestamp=now_utc(),
            )
        )
        session.commit()
