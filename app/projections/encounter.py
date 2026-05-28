"""Search projection for FHIR Encounter."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import EncounterProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import (
    first,
    first_code,
    period_end,
    period_start,
    reference_of,
)


class EncounterProjection(Projection):
    resource_type = "Encounter"
    table = EncounterProjectionSchema

    TOKEN_PARAMS = {
        "status": "status",
        "class": "class_code",
    }
    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
    }
    DATE_PARAMS = {
        "date": "period_start",
        "date-start": "period_start",
        "date-end": "period_end",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        # In R5, Encounter.class is a list of CodeableConcept; pre-R5 it was a Coding.
        # Handle both for robustness.
        cls = resource.get("class")
        if isinstance(cls, list):
            class_code = first_code(first(cls))
        elif isinstance(cls, dict):
            class_code = cls.get("code") or first_code(cls)
        else:
            class_code = None

        return {
            "subject_reference": reference_of(resource.get("subject")),
            "status": resource.get("status"),
            "class_code": class_code,
            "period_start": period_start(
                resource.get("actualPeriod") or resource.get("period")
            ),
            "period_end": period_end(
                resource.get("actualPeriod") or resource.get("period")
            ),
        }


registry.register(EncounterProjection())
