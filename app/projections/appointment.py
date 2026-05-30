"""Search projection for FHIR Appointment."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import AppointmentProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import parse_fhir_datetime, reference_of


def _patient_ref(resource: dict[str, Any]) -> str | None:
    for participant in resource.get("participant") or []:
        actor = participant.get("actor")
        ref = reference_of(actor)
        if ref and ref.startswith("Patient/"):
            return ref
    return None


class AppointmentProjection(Projection):
    resource_type = "Appointment"
    table = AppointmentProjectionSchema

    REFERENCE_PARAMS = {
        "patient": ("patient_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "status": "status",
    }
    DATE_PARAMS = {
        "date": "start_at",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "patient_reference": _patient_ref(resource),
            "status": resource.get("status"),
            "start_at": parse_fhir_datetime(resource.get("start")),
        }


registry.register(AppointmentProjection())
