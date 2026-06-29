"""Exception types and FastAPI exception handlers.

Domain exceptions never leak raw SQL errors; the handler turns each into a
Problem-style JSON response.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_LOG = logging.getLogger(__name__)


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


class WorkspaceUnavailableError(MemoryServiceError):
    """A read landed on a connection that is a DEFINITE mismatch for the
    requested workspace (the served DB is a different physical file than the
    workspace's registered DB). Returning a result would risk surfacing another
    workspace's rows, so the read is refused with a distinct, actionable signal
    instead of a misleading empty (or a silent cross-workspace leak)."""

    status_code = 409
    error_code = "workspace_unavailable"


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(MemoryServiceError)
    async def _handle_service_error(_request: Request, exc: MemoryServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    # v3.5 sector-4 audit-followup: catch-all so storage / vector /
    # extraction failures degrade to a typed 500 envelope instead of
    # FastAPI's bare ``"detail":"Internal Server Error"``. The
    # original message is logged but NOT returned to the client (the
    # error string can carry SQL fragments / paths that leak setup
    # info). Operator-facing diagnostics live in server logs.
    @app.exception_handler(Exception)
    async def _handle_unhandled(_request: Request, exc: Exception) -> JSONResponse:
        _LOG.exception(
            "Unhandled exception in route — converting to typed 500",
            exc_info=exc,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "detail": (
                    "An unexpected error occurred. Check the service logs for the "
                    "stack trace. If the error is reproducible, file an issue with "
                    "the request payload."
                ),
            },
        )
