"""Search projection for FHIR Coverage."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import CoverageProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import reference_of


class CoverageProjection(Projection):
    resource_type = "Coverage"
    table = CoverageProjectionSchema

    REFERENCE_PARAMS = {
        "beneficiary": ("beneficiary_reference", "Patient"),
        "patient": ("beneficiary_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "beneficiary_reference": reference_of(resource.get("beneficiary")),
            "status": resource.get("status"),
        }


registry.register(CoverageProjection())
