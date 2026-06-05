"""Audit writer — persists FHIR R5 AuditEvent resources for every mutation."""

from __future__ import annotations

from contextvars import ContextVar

from fhir.resources.auditevent import AuditEvent

from app.utils.time import now_utc

_current_actor: ContextVar[str | None] = ContextVar("current_actor", default=None)

_ACTION_CODE: dict[str, str] = {
    "create": "C",
    "update": "U",
    "delete": "D",
}

_INTERACTION_SYSTEM = "http://hl7.org/fhir/restful-interaction"
_AUDIT_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/audit-event-type"
_OUTCOME_SYSTEM = "http://terminology.hl7.org/CodeSystem/operation-outcome"


def set_actor(username: str | None) -> None:
    """Set the actor for all audit entries written in this request context."""
    _current_actor.set(username)


def _build_audit_event(
    resource_type: str,
    resource_id: str,
    version_id: str,
    action: str,
) -> dict:
    actor = _current_actor.get()
    audit_event = AuditEvent.model_validate(
        {
            "resourceType": "AuditEvent",
            "category": [
                {
                    "coding": [
                        {
                            "system": _AUDIT_TYPE_SYSTEM,
                            "code": "rest",
                            "display": "RESTful Operation",
                        }
                    ]
                }
            ],
            "code": {"coding": [{"system": _INTERACTION_SYSTEM, "code": action}]},
            "action": _ACTION_CODE.get(action, "E"),
            "severity": "notice",
            "recorded": now_utc().isoformat(),
            "outcome": {"code": {"system": _OUTCOME_SYSTEM, "code": "0", "display": "Success"}},
            "agent": [
                {
                    "who": {"identifier": {"value": actor or "anonymous"}},
                    "requestor": True,
                }
            ],
            "source": {"observer": {"identifier": {"value": "fhir-rest-layer"}}},
            "entity": [
                {"what": {"reference": f"{resource_type}/{resource_id}/_history/{version_id}"}}
            ],
        }
    )
    return audit_event.model_dump(exclude_none=True, mode="json")


def write_audit_entry(
    resource_type: str,
    resource_id: str,
    version_id: str,
    action: str,
) -> None:
    """Persist one FHIR AuditEvent for a completed create/update/delete."""
    from app.store import store  # late import — store imports projections, not audit

    audit_event = _build_audit_event(resource_type, resource_id, version_id, action)
    store.create("AuditEvent", audit_event)
