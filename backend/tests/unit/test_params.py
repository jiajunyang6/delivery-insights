import base64
from dataclasses import replace

import orjson
import pytest
from starlette.datastructures import QueryParams
from tests.factories import at

from insights.api.caching import decode_cursor, encode_cursor, matches_etag
from insights.api.errors import ProblemError
from insights.api.params import PrFilters, parse_filters, parse_params, validate_job_id
from insights.config import Settings


@pytest.mark.parametrize(
    ("query", "param"),
    [
        ("", "repo"),
        ("repo=a/b&org=a", "repo"),
        ("repo=../../secret", "repo"),
        ("org=bad.org", "org"),
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


def test_defaults_case_dedup_org_and_combined_errors():
    settings = Settings(tracked_repos="A/B,a/C", max_repos_per_request=2)
    result = parse_params(QueryParams("repo=a/b&repo=A/B"), settings, at(24))
    assert result.repos == ("A/B",) and (result.period_to - result.period_from).days == 29
    assert parse_params(QueryParams("org=A"), settings, at(24)).repos == ("A/B", "a/C")
    with pytest.raises(ProblemError) as captured:
        parse_params(QueryParams("repo=a/b&from=2020-01-01&to=2027-01-01"), settings, at(24))
    assert len(captured.value.errors) == 3
    with pytest.raises(ProblemError) as captured:
        parse_params(QueryParams("org=unknown"), settings, at(24))
    assert captured.value.status == 403


@pytest.mark.parametrize(
    "query",
    [
        "at_risk=1",
        "at_risk=true&status=closed",
        "state=waiting_ci",
        "status=unknown",
        "status=open&state=closed",
        "location=%00",
        "limit=0",
        "limit=201",
        "limit=1.0",
        "cursor=x=",
        "location=",
    ],
)
def test_filter_validation(query):
    with pytest.raises(ProblemError):
        parse_filters(QueryParams(query))


def test_filters_etags_and_cursors():
    filters, limit, cursor = parse_filters(QueryParams("at_risk=true"))
    assert filters.status == "open" and filters.at_risk and limit == 50 and cursor is None
    token = encode_cursor("s_" + "a" * 16, 50, filters)
    assert decode_cursor(token, "s_" + "a" * 16, filters) == 50
    with pytest.raises(ProblemError, match="Invalid parameter"):
        decode_cursor(token, "s_" + "b" * 16, filters)
    with pytest.raises(ProblemError):
        decode_cursor(token, "s_" + "a" * 16, replace(filters, location="area-A"))
    for payload in (
        [],
        {"v": True, "sid": "x", "o": 0, "f": ""},
        {"v": 1, "sid": "x", "o": -1, "f": ""},
    ):
        bad = base64.urlsafe_b64encode(orjson.dumps(payload)).decode().rstrip("=")
        with pytest.raises(ProblemError):
            decode_cursor(bad, "x", PrFilters())
    assert matches_etag(' "a", W/"b" ', '"b"')
    assert matches_etag("*", '"anything"')
    assert not matches_etag(None, '"b"')
    assert not matches_etag('"a"', '"b"')
    with pytest.raises(ProblemError):
        validate_job_id("123456781234123412341234567890AB")
