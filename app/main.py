"""FastAPI application wiring the FHIR REST layer.

Routers (see ``app.routes``):

* ``system``    - capability statement, system history, system search
* ``resources`` - type-level and instance-level interactions

Reusable building blocks:

* ``app.utils.*`` - FHIR/HTTP helpers (errors, headers, search, bundles, ...)
* ``app.db.*``    - SQLAlchemy engine and ORM models
* ``app.store``   - FHIRStore on top of SQLAlchemy
* ``app.hooks``   - ResourceHooks + HookRegistry for side effects

Environment variables:

* ``DATABASE_URL``  - SQLAlchemy connection string (default: sqlite:///./fhir.db)
* ``CORS_ORIGINS``  - comma-separated allowed origins, or ``*`` (default: ``*``)
* ``LOG_FILE``      - path to append NDJSON logs; stdout if unset
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import CORS_ORIGINS
from app.db.base import init_db
from app.routes import resources_router, system_router
from app.utils.errors import register_exception_handlers
from app.utils.logging import configure_logging

logger = configure_logging()


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


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": ms,
        },
    )
    return response


register_exception_handlers(app)

# Order matters: the system router's literal paths (/metadata, /_history, /)
# are registered first so they win against the generic /{resource_type} routes.
app.include_router(system_router)
app.include_router(resources_router)
