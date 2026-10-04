from dataclasses import replace

import pytest
from tests.factories import at, event, record

from insights.analytics.classify import LinkInput, is_flow, link_prs, locations_for
from insights.analytics.types import PrFacts
from insights.domain import Actor, OwnershipRule


def item(identifier=1, **changes):
    pr = record(number=identifier, **changes)
    return LinkInput(
        identifier,
        pr,
        PrFacts(
            number=identifier,
            ready_at=pr.created_at,
            merged_at=pr.merged_at,
            closed_at=pr.closed_at if not pr.merged_at else None,
            human_reviews=sum(e.kind == "review" for e in pr.events),
            review_rounds=2,
            state_at_close="waiting_reviewer",
        ),
    )


def linked(*items):
    return link_prs(items, repo_full_name="a/b", default_branch="main")


def test_location_fallback_chain():
    pr = record(labels=("Area-Z", "area-A", "area-A"))
    assert locations_for(pr, "label:area-", 2, ()) == (("Area-Z", "area-A"), "label")
    rules = [
        OwnershipRule("codeowners", "*", ("@a",), 1),
        OwnershipRule("codeowners", "src/**", ("@b",), 2),
    ]
    assert locations_for(replace(pr, labels=()), "label:area-", 2, rules) == (
        ("codeowners:src/**",),
        "codeowners",
    )
    paths = ("a/x/1", "a/x/2", "b/y/1", "c/z/1", "README", "README2")
    assert locations_for(replace(pr, files=paths), "directory", 2, rules) == (
        ("dir:/", "dir:a/x", "dir:b/y"),
        "directory",
    )
    assert locations_for(replace(pr, labels=(), files=()), "label:area-", 2, ()) == (
        ("unclassified",),
        "unclassified",
    )


@pytest.mark.parametrize(
    ("actor", "reviewed", "expected"),
    [
        ("author", False, "no_review"),
        ("reviewer", True, "rejected"),
        ("author", True, "abandoned"),
        ("bot[bot]", True, "abandoned"),
    ],
)
def test_close_classes(actor, reviewed, expected):
    events = ([event("review", 2)] if reviewed else []) + [event("closed", 10, actor)]
    result = linked(item(state="CLOSED", merged_at=None, events=tuple(events)))[1]
    assert result.close_class == expected
    assert result.late_rejection == (expected == "rejected")


def test_late_rejection_calendar_boundary():
    original = item(
        state="CLOSED",
        merged_at=None,
        closed_at=at(336),
        events=(event("review", 2), event("closed", 336)),
    )
    original = replace(original, facts=replace(original.facts, review_rounds=0))
    assert linked(original)[1].late_rejection
    earlier = replace(original, record=replace(original.record, closed_at=at(335)))
    assert not linked(earlier)[1].late_rejection


@pytest.mark.parametrize(
    ("title", "body", "events"),
    [
        ('Revert "Original"', "", ()),
        ("Rollback", "Reverts A/B#1", ()),
        ("Revert commit", "", (event("commit", 21, reverts=["abcdef0"]),)),
    ],
)
def test_revert_resolution(title, body, events):
    original = item(title="Original", merge_commit_oid="abcdef0123456")
    reverted = item(
        2, title=title, body_excerpt=body, created_at=at(20), merged_at=at(30), events=events
    )
    result = linked(original, reverted)
    assert result[2].is_revert and result[2].reverts_pr_id == 1
    assert result[1].reverted_by_pr_id == 2 and result[1].reverted_at == at(30)


def test_title_uses_latest_earlier_default_branch_merge():
    originals = [
        item(1, title="X"),
        item(2, title="X", merged_at=at(15)),
        item(3, title="X", base_ref="release", merged_at=at(18)),
        item(4, title="X", merged_at=at(25)),
    ]
    revert = item(5, title='Revert "X"', created_at=at(20), merged_at=at(30))
    assert linked(*originals, revert)[5].reverts_pr_id == 2


def test_unmerged_cross_repo_and_revert_of_revert():
    a = item(title="Original")
    cross = item(2, title="Rollback", body_excerpt="Reverts other/repo#1", created_at=at(20))
    assert linked(a, cross)[2].reverts_pr_id is None
    b = item(2, title='Revert "Original"', created_at=at(20), merged_at=None, state="OPEN")
    assert linked(a, b)[2].is_revert and linked(a, b)[1].reverted_at is None
    b = replace(b, record=replace(b.record, merged_at=at(30), state="MERGED"))
    c = item(3, title='Revert "Revert "Original""', created_at=at(40), merged_at=at(50))
    result = linked(a, b, c)
    assert result[3].reland_of_pr_id == 1
    assert not result[3].is_revert
    assert result[2].reverted_at is None


@pytest.mark.parametrize("title", ["Reland #1", 'Reapply "Original"', "Re-land Original"])
def test_reland_titles(title):
    a = item(title="Original")
    b = item(2, title='Revert "Original"', created_at=at(20), merged_at=at(30))
    c = item(3, title=title, created_at=at(40))
    assert linked(a, b, c)[3].reland_of_pr_id == 1


def test_superseded_current_source_overrides_stale_event():
    cross = event(
        "cross_referenced",
        5,
        source_repo="a/b",
        source_number=2,
        source_state="OPEN",
        source_merged_at=None,
        source_author="wrong",
        will_close=False,
    )
    a = item(state="CLOSED", merged_at=None, events=(cross,))
    b = item(2, head_ref="different", merged_at=at(12))
    result = linked(a, b)[1]
    assert result.close_class == "superseded"


@pytest.mark.parametrize("unknown", [False, True])
def test_head_ref_supersede_and_unknown_author(unknown):
    author = Actor(None if unknown else "AUTHOR", False)
    a = item(state="CLOSED", merged_at=None, author=author)
    b = item(2, created_at=at(11), merged_at=at(20))
    assert linked(a, b)[1].close_class == ("no_review" if unknown else "superseded")


def test_source_missing_and_unknown_source_author():
    payload = {
        "source_repo": "a/b",
        "source_number": 99,
        "source_state": "MERGED",
        "source_merged_at": at(12).isoformat(),
        "source_author": "author",
        "will_close": False,
    }
    a = item(state="CLOSED", merged_at=None, events=(event("cross_referenced", 5, **payload),))
    assert linked(a)[1].close_class == "superseded"
    a = replace(
        a,
        record=replace(
            a.record, events=(event("cross_referenced", 5, **{**payload, "source_author": None}),)
        ),
    )
    assert linked(a)[1].close_class == "no_review"


def test_flow_scope():
    assert is_flow(PrFacts(ready_at=at(0)))
    assert not is_flow(PrFacts())
    assert not is_flow(PrFacts(ready_at=at(0), is_bot_author=True))
    assert not is_flow(PrFacts(ready_at=at(0), is_backport=True))
