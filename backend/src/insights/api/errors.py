"""RFC 9457 problem+json responses and the exception handlers that produce them."""

from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from insights.snapshots.errors import ResourceError as ProblemError

__all__ = [
    "ProblemError",
    "handle_problem",
    "install_handlers",
    "invalid_many",
    "problem_response",
    "unavailable",
]

logger = structlog.get_logger(__name__)


def problem_response(request: Request, error: ProblemError) -> JSONResponse:
    """Render an error as problem+json, adding the request path and request id."""
    body: dict[str, Any] = {
        "type": f"/problems/{error.type_slug}",
        "title": error.title,
        "status": error.status,
        "detail": error.detail,
        "instance": request.url.path,
        "request_id": getattr(request.state, "request_id", ""),
        **error.extensions,
    }
    if error.errors is not None:
        body["errors"] = error.errors
    return JSONResponse(
        body, status_code=error.status, media_type="application/problem+json", headers=error.headers
    )


async def handle_problem(request: Request, exc: Exception) -> JSONResponse:
    """Map any exception to a problem response; unexpected ones are logged by type only."""
    if isinstance(exc, ProblemError):
        return problem_response(request, exc)
    if isinstance(exc, RequestValidationError):
        return problem_response(
            request,
            ProblemError(
                422,
                "invalid-parameter",
                "Invalid parameter",
                "One or more parameters are invalid.",
                errors=[
                    {"param": str(e["loc"][-1]), "message": "invalid value"} for e in exc.errors()
                ],
            ),
        )
    if isinstance(exc, HTTPException):
        # Framework errors (unknown route, wrong method) carry only a status; derive the
        # title and slug from it rather than echoing the exception's detail text.
        phrase = HTTPStatus(exc.status_code).phrase
        title = phrase[0] + phrase[1:].lower()
        slug = phrase.lower().replace(" ", "-")
        return problem_response(request, ProblemError(exc.status_code, slug, title, title))
    if isinstance(exc, (SQLAlchemyError, OSError, TimeoutError)):
        logger.error("database_unavailable", error_type=type(exc).__name__)
        return problem_response(
            request,
            unavailable("Postgres is unavailable."),
        )
    logger.error("unhandled_error", error_type=type(exc).__name__)
    return problem_response(
        request,
        ProblemError(
            500, "internal-error", "Internal server error", "An unexpected error occurred."
        ),
    )


def install_handlers(app: FastAPI) -> None:
    """Register handlers; other exceptions reach RequestMiddleware, which reuses handle_problem."""
    for error_type in (ProblemError, RequestValidationError, HTTPException, SQLAlchemyError):
        app.add_exception_handler(error_type, handle_problem)


def unavailable(detail: str) -> ProblemError:
    """Construct a sanitized 503 dependency-unavailable problem with the supplied detail."""
    return ProblemError(503, "dependency-unavailable", "Dependency unavailable", detail)


def invalid_many(errors: list[dict[str, str]]) -> ProblemError:
    """Construct one 422 problem containing all collected parameter validation errors."""
    return ProblemError(
        422,
        "invalid-parameter",
        "Invalid parameter",
        "One or more parameters are invalid.",
        errors=errors,
    )
