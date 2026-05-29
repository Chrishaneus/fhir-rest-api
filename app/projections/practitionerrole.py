"""Search projection for FHIR PractitionerRole."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import PractitionerRoleProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first_code, reference_of


class PractitionerRoleProjection(Projection):
    resource_type = "PractitionerRole"
    table = PractitionerRoleProjectionSchema

    REFERENCE_PARAMS = {
        "practitioner": ("practitioner_reference", "Practitioner"),
        "organization": ("organization_reference", "Organization"),
    }
    TOKEN_PARAMS = {
        "role": "role_code",
    }
    BOOL_PARAMS = {
        "active": "active",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "practitioner_reference": reference_of(resource.get("practitioner")),
            "organization_reference": reference_of(resource.get("organization")),
            "role_code": first_code(next(iter(resource.get("code") or []), None)),
            "active": resource.get("active"),
        }


registry.register(PractitionerRoleProjection())
