"""Stable JSON error envelope (AGENTS.md §23.4).

The frontend ``ApiError`` (``frontend/lib/api/client.ts``) reads
``envelope.error.{code,message,details,correlation_id}`` — keep this shape.
**Every** error response must carry this envelope: the client dereferences
``envelope.error.code`` unconditionally, so a bare ``{"detail": ...}`` body
(FastAPI's default for an unhandled exception) crashes it. The catch-all
handler below guarantees the shape for anything the specific handlers miss.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import SQLAlchemyError

log = logging.getLogger("app.errors")


class ApiError(Exception):
    """Raise from a route to emit the standard error envelope."""

    def __init__(self, status_code: int, code: str, message: str, details: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def _envelope(code: str, message: str, details: str | None, correlation_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "correlation_id": correlation_id,
        }
    }


def _respond(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: str | None,
    *,
    exc: BaseException | None = None,
) -> JSONResponse:
    """Log the failure against a fresh correlation id and return the envelope."""
    correlation_id = str(uuid.uuid4())
    level = logging.ERROR if status_code >= 500 else logging.INFO
    log.log(
        level,
        "%s %s -> %s %s (%s)",
        request.method,
        request.url.path,
        status_code,
        code,
        correlation_id,
        exc_info=exc if status_code >= 500 else None,
    )
    return JSONResponse(
        status_code=status_code,
        content=_envelope(code, message, details, correlation_id),
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _respond(request, exc.status_code, exc.code, exc.message, exc.details, exc=exc)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 401: "unauthorized", 403: "forbidden", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return _respond(request, exc.status_code, code, str(exc.detail), None, exc=exc)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _respond(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "validation_error",
            "Request validation failed.",
            str(exc.errors()),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _handle_db_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        # Keep the envelope stable so the frontend falls back to demo data
        # cleanly instead of surfacing a raw 500 (§23.2).
        return _respond(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "metastore_unavailable",
            "The metastore is not reachable.",
            type(exc).__name__,
            exc=exc,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Last resort: any exception a specific handler above did not claim.
        # Without this, FastAPI returns a bare ``{"detail": ...}`` body and the
        # frontend client throws on ``envelope.error.code``.
        return _respond(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "An unexpected error occurred.",
            type(exc).__name__,
            exc=exc,
        )
