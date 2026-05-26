"""Lifecycle hooks for the Patient resource type."""

from __future__ import annotations

import logging
from typing import Any

from app.hooks import ResourceHooks, hooks
from app.utils.errors import FHIRHTTPError
from app.utils.time import fhir_instant, now_utc

logger = logging.getLogger(__name__)

_CREATED_AT_URL = "https://example.org/fhir/StructureDefinition/created-at"


class PatientHooks(ResourceHooks):

    def before_create(self, resource: dict[str, Any]) -> dict[str, Any]:
        # Stamp the server-side creation time as an extension so consumers
        # can distinguish "when was this first entered" from lastUpdated.
        resource.setdefault("extension", []).append(
            {
                "url": _CREATED_AT_URL,
                "valueInstant": fhir_instant(now_utc()),
            }
        )
        return resource

    def before_update(
        self,
        old: dict[str, Any] | None,
        new: dict[str, Any],
    ) -> dict[str, Any]:
        # birthDate is immutable once set.
        if old and old.get("birthDate") and new.get("birthDate") != old["birthDate"]:
            raise FHIRHTTPError(
                409,
                "birthDate cannot be changed after it has been set",
                "business-rule",
            )

        # Re-attach the created-at extension if the client's PUT body omitted it.
        if old:
            original = next(
                (e for e in old.get("extension", []) if e.get("url") == _CREATED_AT_URL),
                None,
            )
            if original:
                existing = new.setdefault("extension", [])
                if not any(e.get("url") == _CREATED_AT_URL for e in existing):
                    existing.append(original)

        return new

    def before_delete(self, resource: dict[str, Any]) -> None:
        if resource.get("deceasedBoolean") or resource.get("deceasedDateTime"):
            raise FHIRHTTPError(
                409,
                "Deceased patients cannot be deleted; use a data-correction workflow instead",
                "business-rule",
            )

    def after_create(self, resource: dict[str, Any]) -> None:
        logger.info("patient.created", extra={"id": resource.get("id")})

    def after_update(self, old: dict[str, Any] | None, new: dict[str, Any]) -> None:
        logger.info(
            "patient.updated",
            extra={
                "id": new.get("id"),
                "from_version": (old or {}).get("meta", {}).get("versionId"),
                "to_version": new.get("meta", {}).get("versionId"),
            },
        )

    def after_delete(self, resource: dict[str, Any]) -> None:
        logger.info("patient.deleted", extra={"id": resource.get("id")})


hooks.register("Patient", PatientHooks())
