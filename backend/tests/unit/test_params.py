"""Query parameter parsing, horizon limits and ETag matching."""

from datetime import UTC, datetime

import pytest
from starlette.datastructures import QueryParams
from tests.factories import at

from insights.api.errors import ProblemError
from insights.api.params import parse_params
from insights.config import Settings
from insights.snapshots.caching import matches_etag


@pytest.mark.parametrize(
    ("query", "param"),
    [
        ("", "repo"),
        ("repo=a/b&repo=a/b", "repo"),
        ("repo=../../secret", "repo"),
        ("repo=a/b&from=2026-1-01", "from"),
        ("repo=a/b&from=2026-02-30", "from"),
        ("repo=a/b&from=2026-01-03&to=2026-01-01", "from"),
        ("repo=a/b&to=2027-01-01", "to"),
        ("repo=a/b&from=2020-01-01", "from"),
    ],
)
def test_parameter_errors_are_sanitized(query, param):
    with pytest.raises(ProblemError) as captured:
        parse_params(QueryParams(query), Settings(tracked_repos="a/b"), at(24))
    assert captured.value.status == 422
    assert param in {e["param"] for e in captured.value.errors}
    assert "../../secret" not in captured.value.detail


def test_defaults_configured_spelling_and_combined_errors():
    settings = Settings(tracked_repos="A/B,a/C")
    result = parse_params(QueryParams("repo=a/b"), settings, at(24))
    assert result.repos == ("A/B",) and (result.period_to - result.period_from).days == 29
    with pytest.raises(ProblemError) as captured:
        parse_params(QueryParams("repo=a/b&from=2020-01-01&to=2027-01-01"), settings, at(24))
    assert len(captured.value.errors) == 3
    with pytest.raises(ProblemError) as captured:
        parse_params(QueryParams("repo=x/y"), settings, at(24))
    assert captured.value.status == 403


@pytest.mark.parametrize("backfill_days", [30, 90, 180])
def test_ninety_day_preset_respects_configured_horizon(backfill_days):
    settings = Settings(tracked_repos="a/b", backfill_days=backfill_days)
    query = QueryParams("repo=a/b&from=2026-07-06&to=2026-10-03")
    now = datetime(2026, 10, 3, tzinfo=UTC)
    if backfill_days < 89:
        with pytest.raises(ProblemError) as captured:
            parse_params(query, settings, now)
        assert captured.value.errors == [
            {"param": "from", "message": "must be within the configured backfill horizon"}
        ]
    else:
        params = parse_params(query, settings, now)
        assert (params.period_to - params.period_from).days + 1 == 90


def test_etag_matching():
    assert matches_etag(' "a", W/"b" ', '"b"')
    assert matches_etag("*", '"anything"')
    assert not matches_etag(None, '"b"')
    assert not matches_etag('"a"', '"b"')
