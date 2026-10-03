from dataclasses import replace
from datetime import date

import pytest
from tests.analytics_factory import dataset, pr
from tests.factories import at, event, record
from tests.unit.test_ci import run

from insights.analytics.bottlenecks import at_risk, series
from insights.analytics.dataset import SnapshotParams, active_in
from insights.analytics.rows import build_pr_rows
from insights.analytics.snapshot import build_snapshot
from insights.analytics.timeline import Interval
from insights.narrative.evidence import extract_evidence
from insights_eval.generator import SyntheticRepo
from insights_eval.pipeline import dataset_from_repo


def period_records():
    def old(number, events):
        return record(
            number=number,
            source_id=f"PR{number}",
            url=f"https://github.com/a/b/pull/{number}",
            created_at=at(-200),
            updated_at=at(60),
            state="OPEN",
            merged_at=None,
            closed_at=None,
            events=events,
        )

    return (
        old(1, (event("comment", 20, "author"),)),
        old(2, (event("comment", 48, "author"),)),
        old(3, (event("comment", 55, "github-actions[bot]"),)),
        old(4, (event("comment", 55, None),)),
        replace(old(5, ()), created_at=at(50)),
        old(6, (event("review", 72),)),
        old(7, (event("comment", 80),)),
        old(8, (event("review", 60, state="COMMENTED"),)),
    )


def period_repo():
    return SyntheticRepo(
        "a/b", "main", period_records(), (), date(2026, 1, 3), date(2026, 1, 3), at(72)
    )


def test_human_activity_union_flows_through_all_dashboard_outputs():
    d = dataset_from_repo(period_repo(), covered_since=at(-5000))
    assert {p.number for p in d.flow} == {2, 5, 8}
    snapshot = build_snapshot(d, params=SnapshotParams(("a/b",), d.period_from, d.period_to))
    assert snapshot["meta"]["sample"]["open_prs_at_as_of"] == 3
    assert snapshot["meta"]["sample"]["human_reviews"] == 1
    assert snapshot["bottleneck_analysis"]["review_load"]["reviews"] == 1
    assert snapshot["bottleneck_analysis"]["review_queue"]["weeks"][0]["open_at_week_end"] == 2
    assert snapshot["at_risk_summary"]["total"] == 1
    assert {p["number"] for p in snapshot["at_risk_prs"]} == {2}
    assert {p["number"] for p in build_pr_rows(d)} == {2, 5, 8}
    evidence = next(e for e in extract_evidence(snapshot) if e["id"] == "E25")
    assert evidence["value"] == 1
    assert set(evidence["examples"]) == {
        "https://github.com/a/b/pull/2",
    }


@pytest.mark.parametrize(
    "created, activity, expected",
    [
        (48, (), True),
        (72, (), False),
        (0, (48,), True),
        (0, (71.99,), True),
        (0, (47.99,), False),
        (0, (72,), False),
        (0, (80,), False),
        (0, (), False),
    ],
)
def test_period_boundaries_do_not_leak_future_activity(created, activity, expected):
    p = replace(pr(1), created_at=at(created), human_activity_at=tuple(at(t) for t in activity))
    assert active_in(p, dataset([p]).current) is expected


def test_previous_risk_uses_previous_activity_and_partial_day_uses_watermark():
    def waiting(number, activity):
        p = pr(number, ready_at=at(-200), merged_at=None, end_at=None)
        return replace(
            p,
            created_at=at(-210),
            human_activity_at=(at(activity),),
            intervals=(Interval("waiting_reviewer", at(-200), None),),
        )

    d = dataset([waiting(1, 30), waiting(2, 55), waiting(3, 65)])
    assert {r["number"] for r in at_risk(d, at=d.start, window=d.previous)} == {1}
    d = replace(d, observation_time=at(60))
    assert {r["number"] for r in at_risk(d, at=d.as_of)} == {2}


def test_inactive_bot_merged_pr_and_ci_are_excluded_with_consistent_totals():
    active = pr(1)
    inactive = replace(pr(2), human_activity_at=())
    d = dataset(
        [active, inactive],
        ci_runs=(("a/b", run(pr_numbers=(1, 2))), ("a/b", run(2, pr_numbers=(2,)))),
    )
    s = build_snapshot(
        d, params=SnapshotParams(("a/b",), d.period_from, d.period_to, ci_source="actions")
    )
    assert s["efficiency"]["merged_prs"]["value"] == 1
    assert s["time_ledger"]["merged_prs"] == 1
    assert s["time_ledger"]["total_pr_hours"] == 30
    assert sum(w["merged"] for w in s["series"]["current"]) == 1
    assert s["bottleneck_analysis"]["ci"]["queue_p50_minutes"]["n"] == 1
    assert s["bottleneck_analysis"]["ci"]["runs_per_pr_p50"]["n"] == 1
    rows = build_pr_rows(d)
    assert [r["number"] for r in rows] == [1]
    assert sum(sum(r["ledger_hours"].values()) for r in rows) == 30


def test_weekly_series_keeps_the_full_selected_period_cohort():
    # Human activity in week one, automated merge in week two: still in this report.
    p = replace(pr(1, merged_at=at(24 * 9)), created_at=at(-200), human_activity_at=(at(24),))
    d = dataset(
        [p], period_from=date(2026, 1, 1), period_to=date(2026, 1, 14), observation_time=at(24 * 14)
    )
    assert sum(w["merged"] for w in series(d, d.current)) == 1
