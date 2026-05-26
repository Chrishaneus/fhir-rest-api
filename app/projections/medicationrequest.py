"""Search projection for FHIR MedicationRequest."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import MedicationRequestIndex
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_datetime, reference_of


class MedicationRequestProjection(Projection):
    resource_type = "MedicationRequest"
    table = MedicationRequestIndex

    TOKEN_PARAMS = {
        "status": "status",
        "intent": "intent",
    }
    REFERENCE_PARAMS = {
        "subject": ("subject_ref", "Patient"),
        "patient": ("subject_ref", "Patient"),
        "requester": ("requester_ref", "Practitioner"),
    }
    DATE_PARAMS = {
        "authoredon": "authored_on",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_ref": reference_of(resource.get("subject")),
            "status": resource.get("status"),
            "intent": resource.get("intent"),
            "authored_on": parse_fhir_datetime(resource.get("authoredOn")),
            "requester_ref": reference_of(resource.get("requester")),
        }


registry.register(MedicationRequestProjection())
