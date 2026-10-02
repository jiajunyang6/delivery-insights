import re
from time import perf_counter
from uuid import uuid4

import structlog
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from insights.api.errors import handle_problem

logger = structlog.get_logger(__name__)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if REQUEST_ID_RE.fullmatch(incoming) else uuid4().hex
        request.state.request_id = request_id
        tokens = structlog.contextvars.bind_contextvars(request_id=request_id)
        started = perf_counter()
        status = 500
        response_started = False

        async def wrapped_send(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                ]
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        except Exception as exc:
            if response_started:
                raise
            response = await handle_problem(request, exc)
            await response(scope, receive, wrapped_send)
        finally:
            if request.url.path not in {"/healthz", "/readyz"}:
                logger.info(
                    "request_completed",
                    method=request.method,
                    path=request.url.path,
                    status=status,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                )
            structlog.contextvars.reset_contextvars(**tokens)
