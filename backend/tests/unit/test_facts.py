import pytest
from tests.factories import at, event, record

from insights.analytics.facts import compute_facts
from insights.analytics.timeline import build_timeline, pr_input
from insights.domain import Actor


def facts(pr):
    timeline = build_timeline(pr_input(pr), pr.events, (), at(30))
    return compute_facts(
        pr, pr.events, timeline, default_branch="main", location_rules=(), now=at(30)
    )


def test_all_milestones_and_counts():
    pr = record(
        events=(
            event("commit", -4),
            event("comment", 1),
            event("review_requested", 1),
            event("review", 2, state="CHANGES_REQUESTED"),
            event("commit", 3),
            event("review", 4, state="COMMENTED"),
            event("force_push", 5),
            event("review", 6),
            event("review", 8, "second"),
            event("commit", 9),
            event("force_push", 9.5),
            event("commit", 11),
        )
    )
    f = facts(pr)
    assert (f.coding_hours, f.pickup_hours, f.review_hours, f.merge_hours, f.cycle_hours) == (
        4,
        2,
        4,
        4,
        14,
    )
    assert f.review_rounds == 2
    assert f.feedback_before_approval == 2
    assert f.commits_after_first_review == 3
    assert f.updates_after_approval == 2
    assert f.distinct_approvers == 2 and f.second_approval_wait_hours == 2
    assert f.first_review_at == at(2) and f.first_approval_at == at(6)
    assert f.review_requested_before_first_review and not f.merged_without_approval
    assert f.human_reviews == 4
    assert f.closed_at is None and f.end_at == at(10)


def test_missing_and_pre_ready_values():
    pr = record(
        author=Actor(None, False),
        events=(event("ready_for_review", 4), event("review", 2), event("commit", 5)),
    )
    f = facts(pr)
    assert f.pickup_hours == 0 and f.coding_hours == 0
    empty = facts(record())
    assert empty.coding_hours is None and empty.pickup_hours is None
    assert empty.merged_without_approval
    assert empty.updates_after_approval == 0 and empty.second_approval_wait_hours is None


@pytest.mark.parametrize(
    ("size", "bucket"),
    [(9, "XS"), (10, "S"), (99, "S"), (100, "M"), (499, "M"), (500, "L"), (999, "L"), (1000, "XL")],
)
def test_size_boundaries(size, bucket):
    assert facts(record(additions=size, deletions=0)).size_bucket == bucket


def test_scope_and_draft_readiness():
    f = facts(record(base_ref="release", author=Actor("robot", True), author_association="NONE"))
    assert f.is_bot_author and f.is_backport and f.external_contributor
    draft = facts(record(state="OPEN", merged_at=None, closed_at=None, is_draft=True))
    assert draft.ready_at is None
