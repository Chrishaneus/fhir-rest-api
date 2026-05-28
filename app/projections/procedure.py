"""Search projection for FHIR Procedure."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import ProcedureProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import (
    first_code,
    performed_datetime,
    reference_of,
)


class ProcedureProjection(Projection):
    resource_type = "Procedure"
    table = ProcedureProjectionSchema

    TOKEN_PARAMS = {
        "status": "status",
        "code": "code_code",
    }
    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
        "encounter": ("encounter_reference", "Encounter"),
    }
    DATE_PARAMS = {
        "date": "performed_at",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_reference": reference_of(resource.get("subject")),
            "encounter_reference": reference_of(resource.get("encounter")),
            "status": resource.get("status"),
            "code_code": first_code(resource.get("code")),
            "performed_at": performed_datetime(resource),
        }


registry.register(ProcedureProjection())
