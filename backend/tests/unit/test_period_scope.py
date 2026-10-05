from dataclasses import replace
from datetime import date

import pytest
from tests.analytics_factory import dataset, pr
from tests.factories import at, event, record
from tests.unit.test_ci import run

from insights.analytics.bottlenecks import series
from insights.analytics.dataset import RepoData, SnapshotParams, Window, active_in
from insights.analytics.snapshot import build_snapshot
from insights_eval.generator import SyntheticRepo
from insights_eval.pipeline import dataset_from_repo


def period_records():
    def old(number, events):
        return record(
            number=number,
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


def test_human_activity_union_selects_the_period_cohort():
    d = dataset_from_repo(period_repo(), covered_since=at(-5000))
    # Bot and anonymous activity, or activity outside the period, does not make a PR active.
    assert {p.number for p in d.flow} == {2, 5, 8}
    snapshot = build_snapshot(d, params=SnapshotParams(("a/b",), d.period_from, d.period_to))
    assert snapshot["efficiency"]["review_concentration_top_k"]["n"] == 1


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
    assert s["bottleneck_analysis"]["ci"]["flaky_rerun_rate"]["n"] == 1


def test_weekly_series_keeps_the_full_selected_period_cohort():
    # Human activity in week one, automated merge in week two: still in this report.
    p = replace(pr(1, merged_at=at(24 * 9)), created_at=at(-200), human_activity_at=(at(24),))
    d = dataset(
        [p],
        period_from=date(2026, 1, 1),
        period_to=date(2026, 1, 14),
        repos=(replace(dataset([]).repos[0], last_synced_at=at(24 * 14)),),
    )
    assert sum(w["merged"] for w in series(d, d.current)) == 1


def test_window_cohorts_reuse_flow_and_review_objects():
    d = dataset([pr(1)])
    current = d.current
    flow = d.flow_in(current)
    reviews = d.reviews_in(current)
    assert flow and reviews
    assert d.flow_in(Window(current.start, current.end)) is flow
    assert d.reviews_in(current) is reviews
    assert d.flow_in(d.previous) is not flow
    assert d.reviews_in(d.previous) == ()


def test_repo_and_replaced_datasets_have_independent_caches():
    d = dataset(
        [pr(1), replace(pr(2), repo="c/d")],
        repos=(RepoData("a/b", 1, at(-5000), at(72)), RepoData("c/d", 1, at(-5000), at(72))),
    )
    d.flow_in(d.current)
    d.reviews_in(d.current)
    scoped = replace(
        d,
        prs=tuple(p for p in d.prs if p.repo == "a/b"),
        reviews=tuple(r for r in d.reviews if r.pr_id == 1),
    )
    empty = replace(d, prs=())
    assert not scoped.cohort_cache and not empty.cohort_cache
    assert scoped.cohort_cache is not d.cohort_cache
    assert empty.cohort_cache is not d.cohort_cache
    assert [p.pr_id for p in scoped.flow] == [1]
    assert [r.pr_id for r in scoped.reviews_in(scoped.current)] == [1]
    assert not empty.flow and not empty.reviews_in(empty.current)
    same = replace(d)
    assert same == d
    assert "cohort_cache" not in repr(d)


def test_activity_is_sorted_once_and_binary_lookup_keeps_half_open_boundaries():
    p = replace(pr(1), created_at=at(-100), human_activity_at=(at(80), at(72), at(48), at(20)))
    assert p.human_activity_at == (at(20), at(48), at(72), at(80))
    for start, end in [(48, 72), (49, 72), (72, 80), (81, 90), (48, 48)]:
        window = Window(at(start), at(end))
        assert active_in(p, window) == any(window.contains(t) for t in p.human_activity_at)


def test_snapshot_is_unchanged_by_cache_history():
    d = dataset_from_repo(period_repo(), covered_since=at(-5000))
    params = SnapshotParams(("a/b",), d.period_from, d.period_to)
    expected = build_snapshot(d, params=params)
    d.reviews_in(d.previous)
    d.flow_in(Window(at(-100), at(100)))
    assert build_snapshot(d, params=params) == expected
    assert build_snapshot(replace(d), params=params) == expected
