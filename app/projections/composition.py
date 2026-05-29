"""Search projection for FHIR Composition."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import CompositionProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first, first_code, parse_fhir_datetime, reference_of


class CompositionProjection(Projection):
    resource_type = "Composition"
    table = CompositionProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
        "encounter": ("encounter_reference", "Encounter"),
    }
    TOKEN_PARAMS = {
        "status": "status",
        "type": "type_code",
    }
    DATE_PARAMS = {
        "date": "doc_date",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_reference": reference_of(first(resource.get("subject"))),
            "encounter_reference": reference_of(resource.get("encounter")),
            "status": resource.get("status"),
            "type_code": first_code(resource.get("type")),
            "doc_date": parse_fhir_datetime(resource.get("date")),
        }


registry.register(CompositionProjection())
