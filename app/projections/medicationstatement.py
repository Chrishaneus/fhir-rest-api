"""Search projection for FHIR MedicationStatement."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import MedicationStatementProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import effective_datetime, reference_of


class MedicationStatementProjection(Projection):
    resource_type = "MedicationStatement"
    table = MedicationStatementProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }
    DATE_PARAMS = {
        "effective": "effective_at",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_reference": reference_of(resource.get("subject")),
            "status": resource.get("status"),
            "effective_at": effective_datetime(resource),
        }


registry.register(MedicationStatementProjection())
