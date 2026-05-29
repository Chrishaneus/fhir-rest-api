"""Search projection for FHIR Immunization."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import ImmunizationProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first_code, parse_fhir_datetime, reference_of


class ImmunizationProjection(Projection):
    resource_type = "Immunization"
    table = ImmunizationProjectionSchema

    REFERENCE_PARAMS = {
        "patient": ("patient_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "status": "status",
        "vaccine-code": "vaccine_code",
    }
    DATE_PARAMS = {
        "date": "occurrence_at",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "patient_reference": reference_of(resource.get("patient")),
            "status": resource.get("status"),
            "vaccine_code": first_code(resource.get("vaccineCode")),
            "occurrence_at": parse_fhir_datetime(resource.get("occurrenceDateTime")),
        }


registry.register(ImmunizationProjection())
