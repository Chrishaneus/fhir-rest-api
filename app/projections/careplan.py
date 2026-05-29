"""Search projection for FHIR CarePlan."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import CarePlanProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import reference_of


class CarePlanProjection(Projection):
    resource_type = "CarePlan"
    table = CarePlanProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
        "encounter": ("encounter_reference", "Encounter"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_reference": reference_of(resource.get("subject")),
            "encounter_reference": reference_of(resource.get("encounter")),
            "status": resource.get("status"),
        }


registry.register(CarePlanProjection())
