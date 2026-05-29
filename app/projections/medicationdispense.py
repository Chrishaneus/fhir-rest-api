"""Search projection for FHIR MedicationDispense."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import MedicationDispenseProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_datetime, reference_of


class MedicationDispenseProjection(Projection):
    resource_type = "MedicationDispense"
    table = MedicationDispenseProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }
    DATE_PARAMS = {
        "whenhandedover": "when_handed_over",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_reference": reference_of(resource.get("subject")),
            "status": resource.get("status"),
            "when_handed_over": parse_fhir_datetime(resource.get("whenHandedOver")),
        }


registry.register(MedicationDispenseProjection())
