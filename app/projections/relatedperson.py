"""Search projection for FHIR RelatedPerson."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import RelatedPersonProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first_name_part, reference_of


class RelatedPersonProjection(Projection):
    resource_type = "RelatedPerson"
    table = RelatedPersonProjectionSchema

    REFERENCE_PARAMS = {
        "patient": ("patient_reference", "Patient"),
    }
    STRING_PARAMS = {
        "name": "family",
    }
    BOOL_PARAMS = {
        "active": "active",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "patient_reference": reference_of(resource.get("patient")),
            "family": first_name_part(resource, "family"),
            "active": resource.get("active"),
        }


registry.register(RelatedPersonProjection())
