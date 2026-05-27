"""Middleware package."""

from app.middleware.logging import log_requests

__all__ = ["log_requests"]
