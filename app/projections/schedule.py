"""Search projection for FHIR Schedule."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import ScheduleProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first, reference_of


class ScheduleProjection(Projection):
    resource_type = "Schedule"
    table = ScheduleProjectionSchema

    REFERENCE_PARAMS = {
        "actor": ("actor_reference", "Practitioner"),
    }
    BOOL_PARAMS = {
        "active": "active",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "actor_reference": reference_of(first(resource.get("actor"))),
            "active": resource.get("active"),
        }


registry.register(ScheduleProjection())
