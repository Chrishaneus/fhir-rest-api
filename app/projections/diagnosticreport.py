"""Search projection for FHIR DiagnosticReport."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import DiagnosticReportProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import (
    effective_datetime,
    first_code,
    reference_of,
)


class DiagnosticReportProjection(Projection):
    resource_type = "DiagnosticReport"
    table = DiagnosticReportProjectionSchema

    TOKEN_PARAMS = {
        "status": "status",
        "code": "code_code",
    }
    REFERENCE_PARAMS = {
        "subject": ("subject_ref", "Patient"),
        "patient": ("subject_ref", "Patient"),
        "encounter": ("encounter_ref", "Encounter"),
    }
    DATE_PARAMS = {
        "date": "effective_at",
        "effective": "effective_at",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_ref": reference_of(resource.get("subject")),
            "encounter_ref": reference_of(resource.get("encounter")),
            "status": resource.get("status"),
            "code_code": first_code(resource.get("code")),
            "effective_at": effective_datetime(resource),
        }


registry.register(DiagnosticReportProjection())
