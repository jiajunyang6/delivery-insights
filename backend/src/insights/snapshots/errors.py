"""Problem-details errors raised by snapshot orchestration and rendered by the API."""

from typing import Any


class ResourceError(Exception):
    """Error rendered as application/problem+json; type_slug becomes /problems/<slug>."""

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


def invalid(param: str, message: str = "invalid value") -> ResourceError:
    return ResourceError(
        422,
        "invalid-parameter",
        "Invalid parameter",
        "One or more parameters are invalid.",
        errors=[{"param": param, "message": message}],
    )
