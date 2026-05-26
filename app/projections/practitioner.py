"""Search projection for FHIR Practitioner."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import PractitionerProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first_name_part


class PractitionerProjection(Projection):
    resource_type = "Practitioner"
    table = PractitionerProjectionSchema

    STRING_PARAMS = {
        "family": "family",
        "given": "given",
    }
    BOOL_PARAMS = {
        "active": "active",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "family": first_name_part(resource, "family"),
            "given": first_name_part(resource, "given"),
            "active": resource.get("active"),
        }


registry.register(PractitionerProjection())
