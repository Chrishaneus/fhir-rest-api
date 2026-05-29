"""Search projection for FHIR ServiceRequest."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import ServiceRequestProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_datetime, reference_of


class ServiceRequestProjection(Projection):
    resource_type = "ServiceRequest"
    table = ServiceRequestProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
        "encounter": ("encounter_reference", "Encounter"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }
    DATE_PARAMS = {
        "authored": "authored_on",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_reference": reference_of(resource.get("subject")),
            "encounter_reference": reference_of(resource.get("encounter")),
            "status": resource.get("status"),
            "authored_on": parse_fhir_datetime(resource.get("authoredOn")),
        }


registry.register(ServiceRequestProjection())
