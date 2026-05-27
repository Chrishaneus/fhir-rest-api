"""FastAPI routers for the FHIR REST layer."""

from app.routes.auth import router as auth_router
from app.routes.resources import router as resources_router
from app.routes.system import router as system_router

__all__ = ["auth_router", "resources_router", "system_router"]
