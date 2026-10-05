from copy import deepcopy

import pytest
from tests.narrative_factory import golden

from insights.analytics.insight import insight_view
from insights.api.schemas import Insight


def ledger(snapshot, shares, previous, total=100.0):
    snapshot["time_ledger"]["total_pr_hours"] = total
    for state, share in shares.items():
        snapshot["time_ledger"]["states"][state].update(
            share=share,
            previous_share=previous[state],
            change_pp=round((share - previous[state]) * 100, 2),
            pr_hours=share * total,
            previous_pr_hours=previous[state] * total,
        )
    return snapshot


@pytest.fixture
def snapshot():
    data = deepcopy(golden())
    data["meta"]["comparison_available"] = True
    data["efficiency"]["merged_prs"].update(value=203, previous=181)
    data["efficiency"]["cycle_time_p50_hours"].update(
        value=59.04, previous=123.67, change_rel=-0.5226, significant=True, n=203
    )
    return data


def test_view_is_valid_contract_and_hides_internal_evidence(snapshot):
    view = insight_view(snapshot)
    Insight.model_validate(view)
    assert set(view) == {
        "snapshot_id",
        "repo",
        "period",
        "as_of",
        "comparison_available",
        "insight",
        "time_ledger",
        "links",
    }
    assert view["links"]["narrative"] == f"/v1/snapshots/{snapshot['snapshot_id']}/narrative"
    assert view == insight_view(deepcopy(snapshot))


def test_same_state_largest_and_most_changed_is_stated_once(snapshot):
    shares = {"waiting_reviewer": 0.39, "waiting_author": 0.10, "waiting_merge": 0.51}
    previous = {"waiting_reviewer": 0.35, "waiting_author": 0.05, "waiting_merge": 0.60}
    insight = insight_view(ledger(snapshot, shares, previous))["insight"]
    assert insight["largest_wait"] == {
        "state": "waiting_merge",
        "share": 0.51,
        "previous_share": 0.60,
    }
    assert insight["largest_change"] == {"state": "waiting_merge", "change_pp": -9.0}
    assert insight["statement"] == (
        "Across 203 merged PRs, 51% of post-ready waiting time was spent waiting to merge after "
        "approval (previous period 60%, down 9.0 pp). Median cycle time fell 52% to 59.0 h."
    )


def test_different_most_changed_state_gets_its_own_sentence(snapshot):
    shares = {"waiting_reviewer": 0.45, "waiting_author": 0.05, "waiting_merge": 0.50}
    previous = {"waiting_reviewer": 0.30, "waiting_author": 0.10, "waiting_merge": 0.60}
    statement = insight_view(ledger(snapshot, shares, previous))["insight"]["statement"]
    assert "(previous period 60%)." in statement
    assert "The largest shift: time waiting on reviewers rose 15.0 pp to 45%." in statement


def test_no_comparison_no_waits_and_too_few_prs(snapshot):
    snapshot["meta"]["comparison_available"] = False
    snapshot["efficiency"]["merged_prs"].update(value=3, previous=None)
    snapshot["efficiency"]["cycle_time_p50_hours"].update(
        value=None, previous=None, change_rel=None, significant=None
    )
    shares = dict.fromkeys(("waiting_reviewer", "waiting_author", "waiting_merge"), 0.0)
    view = insight_view(ledger(snapshot, shares, shares, total=0.0))
    insight = view["insight"]
    assert insight["largest_wait"] is None and insight["largest_change"] is None
    assert insight["statement"] == (
        "3 merged PRs recorded no post-ready waiting time in this period. "
        "Too few merged PRs for a median cycle time."
    )
    Insight.model_validate(view)
