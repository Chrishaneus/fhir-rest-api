"""FHIR-aware HTTP errors and FastAPI exception handlers."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.utils.outcomes import fhir_json_response, operation_outcome


class FHIRHTTPError(Exception):
    """Raised by routes or hooks to return an OperationOutcome with a status code."""

    def __init__(self, status_code: int, diagnostics: str, code: str = "processing") -> None:
        super().__init__(diagnostics)
        self.status_code = status_code
        self.diagnostics = diagnostics
        self.code = code


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(FHIRHTTPError)
    async def fhir_error_handler(_: Request, exc: FHIRHTTPError) -> JSONResponse:
        return fhir_json_response(
            operation_outcome(exc.diagnostics, code=exc.code),
            status_code=exc.status_code,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not-found" if exc.status_code == 404 else "processing"
        return fhir_json_response(
            operation_outcome(str(exc.detail), code=code),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        parts = [f"{' -> '.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return fhir_json_response(
            operation_outcome(f"Invalid request: {'; '.join(parts)}", code="invalid"),
            status_code=400,
        )
