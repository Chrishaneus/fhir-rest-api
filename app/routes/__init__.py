"""FastAPI routers for the FHIR REST layer."""

from app.routes.resources import router as resources_router
from app.routes.system import router as system_router

__all__ = ["resources_router", "system_router"]
