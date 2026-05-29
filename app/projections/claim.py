"""Search projection for FHIR Claim."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import ClaimProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_datetime, reference_of


class ClaimProjection(Projection):
    resource_type = "Claim"
    table = ClaimProjectionSchema

    REFERENCE_PARAMS = {
        "patient": ("patient_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }
    DATE_PARAMS = {
        "created": "created_at",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "patient_reference": reference_of(resource.get("patient")),
            "status": resource.get("status"),
            "created_at": parse_fhir_datetime(resource.get("created")),
        }


registry.register(ClaimProjection())
