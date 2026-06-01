"""Search projection for FHIR AuditEvent."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.projection_models import AuditEventProjectionSchema
from app.projections.base import Projection, registry


class AuditEventProjection(Projection):
    resource_type = "AuditEvent"
    table = AuditEventProjectionSchema

    DATE_PARAMS = {"date": "recorded_at"}
    TOKEN_PARAMS = {"action": "action"}
    STRING_PARAMS = {"agent": "agent_username", "entity": "entity_reference"}

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        agent = next(iter(resource.get("agent") or []), None)
        agent_username = (
            agent.get("who", {}).get("identifier", {}).get("value") if agent else None
        )
        entity = next(iter(resource.get("entity") or []), None)
        entity_reference = (
            entity.get("what", {}).get("reference") if entity else None
        )
        return {
            "recorded_at": self._parse_datetime(resource.get("recorded")),
            "action": resource.get("action"),
            "agent_username": agent_username,
            "entity_reference": entity_reference,
        }


registry.register(AuditEventProjection())
