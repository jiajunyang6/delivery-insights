from dataclasses import replace

import pytest
from tests.analytics_factory import dataset, pr
from tests.factories import at
from tests.unit.test_classify import item, linked

from insights.analytics.drivers import build_drivers
from insights.analytics.stats import spearman
from insights.analytics.timeline import Interval
from insights.domain import Actor


def test_author_wip_ties_half_open_end_unknown_author_and_flow_filter():
    a = item(1, created_at=at(0), merged_at=at(2))
    b = item(2, created_at=at(0), merged_at=at(5), author=Actor("AUTHOR", False))
    c = item(3, created_at=at(2), merged_at=at(10))
    unknown = item(4, author=Actor(None, False))
    bot = item(5)
    values = [
        replace(p, facts=replace(p.facts, end_at=p.record.merged_at)) for p in (a, b, c, unknown)
    ]
    values.append(replace(bot, facts=replace(bot.facts, is_bot_author=True)))
    result = linked(*values)
    assert [result[n].author_open_prs_at_ready for n in (1, 2, 3, 4, 5)] == [1, 1, 1, None, None]
    assert linked(*reversed(values)) == result


def test_assignment_round_buckets_wip_and_submission_groups():
    prs = []
    for bucket in range(4):
        for i in range(10):
            p = pr(
                bucket * 10 + i,
                review_rounds=bucket,
                cycle_hours=20 + bucket * 10,
                review_requested_before_first_review=bucket == 0,
                pickup_hours=2 if bucket == 0 else 6,
                author_open_prs_at_ready=bucket,
                ready_weekday=bucket,
                ready_hour=bucket * 6,
            )
            p = replace(
                p,
                intervals=(
                    Interval("waiting_author", at(50), at(52 + bucket)),
                    Interval("waiting_reviewer", at(52 + bucket), at(56 + bucket)),
                ),
            )
            prs.append(p)
    result = build_drivers(dataset(prs), 6)
    assert result["assignment"]["ratio"] == 3
    assert result["review_round_cost"]["hours_per_extra_round"] == 10
    assert result["review_round_cost"]["re_review_wait_p50_hours"] == 4
    assert [b["n"] for b in result["author_wip"]["buckets"]] == [10, 20, 10]
    assert result["author_wip"]["spearman"] == pytest.approx(1)
    assert [v["n"] for v in result["submit_timing"]["by_weekday"]] == [10, 10, 10, 10, 0, 0, 0]
    assert [v["hours"] for v in result["submit_timing"]["by_hour_block"]] == [
        "00-05",
        "06-11",
        "12-17",
        "18-23",
    ]
    assert result["slowest_decile"] is None
    sparse = build_drivers(dataset(prs[:9]), 2)
    assert sparse["assignment"]["assigned"]["pickup_p50_hours"] is None
    assert sparse["author_wip"]["spearman"] is None


def test_slowest_decile_ceil_fixed_features_zero_baseline_and_sample_gate():
    prs = [
        pr(
            i,
            cycle_hours=i + 1,
            size_lines=1000 if i >= 90 else 100,
            external_contributor=i >= 90,
            review_rounds=3 if i >= 90 else 1,
            review_requested_before_first_review=i < 90,
            locations=("area-A", "area-B") if i >= 90 else ("area-A",),
        )
        for i in range(101)
    ]
    slowest = build_drivers(dataset(prs), 20)["slowest_decile"]
    assert slowest["n"] == 11
    features = {f["feature"]: f for f in slowest["features"]}
    assert features["size_lines_p50"]["ratio"] == 10
    assert features["review_rounds_p50"]["ratio"] == 3
    assert features["external_share"]["slowest"] == 1
    assert features["external_share"]["ratio"] is None
    assert build_drivers(dataset(prs[:49]), 20)["slowest_decile"] is None
    small = build_drivers(dataset(prs[:50]), 20)["slowest_decile"]
    assert small["n"] == 5 and small["features"][0]["slowest"] is None


def test_spearman_average_tied_ranks_and_constant_inputs():
    assert spearman([1, 1, 2, 2, 3] * 2, [10, 10, 20, 20, 30] * 2) == pytest.approx(1)
    assert spearman(list(range(10)), list(reversed(range(10)))) == pytest.approx(-1)
    assert spearman([1] * 10, list(range(10))) is None
    with pytest.raises(ValueError):
        spearman([1], [])
