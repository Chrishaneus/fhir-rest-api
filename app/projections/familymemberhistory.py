"""Search projection for FHIR FamilyMemberHistory."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import FamilyMemberHistoryProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first_code, reference_of


class FamilyMemberHistoryProjection(Projection):
    resource_type = "FamilyMemberHistory"
    table = FamilyMemberHistoryProjectionSchema

    REFERENCE_PARAMS = {
        "patient": ("patient_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "relationship": "relationship_code",
        "status": "status",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "patient_reference": reference_of(resource.get("patient")),
            "relationship_code": first_code(resource.get("relationship")),
            "status": resource.get("status"),
        }


registry.register(FamilyMemberHistoryProjection())
