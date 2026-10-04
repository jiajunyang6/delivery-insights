from dataclasses import replace
from datetime import date

import pytest
from tests.analytics_factory import dataset, pr
from tests.factories import at

from insights.analytics.bottlenecks import (
    at_risk,
    attribution,
    locations,
    review_queue,
    time_ledger,
    what_if,
)
from insights.analytics.dataset import Baseline, RepoData
from insights.analytics.timeline import Interval


def test_ledger_weighted_locations_and_other_union():
    prs = [pr(i, locations=("a", "b")) for i in range(5)]
    d = dataset(prs)
    ledger = time_ledger(d)
    locs = locations(d, [], ledger)
    assert ledger["total_pr_hours"] == 150
    assert sum(s["pr_hours"] for s in ledger["states"].values()) == 150
    assert len(locs) == 1 and locs[0]["location"] == "other"
    assert locs[0]["merged_prs"] == 5
    assert locs[0]["waiting_reviewer_pr_hours"] == 100


def test_what_if_and_null_zero_cases():
    d = dataset([pr(i) for i in range(20)])
    result = what_if(d, "pickup")
    assert result["cycle_p50_before_hours"] == 40
    assert result["cycle_p50_after_hours"] == 28
    assert result["change_rel"] == pytest.approx(-0.3)
    assert result["affected_prs"] == 20
    assert what_if(dataset([pr(1)]), "pickup") is None
    zero = [replace(p, facts=replace(p.facts, cycle_hours=0)) for p in d.prs]
    assert what_if(dataset(zero), "pickup") is None
    assert what_if(d, "pickup", "unknown")["affected_prs"] == 0


def test_attribution_conserves_net_reviewer_increase():
    current = [pr(i, reviewer=23, author=7, locations=("a",)) for i in range(20)]
    previous = [pr(i + 20, offset=0, locations=("a",)) for i in range(20)]
    d = dataset(current + previous)
    ledger = time_ledger(d)
    locs = [
        {
            "location": "A",
            "waiting_reviewer_pr_hours": 100,
            "previous_waiting_reviewer_pr_hours": 40,
        },
        {
            "location": "B",
            "waiting_reviewer_pr_hours": 100,
            "previous_waiting_reviewer_pr_hours": 120,
        },
    ]
    # Isolate the specified +3/-1 location example and a 0.5 reviewer share of increase.
    ledger["states"]["waiting_reviewer"].update(pr_hours=440, previous_pr_hours=400)
    ledger["states"]["waiting_author"].update(pr_hours=140, previous_pr_hours=100)
    result = attribution(d, ledger, locs)
    assert result["states"]["waiting_reviewer"]["share_of_increase"] == 0.5
    assert result["locations"][0]["share_of_increase"] == 0.5
    assert result["locations"][1]["share_of_increase"] == 0
    assert sum(c["share_of_increase"] for c in result["states"].values()) == pytest.approx(1)
    ledger["states"]["waiting_reviewer"].update(pr_hours=400)
    result = attribution(d, ledger, locs)
    assert all(p["share_of_increase"] == 0 for p in result["locations"])


def test_risk_repo_specific_fallback_and_closed_period():
    p = pr(1)
    opened = replace(
        p,
        facts=replace(p.facts, merged_at=None, end_at=None),
        intervals=(Interval("waiting_reviewer", at(0), None),),
    )
    second = replace(opened, pr_id=2, number=2, repo="c/d")
    bases = tuple(Baseline("a/b", "waiting_reviewer", at(10), 100.0) for _ in range(30))
    bases += tuple(Baseline("c/d", "waiting_reviewer", at(-2400), 20.0) for _ in range(30))
    d = dataset(
        [opened, second],
        repos=(RepoData("a/b", 1, at(-5000), at(72)), RepoData("c/d", 1, at(-5000), at(72))),
        baselines=bases,
    )
    risks = at_risk(d, at=at(72))
    assert len(risks) == 1 and risks[0]["repo"] == "c/d"
    assert risks[0]["baseline_source"] == "180d" and risks[0]["severity"] == "critical"
    d = replace(d, baselines=())
    assert len(at_risk(d, at=at(72))) == 2
    paused = replace(opened, intervals=(Interval("closed", at(50), at(80)),))
    assert not at_risk(dataset([paused]), at=at(72))
    draft = replace(opened, is_draft=True)
    assert not at_risk(dataset([draft]), at=at(72), exclude_current_drafts=True)


def test_week_clipping_and_shift_boundary():
    d = dataset([])
    queue = review_queue(d, d.current)
    assert len(queue["weeks"]) == 1 and queue["weeks"][0]["days"] == 1
    assert queue["net_inflow_share"] is None
    ledger = time_ledger(d)
    ledger["states"]["waiting_ci"]["change_pp"] = 5


def half_unreviewed_dataset():
    prs = []
    for week in range(4):
        for index in range(20):
            p = pr(week * 20 + index, offset=96 + week * 168)
            if index >= 10:
                p = replace(
                    p,
                    facts=replace(p.facts, first_review_at=None, merged_at=None, end_at=None),
                    intervals=(Interval("waiting_reviewer", p.facts.ready_at, None),),
                    human_activity_at=(),
                )
            prs.append(p)
    return dataset(
        prs,
        repos=(RepoData("a/b", 1, at(-5000), at(744)),),
        period_from=date(2026, 1, 5),
        period_to=date(2026, 2, 1),
    )


def test_review_queue_half_of_new_prs_are_unserved():
    d = half_unreviewed_dataset()
    queue = review_queue(d, d.current)
    assert [week["inflow"] for week in queue["weeks"]] == [20] * 4
    assert [week["outflow"] for week in queue["weeks"]] == [10] * 4
    assert queue["net_inflow_share"] == 0.5
    assert queue["weeks_inflow_exceeds_outflow"] == 4
