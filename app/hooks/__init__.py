"""Resource lifecycle hooks.

Each FHIR resource type can have its own hook module under this package.
The module defines a ResourceHooks subclass and calls ``hooks.register``
at module level so the hook is active as soon as the package is imported.

To add hooks for a new resource type:

    1. Create ``app/hooks/<resource_type_lower>.py``
    2. Define a class that subclasses ``ResourceHooks``
    3. Call ``hooks.register("<ResourceType>", MyHooks())`` at the bottom
    4. Add ``from app.hooks import <resource_type_lower> as _``  # noqa: F401
       at the end of this file

All existing code that does ``from app.hooks import hooks`` continues to
work without change.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ResourceHooks:
    """Default no-op hook implementation; subclass to add side effects."""

    def before_create(self, resource: dict[str, Any]) -> dict[str, Any]:
        return resource

    def after_create(self, resource: dict[str, Any]) -> None:
        return None

    def before_update(
        self,
        old: dict[str, Any] | None,
        new: dict[str, Any],
    ) -> dict[str, Any]:
        return new

    def after_update(
        self,
        old: dict[str, Any] | None,
        new: dict[str, Any],
    ) -> None:
        return None

    def before_delete(self, resource: dict[str, Any]) -> None:
        return None

    def after_delete(self, resource: dict[str, Any]) -> None:
        return None


class HookRegistry:
    """Maps FHIR resource types to :class:`ResourceHooks` instances."""

    def __init__(self) -> None:
        self._hooks: dict[str, ResourceHooks] = {}
        self._default = ResourceHooks()

    def register(self, resource_type: str, hooks: ResourceHooks) -> None:
        self._hooks[resource_type] = hooks

    def unregister(self, resource_type: str) -> None:
        self._hooks.pop(resource_type, None)

    def clear(self) -> None:
        self._hooks.clear()

    def snapshot(self) -> dict[str, "ResourceHooks"]:
        """Return a shallow copy of the current registry."""
        return dict(self._hooks)

    def restore(self, snapshot: dict[str, "ResourceHooks"]) -> None:
        """Replace the registry with a previously taken snapshot."""
        self._hooks.clear()
        self._hooks.update(snapshot)

    def get(self, resource_type: str) -> ResourceHooks:
        return self._hooks.get(resource_type, self._default)

    def registered_types(self) -> list[str]:
        return sorted(self._hooks)


hooks = HookRegistry()


class LoggingHooks(ResourceHooks):
    """Hook that emits structured log lines for each interaction."""

    def __init__(self, resource_type: str) -> None:
        self._resource_type = resource_type

    def after_create(self, resource: dict[str, Any]) -> None:
        logger.info(
            "fhir.created",
            extra={"resource_type": self._resource_type, "id": resource.get("id")},
        )

    def after_update(
        self,
        old: dict[str, Any] | None,
        new: dict[str, Any],
    ) -> None:
        logger.info(
            "fhir.updated",
            extra={
                "resource_type": self._resource_type,
                "id": new.get("id"),
                "from_version": (old or {}).get("meta", {}).get("versionId"),
                "to_version": new.get("meta", {}).get("versionId"),
            },
        )

    def after_delete(self, resource: dict[str, Any]) -> None:
        logger.info(
            "fhir.deleted",
            extra={"resource_type": self._resource_type, "id": resource.get("id")},
        )


# ---------------------------------------------------------------------------
# Per-type hook modules — each self-registers when imported.
# Add a line here for every new hook file you create.
# ---------------------------------------------------------------------------
from app.hooks import patient as _patient  # noqa: E402,F401
from app.hooks import reference_validation as _reference_validation  # noqa: E402,F401
