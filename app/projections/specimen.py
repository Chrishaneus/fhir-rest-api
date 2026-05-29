"""Search projection for FHIR Specimen."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import SpecimenProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first_code, parse_fhir_datetime, reference_of


class SpecimenProjection(Projection):
    resource_type = "Specimen"
    table = SpecimenProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "type": "type_code",
        "status": "status",
    }
    DATE_PARAMS = {
        "collected": "collected_at",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        collection = resource.get("collection") or {}
        return {
            "subject_reference": reference_of(resource.get("subject")),
            "type_code": first_code(resource.get("type")),
            "collected_at": parse_fhir_datetime(collection.get("collectedDateTime")),
            "status": resource.get("status"),
        }


registry.register(SpecimenProjection())
