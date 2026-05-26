"""Search projection for FHIR Patient."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import PatientProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first_name_part, parse_fhir_date


class PatientProjection(Projection):
    resource_type = "Patient"
    table = PatientProjectionSchema

    STRING_PARAMS = {
        "family": "family",
        "given": "given",
    }
    TOKEN_PARAMS = {
        "gender": "gender",
    }
    BOOL_PARAMS = {
        "active": "active",
    }
    DATE_PARAMS = {
        "birthdate": "birth_date",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "family": first_name_part(resource, "family"),
            "given": first_name_part(resource, "given"),
            "gender": resource.get("gender"),
            "birth_date": parse_fhir_date(resource.get("birthDate")),
            "active": resource.get("active"),
        }


registry.register(PatientProjection())
