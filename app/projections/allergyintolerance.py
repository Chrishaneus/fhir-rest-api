"""Search projection for FHIR AllergyIntolerance."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import AllergyIntoleranceIndex
from app.projections.base import Projection, registry
from app.projections.helpers import first_code, reference_of


class AllergyIntoleranceProjection(Projection):
    resource_type = "AllergyIntolerance"
    table = AllergyIntoleranceIndex

    TOKEN_PARAMS = {
        "code": "code_code",
        "clinical-status": "clinical_status",
        "criticality": "criticality",
    }
    REFERENCE_PARAMS = {
        "patient": ("patient_ref", "Patient"),
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "patient_ref": reference_of(resource.get("patient")),
            "code_code": first_code(resource.get("code")),
            "clinical_status": first_code(resource.get("clinicalStatus")),
            "criticality": resource.get("criticality"),
        }


registry.register(AllergyIntoleranceProjection())
