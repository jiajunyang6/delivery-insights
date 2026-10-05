from dataclasses import replace

from tests.analytics_factory import dataset, pr
from tests.factories import at
from tests.unit.test_snapshot import params

from insights.analytics.bottlenecks import locations, review_queue
from insights.analytics.dataset import Review, Window
from insights.analytics.snapshot import build_snapshot
from insights.analytics.timeline import Interval


def test_bot_backport_and_draft_prs_are_excluded():
    d = dataset([pr(i) for i in range(20)])
    bot = replace(pr(100), facts=replace(pr(100).facts, is_bot_author=True, is_backport=True))
    backport = replace(pr(101), facts=replace(pr(101).facts, is_backport=True, ready_at=None))
    draft = replace(
        pr(102), facts=replace(pr(102).facts, ready_at=None, merged_at=None, closed_at=at(64))
    )
    payload = build_snapshot(dataset([bot, backport, draft]), params=params(d))
    assert payload["efficiency"]["merged_prs"]["value"] == 0
    assert payload["meta"]["sample"]["merged_prs"] == 0
    assert payload["time_ledger"]["total_pr_hours"] == 0


def test_other_union_counts_multi_location_prs_once():
    originals = [
        pr(i, offset=38, reviewer=5, author=1, merge=1, locations=("a", "b")) for i in range(5)
    ]
    locs = locations(dataset(originals))
    assert len(locs) == 1
    assert locs[0]["merged_prs"] == 5 and locs[0]["waiting_reviewer_pr_hours"] == 25


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
    # Two ready arrivals, one first review clamped to the ready time: inflow exceeds outflow.
    queue = review_queue(d, Window(at(48), at(72)))
    assert queue == {"weeks_total": 1, "weeks_inflow_exceeds_outflow": 1}
