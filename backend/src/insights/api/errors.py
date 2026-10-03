from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

logger = structlog.get_logger(__name__)


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        type_slug: str,
        title: str,
        detail: str,
        *,
        errors: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.type_slug = type_slug
        self.title = title
        self.detail = detail
        self.errors = errors
        self.headers = headers or {}
        self.extensions = extensions or {}


def problem_response(request: Request, error: ProblemError) -> JSONResponse:
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
        slug, title = (
            ("not-found", "Not found")
            if exc.status_code == 404
            else ("method-not-allowed", "Method not allowed")
        )
        return problem_response(request, ProblemError(exc.status_code, slug, title, title))
    if isinstance(exc, (SQLAlchemyError, OSError, TimeoutError)):
        logger.error("database_unavailable", error_type=type(exc).__name__)
        return problem_response(
            request,
            ProblemError(
                503, "dependency-unavailable", "Dependency unavailable", "Postgres is unavailable."
            ),
        )
    logger.error("unhandled_error", error_type=type(exc).__name__)
    return problem_response(
        request,
        ProblemError(
            500, "internal-error", "Internal server error", "An unexpected error occurred."
        ),
    )


def install_handlers(app: FastAPI) -> None:
    for error_type in (ProblemError, RequestValidationError, HTTPException, SQLAlchemyError):
        app.add_exception_handler(error_type, handle_problem)
