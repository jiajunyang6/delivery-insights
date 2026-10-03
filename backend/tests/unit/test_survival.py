from dataclasses import replace
from datetime import date, timedelta
from math import sqrt

import pytest
from tests.analytics_factory import dataset, pr
from tests.factories import at

from insights.analytics.efficiency import predictability, survival_cohort
from insights.analytics.stats import kaplan_meier


def test_km_five_hand_calculated_samples_tied_event_before_censor():
    km = kaplan_meier([(24, True), (24, False), (72, True), (168, False), (336, True)])
    assert km["n"] == 5 and km["events"] == 3
    assert km["s_at_hours"] == pytest.approx({"24": 0.8, "72": 8 / 15, "168": 8 / 15, "336": 0})
    assert km["median_hours"] == 336
    assert kaplan_meier([(1, False), (2, False)])["median_hours"] is None
    assert kaplan_meier([(1, False)])["s_at_hours"]["336"] == 1


def test_survival_ready_cohort_observation_boundary_and_early_closed_censor():
    prs = []
    for i in range(20):
        p = pr(i)
        f = replace(
            p.facts,
            ready_at=at(50),
            merged_at=at(51) if i < 10 else None,
            closed_at=at(50.5) if i >= 15 else None,
        )
        prs.append(replace(p, facts=f))
    d = dataset(prs)
    km = survival_cohort(d, d.current)
    assert km["n"] == 20 and km["events"] == 10 and km["median_hours"] == 1
    assert km["s_at_hours"]["24"] == pytest.approx(1 / 3)
    # A merge at the observation boundary is censored.
    boundary = replace(prs[0], facts=replace(prs[0].facts, merged_at=d.as_of))
    changed = replace(d, prs=(boundary, *prs[1:]))
    assert survival_cohort(changed, changed.current)["events"] == 9
    assert survival_cohort(dataset(prs[:19]), d.current) is None


def test_historical_p85_uses_independent_history_coverage_and_rate_gates():
    d = dataset([pr(i, cycle_hours=float(i + 1)) for i in range(50)])
    history = tuple(("a/b", d.start - timedelta(days=5), float(i)) for i in range(30))
    # Out-of-window records must not shift the historical threshold.
    history += (("a/b", d.start, 10000), ("a/b", d.start - timedelta(days=91), 10000))
    d = replace(d, history=history)
    metric = predictability(d, "fixed")["within_hist_p85"]
    assert metric["value"] == 24 / 50
    assert metric["extra"]["baseline_p85_hours"] == pytest.approx(24.65)
    assert metric["significant"] is None and metric["n"] == 50
    incomplete = replace(
        d, repos=(replace(d.repos[0], covered_since=d.start - timedelta(days=89)),)
    )
    missing = predictability(incomplete, "fixed")["within_hist_p85"]
    assert missing["value"] is None and missing["extra"]["reason"] == "baseline_not_covered"
    assert (
        predictability(replace(d, history=history[:29]), "fixed")["within_hist_p85"]["value"]
        is None
    )
    assert predictability(replace(d, prs=d.prs[:29]), "fixed")["within_hist_p85"]["value"] is None


def test_weekly_cv_uses_only_complete_calendar_weeks_population_std():
    base = dataset([])
    start = at(96)  # Monday January 5.
    prs = [
        replace(
            p := pr(w * 10 + i),
            facts=replace(p.facts, merged_at=start + timedelta(days=7 * w + 1)),
            human_activity_at=(start + timedelta(days=7 * w + 1),),
        )
        for w in range(4)
        for i in range(w + 1)
    ]
    d = replace(
        base,
        prs=tuple(prs),
        period_from=date(2026, 1, 5),
        period_to=date(2026, 2, 1),
        repos=(replace(base.repos[0], last_synced_at=start + timedelta(days=28)),),
    )
    result = predictability(d, "fixed")["weekly_throughput_cv"]
    assert result["n"] == 4 and result["value"] == pytest.approx(sqrt(1.25) / 2.5)
    assert result["significant"] is None
    shorter = replace(d, period_from=date(2026, 1, 6))
    assert predictability(shorter, "fixed")["weekly_throughput_cv"]["value"] is None
