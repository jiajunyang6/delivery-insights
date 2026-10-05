"""Time ledger, locations, attribution, weekly clipping and review queue."""

from dataclasses import replace
from datetime import date

import pytest
from tests.analytics_factory import dataset, pr
from tests.factories import at

from insights.analytics.bottlenecks import attribution, locations, review_queue, time_ledger
from insights.analytics.dataset import RepoData
from insights.analytics.timeline import Interval


def test_ledger_weighted_locations_and_other_union():
    prs = [pr(i, locations=("a", "b")) for i in range(5)]
    d = dataset(prs)
    ledger = time_ledger(d)
    locs = locations(d)
    assert ledger["total_pr_hours"] == 150
    assert sum(s["pr_hours"] for s in ledger["states"].values()) == 150
    assert len(locs) == 1 and locs[0]["location"] == "other"
    assert locs[0]["merged_prs"] == 5
    assert locs[0]["waiting_reviewer_pr_hours"] == 100


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


def test_week_clipping_counts_a_partial_week():
    d = dataset([])
    assert review_queue(d, d.current) == {"weeks_total": 1, "weeks_inflow_exceeds_outflow": 0}


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
    assert review_queue(d, d.current) == {"weeks_total": 4, "weeks_inflow_exceeds_outflow": 4}
