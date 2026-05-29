"""Search projection for FHIR ImagingStudy."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import ImagingStudyProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_datetime, reference_of


class ImagingStudyProjection(Projection):
    resource_type = "ImagingStudy"
    table = ImagingStudyProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
        "encounter": ("encounter_reference", "Encounter"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }
    DATE_PARAMS = {
        "started": "started_at",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_reference": reference_of(resource.get("subject")),
            "encounter_reference": reference_of(resource.get("encounter")),
            "status": resource.get("status"),
            "started_at": parse_fhir_datetime(resource.get("started")),
        }


registry.register(ImagingStudyProjection())
