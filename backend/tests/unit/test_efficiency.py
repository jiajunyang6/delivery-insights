from dataclasses import replace

import pytest
from tests.analytics_factory import dataset, pr
from tests.factories import at

from insights.analytics.efficiency import Measure, build_efficiency, compare, measures, rate


def test_retained_metrics_units_and_unsigned_size_share():
    prs = [pr(i) for i in range(30)]
    prs[0] = replace(prs[0], facts=replace(prs[0].facts, size_lines=600))
    result = build_efficiency(dataset(prs), "abc")
    assert result["merged_prs"]["value"] == 30
    assert result["cycle_time_p50_hours"]["value"] == 40
    assert result["pickup_p50_hours"]["value"] == 20
    assert result["pickup_p50_hours"]["unit"] == "hours"
    assert result["review_concentration_top_k"]["value"] == 1
    assert result["pr_size_p50_lines"]["unit"] == "lines"
    # One large PR is below the five-event rate gate, so the share is withheld.
    assert result["large_pr_share"]["value"] is None
    assert result["large_pr_share"]["significant"] is None


def test_sample_thresholds_and_missing_comparison():
    assert rate([1.0] * 4 + [0.0] * 26).value is None
    assert rate([1.0] * 5 + [0.0] * 24).value is None
    assert rate([1.0] * 5 + [0.0] * 25).value == pytest.approx(1 / 6)
    d = dataset([pr(i) for i in range(19)])
    d = replace(d, repos=(replace(d.repos[0], covered_since=at(48)),))
    result = build_efficiency(d, "abc")
    assert result["cycle_time_p50_hours"]["value"] is None
    for metric in result.values():
        assert metric["previous"] is None and metric["n_previous"] is None


def test_comparison_significance_and_zero_baseline():
    result = compare(
        Measure(20, 30, (20.0,) * 30),
        Measure(10, 30, (10.0,) * 30),
        unit="hours",
        name="test",
        params_hash="x",
        statistic="median",
    )
    assert result["significant"] and result["change_rel"] == 1
    result = compare(Measure(1, 30), Measure(0, 30), unit="share", name="x", params_hash="x")
    assert result["change_abs"] == 1 and result["change_rel"] is None


def test_as_of_and_closed_final_outcome():
    future = pr(1)
    d = dataset([future], repos=(replace(dataset([]).repos[0], last_synced_at=at(60)),))
    result = measures(d, d.current)
    assert result["merged_prs"].value == 0
    never_ready = replace(future, facts=replace(future.facts, ready_at=None))
    assert measures(dataset([never_ready]), dataset([never_ready]).current)["merged_prs"].value == 0
