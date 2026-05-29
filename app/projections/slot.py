"""Search projection for FHIR Slot."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import SlotProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_datetime, reference_of


class SlotProjection(Projection):
    resource_type = "Slot"
    table = SlotProjectionSchema

    REFERENCE_PARAMS = {
        "schedule": ("schedule_reference", "Schedule"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }
    DATE_PARAMS = {
        "start": "start_at",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "schedule_reference": reference_of(resource.get("schedule")),
            "status": resource.get("status"),
            "start_at": parse_fhir_datetime(resource.get("start")),
        }


registry.register(SlotProjection())
