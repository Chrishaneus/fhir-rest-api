"""Search projection for FHIR Goal."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import GoalProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_date, reference_of


class GoalProjection(Projection):
    resource_type = "Goal"
    table = GoalProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "lifecycle-status": "lifecycle_status",
    }
    DATE_PARAMS = {
        "start-date": "start_date",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_reference": reference_of(resource.get("subject")),
            "lifecycle_status": resource.get("lifecycleStatus"),
            "start_date": parse_fhir_date(resource.get("startDate")),
        }


registry.register(GoalProjection())
