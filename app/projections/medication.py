"""Search projection for FHIR Medication."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import MedicationProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first_code


class MedicationProjection(Projection):
    resource_type = "Medication"
    table = MedicationProjectionSchema

    TOKEN_PARAMS = {
        "code": "code_code",
        "status": "status",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "code_code": first_code(resource.get("code")),
            "status": resource.get("status"),
        }


registry.register(MedicationProjection())
