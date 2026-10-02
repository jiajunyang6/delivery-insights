from unittest.mock import AsyncMock

import httpx
import pytest

from insights.api.deps import get_redis, get_session
from insights.main import create_app


@pytest.mark.parametrize("failed", [None, "postgres", "redis"])
async def test_readiness(failed):
    app = create_app()
    session, redis = AsyncMock(), AsyncMock()
    if failed == "postgres":
        session.execute.side_effect = OSError("sensitive failure details")
    if failed == "redis":
        redis.ping.side_effect = OSError("sensitive failure details")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_redis] = lambda: redis
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        live = await client.get("/healthz")
        assert live.json() == {"status": "ok"}
        response = await client.get("/readyz", headers={"X-Request-ID": "test-123"})
        assert response.status_code == (503 if failed else 200)
        assert response.headers["x-request-id"] == "test-123"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        if failed:
            assert response.json()["checks"][failed] == "error"
            assert response.headers["content-type"] == "application/problem+json"
            assert "sensitive" not in response.text


async def test_unhandled_error_is_sanitized_and_request_id_survives():
    app = create_app()

    @app.get("/broken")
    async def broken():
        raise RuntimeError("private database data")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/broken", headers={"X-Request-ID": "x" * 65})
        assert response.status_code == 500
        assert response.json()["detail"] == "An unexpected error occurred."
        assert "private" not in response.text
        assert len(response.headers["x-request-id"]) == 32
        for method, path, status in [("GET", "/missing", 404), ("POST", "/healthz", 405)]:
            result = await client.request(method, path)
            assert result.status_code == status
            assert result.headers["content-type"] == "application/problem+json"
