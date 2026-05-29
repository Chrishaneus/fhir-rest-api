"""Search projection for FHIR MedicationAdministration."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import MedicationAdministrationProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_datetime, reference_of


class MedicationAdministrationProjection(Projection):
    resource_type = "MedicationAdministration"
    table = MedicationAdministrationProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
        "encounter": ("encounter_reference", "Encounter"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }
    DATE_PARAMS = {
        "date": "occurrence_at",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        # FHIR R5 spec uses "occurenceDateTime" (single-r typo, retained in fhir.resources)
        return {
            "subject_reference": reference_of(resource.get("subject")),
            "encounter_reference": reference_of(resource.get("encounter")),
            "status": resource.get("status"),
            "occurrence_at": parse_fhir_datetime(resource.get("occurenceDateTime")),
        }


registry.register(MedicationAdministrationProjection())
