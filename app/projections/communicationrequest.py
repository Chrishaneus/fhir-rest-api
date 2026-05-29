"""Search projection for FHIR CommunicationRequest."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import CommunicationRequestProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_datetime, reference_of


class CommunicationRequestProjection(Projection):
    resource_type = "CommunicationRequest"
    table = CommunicationRequestProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
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
            "status": resource.get("status"),
            "authored_on": parse_fhir_datetime(resource.get("authoredOn")),
        }


registry.register(CommunicationRequestProjection())
