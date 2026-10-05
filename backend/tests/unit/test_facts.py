"""Per-PR fact derivation: milestones, counts, scope and readiness."""

from tests.factories import at, event, record

from insights.analytics.facts import compute_facts
from insights.analytics.timeline import build_timeline, pr_input
from insights.domain import Actor


def facts(pr):
    timeline = build_timeline(pr_input(pr), pr.events, at(30))
    return compute_facts(pr, pr.events, timeline, default_branch="main")


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
    assert (f.coding_hours, f.pickup_hours, f.cycle_hours) == (4, 2, 14)
    assert f.review_rounds == 2
    assert f.commits_after_first_review == 3
    assert f.first_review_at == at(2)
    assert f.size_lines == 25 and f.locations == ("area-A",)
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
    assert empty.first_review_at is None and empty.commits_after_first_review == 0


def test_scope_and_draft_readiness():
    f = facts(record(base_ref="release", author=Actor("robot", True)))
    assert f.is_bot_author and f.is_backport
    draft = facts(record(state="OPEN", merged_at=None, closed_at=None, is_draft=True))
    assert draft.ready_at is None
