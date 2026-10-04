from dataclasses import replace
from datetime import date

import pytest
from tests.analytics_factory import dataset, pr
from tests.factories import at
from tests.unit.test_snapshot import params

from insights.analytics.bottlenecks import at_risk, locations, review_queue, time_ledger, what_if
from insights.analytics.dataset import Baseline, Review, Window
from insights.analytics.efficiency import measures
from insights.analytics.snapshot import build_snapshot
from insights.analytics.timeline import Interval


def test_mature_cohort_includes_boundary_and_not_partial_followup():
    prs = []
    for i in range(30):
        p = pr(i)
        p = replace(p, facts=replace(p.facts, ready_at=at(0), merged_at=at(72)))
        prs.append(p)
    d = dataset(prs, period_from=date(2026, 1, 1), period_to=date(2026, 1, 3))
    m = measures(d, d.current)["merged_within_n_days"]
    assert m.value == 1 and m.n == 30
    assert m.extra["n_days"] == 3
    d = replace(d, repos=(replace(d.repos[0], last_synced_at=at(71)),))
    assert measures(d, d.current)["merged_within_n_days"].n == 0


def test_excluded_priority_and_zero_coding_waiting_share():
    originals = [pr(i) for i in range(20)]
    originals = [replace(p, facts=replace(p.facts, coding_hours=None)) for p in originals]
    d = dataset(originals)
    assert measures(d, d.current)["waiting_share"].value == pytest.approx(25 / 30)
    bot = replace(pr(100), facts=replace(pr(100).facts, is_bot_author=True, is_backport=True))
    backport = replace(pr(101), facts=replace(pr(101).facts, is_backport=True, ready_at=None))
    draft = replace(
        pr(102), facts=replace(pr(102).facts, ready_at=None, merged_at=None, closed_at=at(64))
    )
    payload = build_snapshot(dataset([bot, backport, draft]), params=params(d))
    assert payload["efficiency"]["merged_prs"]["value"] == 0
    assert payload["meta"]["sample"]["merged_prs"] == 0
    assert payload["time_ledger"]["total_pr_hours"] == 0


def test_other_union_counts_inflow_outflow_and_risks_once():
    originals = []
    for i in range(5):
        p = pr(i, offset=38, reviewer=5, author=1, merge=1, locations=("a", "b"))
        originals.append(p)
    d = dataset(originals)
    risks = [{"repo": "a/b", "number": p.number, "locations": ["a", "b"]} for p in originals]
    locs = locations(d, risks, time_ledger(d))
    assert len(locs) == 1
    assert (locs[0]["inflow"], locs[0]["outflow"], locs[0]["at_risk_prs"]) == (5, 5, 5)


def test_queue_ready_pre_review_and_paused_pr():
    p = pr(1, offset=38, reviewer=5)
    p = replace(
        p,
        facts=replace(p.facts, first_review_at=at(40), merged_at=None, end_at=None),
        intervals=(Interval("waiting_reviewer", at(48), None),),
    )
    no_review = replace(
        p,
        pr_id=2,
        facts=replace(p.facts, first_review_at=None),
        intervals=(
            Interval("waiting_reviewer", at(48), at(70)),
            Interval("closed", at(70), at(75)),
            Interval("waiting_reviewer", at(75), None),
        ),
    )
    d = dataset([p, no_review], reviews=(Review("a", at(40), 1),))
    week = review_queue(d, Window(at(48), at(72)))["weeks"][0]
    assert week["inflow"] == 2 and week["outflow"] == 1
    assert week["open_at_week_end"] == 0


def test_risk_90d_and_strict_percentile_threshold():
    p = pr(1)
    p = replace(
        p,
        facts=replace(p.facts, merged_at=None, end_at=None),
        intervals=(Interval("waiting_reviewer", at(22), None),),
    )
    bases = tuple(Baseline("a/b", "waiting_reviewer", at(10), 50) for _ in range(30))
    d = dataset([p], baselines=bases)
    assert not at_risk(d, at=at(72))
    risk = at_risk(d, at=at(72.1))[0]
    assert risk["baseline_source"] == "90d" and risk["severity"] == "critical"
    assert what_if(dataset([pr(i) for i in range(30)]), "merge")["affected_prs"] == 0
