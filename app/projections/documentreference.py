"""Search projection for FHIR DocumentReference."""

from __future__ import annotations

from typing import Any

from app.db.projection_models import DocumentReferenceProjectionSchema
from app.projections.base import Projection, registry
from app.projections.helpers import first_code, parse_fhir_datetime, reference_of


class DocumentReferenceProjection(Projection):
    resource_type = "DocumentReference"
    table = DocumentReferenceProjectionSchema

    REFERENCE_PARAMS = {
        "subject": ("subject_reference", "Patient"),
        "patient": ("subject_reference", "Patient"),
    }
    TOKEN_PARAMS = {
        "status": "status",
        "docstatus": "doc_status",
        "type": "type_code",
    }
    DATE_PARAMS = {
        "date": "doc_date",
    }

    def extract(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_reference": reference_of(resource.get("subject")),
            "status": resource.get("status"),
            "doc_status": resource.get("docStatus"),
            "type_code": first_code(resource.get("type")),
            "doc_date": parse_fhir_datetime(resource.get("date")),
        }


registry.register(DocumentReferenceProjection())
