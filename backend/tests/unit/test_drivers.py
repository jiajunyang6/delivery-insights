from dataclasses import replace

from tests.analytics_factory import dataset, pr
from tests.factories import at

from insights.analytics.drivers import build_drivers
from insights.analytics.timeline import Interval


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
            )
            p = replace(
                p,
                intervals=(
                    Interval("waiting_author", at(50), at(52 + bucket)),
                    Interval("waiting_reviewer", at(52 + bucket), at(56 + bucket)),
                ),
            )
            prs.append(p)
    result = build_drivers(dataset(prs))
    assert result["assignment"]["ratio"] == 3
    assert result["review_round_cost"]["re_review_wait_p50_hours"] == 4
    assert result["slowest_decile"] is None
    sparse = build_drivers(dataset(prs[:9]))
    assert sparse["assignment"]["assigned"]["pickup_p50_hours"] is None


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
    slowest = build_drivers(dataset(prs))["slowest_decile"]
    assert slowest["n"] == 11
    features = {f["feature"]: f for f in slowest["features"]}
    assert features["size_lines_p50"]["ratio"] == 10
    assert features["review_rounds_p50"]["ratio"] == 3
    assert features["external_share"]["slowest"] == 1
    assert features["external_share"]["ratio"] is None
    assert build_drivers(dataset(prs[:49]))["slowest_decile"] is None
    small = build_drivers(dataset(prs[:50]))["slowest_decile"]
    assert small["n"] == 5 and small["features"][0]["slowest"] is None
