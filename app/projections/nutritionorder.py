"""Search projection for FHIR NutritionOrder."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import NutritionOrderProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import reference_of


class NutritionOrderProjection(Projection):
    resource_type = "NutritionOrder"
    table = NutritionOrderProjectionSchema

    REFERENCE_PARAMS = {
        "patient": ("patient_reference", "Patient"),
        "encounter": ("encounter_reference", "Encounter"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "patient_reference": reference_of(resource.get("subject")),
            "encounter_reference": reference_of(resource.get("encounter")),
            "status": resource.get("status"),
        }


registry.register(NutritionOrderProjection())
