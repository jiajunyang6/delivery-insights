from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.datastructures import QueryParams

from insights.api.deps import get_now, get_session
from insights.api.errors import ProblemError
from insights.api.params import parse_params
from insights.api.schemas import RepoList
from insights.config import Settings
from insights.main import create_app


@pytest.mark.parametrize("backfill_days", [30, 90, 180, 365])
async def test_repo_date_limits_match_parameter_validation(backfill_days):
    settings = Settings(tracked_repos="a/b", backfill_days=backfill_days)
    now = datetime(2026, 10, 3, 23, 59, tzinfo=UTC)
    app = create_app(settings)
    app.state.redis = AsyncMock()
    app.state.redis.incr.return_value = 1
    session = AsyncMock()
    session.scalars.side_effect = [[], []]
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_now] = lambda: now
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/repos")
    assert response.status_code == 200
    limits = RepoList.model_validate(response.json()).date_limits
    assert limits.latest_to == date(2026, 10, 3)
    assert limits.max_days == 366
    valid = f"repo=a/b&from={limits.earliest_from}&to={limits.latest_to}"
    assert parse_params(QueryParams(valid), settings, now).period_from == limits.earliest_from
    invalid = f"repo=a/b&from={limits.earliest_from}&to=2026-10-04"
    with pytest.raises(ProblemError):
        parse_params(QueryParams(invalid), settings, now)
