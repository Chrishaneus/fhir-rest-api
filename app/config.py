"""Application settings read from environment variables.

All ``os.getenv`` calls for runtime configuration are centralised here.
Import from this module instead of calling ``os.getenv`` directly elsewhere.

Environment variables
---------------------

DATABASE_URL
    SQLAlchemy connection string.
    Default: ``sqlite:///./fhir.db``
    Example: ``postgresql+psycopg://fhir:fhir@localhost:5432/fhir``

CORS_ORIGINS
    Comma-separated list of allowed CORS origins, or ``*`` to allow all.
    Default: ``*``
    Example: ``https://app.example.com,https://admin.example.com``

LOG_FILE
    Path to append NDJSON log records. Logs go to stdout when unset.
    Example: ``/var/log/fhir/app.ndjson``

FHIR_RESOURCE_TYPES
    Comma-separated list of FHIR resource types to advertise in the
    CapabilityStatement. Empty string (default) means all R5 types.
    Example: ``Patient,Observation,Encounter``
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./fhir.db")

# ---------------------------------------------------------------------------
# HTTP / CORS
# ---------------------------------------------------------------------------

_cors_raw: str = os.getenv("CORS_ORIGINS", "*")
CORS_ORIGINS: list[str] = (
    ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",")]
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE: str | None = os.getenv("LOG_FILE")

# ---------------------------------------------------------------------------
# FHIR feature flags
# ---------------------------------------------------------------------------

# Comma-separated list of resource types to advertise, or "" for all R5 types.
FHIR_RESOURCE_TYPES: str = os.getenv("FHIR_RESOURCE_TYPES", "")

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

# Shared secret used to sign and verify JWT tokens.
# When unset, authentication is disabled (all requests pass through).
JWT_SECRET: str | None = os.getenv("JWT_SECRET")

# Access-token lifetime in minutes (default: 60).
JWT_EXPIRY_MINUTES: int = int(os.getenv("JWT_EXPIRY_MINUTES", "60"))

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
