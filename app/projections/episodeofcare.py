"""Search projection for FHIR EpisodeOfCare."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import EpisodeOfCareProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import period_start, reference_of


class EpisodeOfCareProjection(Projection):
    resource_type = "EpisodeOfCare"
    table = EpisodeOfCareProjectionSchema

    REFERENCE_PARAMS = {
        "patient": ("patient_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }
    DATE_PARAMS = {
        "date": "period_start",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "patient_reference": reference_of(resource.get("patient")),
            "status": resource.get("status"),
            "period_start": period_start(resource.get("period")),
        }


registry.register(EpisodeOfCareProjection())
