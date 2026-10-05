"""Helper that turns service results into HTTP responses."""

from starlette.responses import Response

from insights.snapshots.caching import Reply


def response(reply: Reply) -> Response:
    """Convert domain reply bytes/status/headers into JSON, omitting media type for 304."""
    return Response(
        reply.body,
        status_code=reply.status,
        headers=reply.headers,
        media_type=None if reply.status == 304 else "application/json",
    )
