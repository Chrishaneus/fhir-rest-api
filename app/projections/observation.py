"""Search projection for FHIR Observation.

Observation is the hottest table in any real EHR -- vital signs, lab results,
imaging measurements all flow through it. The projection prioritizes:

* ``subject_ref`` + ``effective_at`` for chart timelines (composite index)
* ``subject_ref`` + ``code_code`` for "all values of this code for this patient"
* ``encounter_ref`` for encounter views
* ``value_quantity_value`` for numeric range queries (e.g. ``value-quantity=gt140``)
"""

from __future__ import annotations

from typing import Any

from app.db.projection_models import ObservationIndex
from app.projections.base import Projection, registry
from app.projections.helpers import (
    effective_datetime,
    first,
    first_code,
    first_coding,
    quantity_parts,
    reference_of,
)


class ObservationProjection(Projection):
    resource_type = "Observation"
    table = ObservationIndex

    TOKEN_PARAMS = {
        "status": "status",
        "code": "code_code",
        "category": "category_code",
    }
    REFERENCE_PARAMS = {
        "subject": ("subject_ref", "Patient"),
        "patient": ("subject_ref", "Patient"),
        "encounter": ("encounter_ref", "Encounter"),
    }
    DATE_PARAMS = {
        "date": "effective_at",
        "effective": "effective_at",
    }
    NUMBER_PARAMS = {
        "value-quantity": "value_quantity_value",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        code_system, code_code = first_coding(resource.get("code"))
        value_value, value_unit = quantity_parts(resource.get("valueQuantity"))
        return {
            "subject_ref": reference_of(resource.get("subject")),
            "encounter_ref": reference_of(resource.get("encounter")),
            "status": resource.get("status"),
            "code_system": code_system,
            "code_code": code_code,
            "category_code": first_code(first(resource.get("category"))),
            "effective_at": effective_datetime(resource),
            "value_quantity_value": value_value,
            "value_quantity_unit": value_unit,
        }


registry.register(ObservationProjection())
