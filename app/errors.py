"""Shared structured error shape and the exception handlers that produce it.

Per project convention (NFR-5), every API error responds with the same JSON
shape instead of ad-hoc HTTPException strings: {error_code, message, detail}.
error_code is an internal, English, machine-readable identifier. message is
the text a user would see (English-only for now — it will route through the
DE/EN i18n layer once that exists for error text specifically). detail
carries optional structured context (e.g. which field failed validation).
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ErrorResponse(BaseModel):
    """The single response shape used for every API error in this project."""

    error_code: str
    message: str
    detail: str | None = None


class AppError(Exception):
    """Raise this anywhere in route/service code for a structured API error.

    Example: raise AppError("client_not_found", "Client not found.", status_code=404)
    """

    def __init__(self, error_code: str, message: str, *, status_code: int = 400, detail: str | None = None):
        self.error_code = error_code
        self.message = message
        self.detail = detail
        self.status_code = status_code
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the shared error-response handlers to the FastAPI app."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse | HTMLResponse:
        """JSON for the dashboard's own /api/* endpoints (a fetch() call needs the structured
        body to branch on); a small HTML page for every other route, since those are full-page
        browser navigations where a raw JSON blob is not a real UI (docs/ROADMAP.md §1
        follow-up — this used to be JSON everywhere, including e.g. a 404 on a nonexistent
        client, not just the newer 403s from require_role()). Imported here, not at module
        level — app.templating imports app.auth, which imports AppError from this very module,
        so a top-level import would be circular; by the time a request actually reaches this
        handler, that's moot.
        """
        if "/api/" in request.url.path:
            return JSONResponse(
                status_code=exc.status_code,
                content=ErrorResponse(error_code=exc.error_code, message=exc.message, detail=exc.detail).model_dump(),
            )
        from app.templating import render

        return render(request, "error.html", {"status_code": exc.status_code, "message": exc.message}, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error_code="validation_error",
                message="The request contains invalid data.",
                detail=str(exc.errors()),
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code="internal_error",
                message="Something went wrong. Please try again.",
                detail=None,
            ).model_dump(),
        )
