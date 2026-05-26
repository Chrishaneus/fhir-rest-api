"""Search projection for FHIR Organization."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import OrganizationProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first, first_code


class OrganizationProjection(Projection):
    resource_type = "Organization"
    table = OrganizationProjectionSchema

    STRING_PARAMS = {
        "name": "name",
    }
    TOKEN_PARAMS = {
        "type": "type_code",
    }
    BOOL_PARAMS = {
        "active": "active",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": resource.get("name"),
            "active": resource.get("active"),
            "type_code": first_code(first(resource.get("type"))),
        }


registry.register(OrganizationProjection())
