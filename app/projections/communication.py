"""Search projection for FHIR Communication."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import CommunicationProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_datetime, reference_of


class CommunicationProjection(Projection):
    resource_type = "Communication"
    table = CommunicationProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }
    DATE_PARAMS = {
        "sent": "sent_at",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_reference": reference_of(resource.get("subject")),
            "status": resource.get("status"),
            "sent_at": parse_fhir_datetime(resource.get("sent")),
        }


registry.register(CommunicationProjection())
