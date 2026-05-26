"""Search projection for FHIR Condition."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import ConditionProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import (
    first_code,
    parse_fhir_date,
    reference_of,
)


class ConditionProjection(Projection):
    resource_type = "Condition"
    table = ConditionProjectionSchema

    TOKEN_PARAMS = {
        "code": "code_code",
        "clinical-status": "clinical_status",
    }
    REFERENCE_PARAMS = {
        "subject": ("subject_ref", "Patient"),
        "patient": ("subject_ref", "Patient"),
        "encounter": ("encounter_ref", "Encounter"),
    }
    DATE_PARAMS = {
        "recorded-date": "recorded_date",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_ref": reference_of(resource.get("subject")),
            "encounter_ref": reference_of(resource.get("encounter")),
            "clinical_status": first_code(resource.get("clinicalStatus")),
            "code_code": first_code(resource.get("code")),
            "recorded_date": parse_fhir_date(resource.get("recordedDate")),
        }


registry.register(ConditionProjection())
