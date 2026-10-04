from dataclasses import replace

from tests.analytics_factory import dataset, pr
from tests.factories import at

from insights.analytics.bottlenecks import at_risk
from insights.analytics.snapshot import build_pr_rows
from insights.analytics.timeline import Interval


def test_populations_historical_open_and_risk_equality():
    merged = pr(1)
    lost = replace(
        pr(2), facts=replace(pr(2).facts, merged_at=None, closed_at=at(64), close_class="abandoned")
    )
    future = pr(3, offset=24)
    future = replace(
        future,
        facts=replace(future.facts, merged_at=at(100), end_at=at(100)),
        intervals=(Interval("waiting_reviewer", at(10), at(100)),),
    )
    draft = replace(future, pr_id=4, number=4, is_draft=True)
    never = replace(pr(5), facts=replace(pr(5).facts, ready_at=None))
    d = dataset([merged, lost, future, draft, never])
    rows = build_pr_rows(d)
    assert [r["status"] for r in rows] == ["merged", "closed", "open", "open"]
    assert rows[2]["merged_at"] is None
    assert rows[2]["current_state_age_hours"] == 62
    assert sum(r["at_risk"] is not None for r in rows) == len(at_risk(d, at=d.as_of)) == 2
    current = replace(d, current_day=True)
    assert sum(r["at_risk"] is not None for r in build_pr_rows(current)) == 1
    assert rows[0]["ledger_hours"] == {
        "waiting_reviewer": 20,
        "waiting_author": 5,
        "waiting_ci": 0,
        "waiting_merge": 5,
    }
