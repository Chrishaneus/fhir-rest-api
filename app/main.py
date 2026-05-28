"""FastAPI application wiring the FHIR REST layer.

Routers (see `app.routes` and `app.auth`):

* `auth`      - register and login (JWT)
* `system`    - capability statement, system history, system search
* `resources` - type-level and instance-level interactions

Reusable building blocks:

* `app.utils.*`  - FHIR/HTTP helpers (errors, headers, search, bundles, ...)
* `app.db.*`     - SQLAlchemy engine and ORM models
* `app.store`    - FHIRStore on top of SQLAlchemy
* `app.hooks`    - ResourceHooks + HookRegistry for side effects
* `app.middleware` - logging and JWT auth middleware

Environment variables:

* `DATABASE_URL`        - SQLAlchemy connection string (default: sqlite:///./fhir.db)
* `CORS_ORIGINS`        - comma-separated allowed origins, or `*` (default: `*`)
* `LOG_FILE`            - path to append NDJSON logs; stdout if unset
* `JWT_SECRET`          - secret for signing JWTs; unset disables authentication
* `JWT_EXPIRY_MINUTES`  - token lifetime in minutes (default: 60)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import CORS_ORIGINS
from app.db.base import init_db
from app.middleware import format_negotiation, log_requests
from app.routes import auth_router, resources_router, system_router
from app.utils.errors import register_exception_handlers
from app.utils.logging import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="FHIR REST Layer",
    version="0.1.0",
    default_response_class=JSONResponse,
    lifespan=lifespan,
)
app.router.redirect_slashes = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(BaseHTTPMiddleware, dispatch=format_negotiation)
app.add_middleware(BaseHTTPMiddleware, dispatch=log_requests)

register_exception_handlers(app)

# Route order: auth and system literal paths first so they win over
# the generic /{resource_type} catch-all in resources_router.
app.include_router(auth_router)
app.include_router(system_router)
app.include_router(resources_router)
