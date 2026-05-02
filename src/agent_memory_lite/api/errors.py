"""Exception types and FastAPI exception handlers.

Domain exceptions never leak raw SQL errors; the handler turns each into a
Problem-style JSON response.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class MemoryServiceError(RuntimeError):
    """Base class for service-level errors. Carries an HTTP status hint."""

    status_code: int = 500
    error_code: str = "internal_error"

    def to_payload(self) -> dict[str, Any]:
        return {"error": self.error_code, "detail": str(self)}


class ValidationError(MemoryServiceError):
    status_code = 400
    error_code = "validation_failed"


class NotFoundError(MemoryServiceError):
    status_code = 404
    error_code = "not_found"


class ConflictError(MemoryServiceError):
    status_code = 409
    error_code = "conflict"


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(MemoryServiceError)
    async def _handle_service_error(_request: Request, exc: MemoryServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
