"""Search projection for FHIR Location."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import LocationProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first_code, reference_of


class LocationProjection(Projection):
    resource_type = "Location"
    table = LocationProjectionSchema

    STRING_PARAMS = {
        "name": "name",
    }
    TOKEN_PARAMS = {
        "status": "status",
        "type": "type_code",
    }
    REFERENCE_PARAMS = {
        "organization": ("organization_reference", "Organization"),
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": resource.get("name"),
            "status": resource.get("status"),
            "type_code": first_code(next(iter(resource.get("type") or []), None)),
            "organization_reference": reference_of(resource.get("managingOrganization")),
        }


registry.register(LocationProjection())
