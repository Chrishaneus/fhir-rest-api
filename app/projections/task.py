"""Search projection for FHIR Task."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import TaskProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_datetime, reference_of


class TaskProjection(Projection):
    resource_type = "Task"
    table = TaskProjectionSchema

    TOKEN_PARAMS = {
        "status": "status",
        "intent": "intent",
    }
    DATE_PARAMS = {
        "authored-on": "authored_on",
    }
    REFERENCE_PARAMS = {
        "owner": ("owner_reference", "Practitioner"),
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": resource.get("status"),
            "intent": resource.get("intent"),
            "authored_on": parse_fhir_datetime(resource.get("authoredOn")),
            "owner_reference": reference_of(resource.get("owner")),
        }


registry.register(TaskProjection())
