import random
from dataclasses import replace

import pytest
from tests.factories import at, event, record

from insights.analytics.timeline import (
    build_timeline,
    check_invariants,
    ledger_hours,
    pr_input,
)
from insights.domain import Actor

R = "waiting_reviewer"
A = "waiting_author"
M = "waiting_merge"


@pytest.mark.parametrize(
    ("case", "events", "changes", "states", "rounds"),
    [
        (1, [event("review", 2)], {}, [R, M], 0),
        (
            2,
            [event("review", 2, state="CHANGES_REQUESTED"), event("commit", 4), event("review", 6)],
            {},
            [R, A, R, M],
            1,
        ),
        (
            3,
            [event("review", 2, state="COMMENTED"), event("comment", 4, "author")],
            {},
            [R, A, R],
            1,
        ),
        (
            4,
            [event("review", 2, state="COMMENTED"), event("review_requested", 4, "author")],
            {},
            [R, A, R],
            1,
        ),
        (5, [event("ready_for_review", 2)], {}, ["coding", R], 0),
        (6, [event("convert_to_draft", 2), event("ready_for_review", 4)], {}, [R, A, R], 0),
        (
            7,
            [event("review", 2, review_id="r1"), event("review_dismissed", 4, review_id="r1")],
            {},
            [R, M, R],
            0,
        ),
        (
            8,
            [
                event("review", 2, "a", state="CHANGES_REQUESTED"),
                event("review", 3, "b"),
                event("commit", 4),
            ],
            {},
            [R, A, R],
            1,
        ),
        (9, [], {}, [R], 0),
        (10, [], {"state": "CLOSED", "merged_at": None}, [R], 0),
        (
            11,
            [event("closed", 2), event("reopened", 4), event("review", 6)],
            {},
            [R, "closed", R, M],
            0,
        ),
        (12, [event("review", 2, "robot[bot]"), event("review", 4, "AUTHOR")], {}, [R], 0),
        (13, [], {"state": "CLOSED", "merged_at": None, "is_draft": True}, ["coding"], 0),
        (14, [event("commit", 2)], {}, [R], 0),
        (15, [], {"state": "OPEN", "closed_at": None, "merged_at": None}, [R], 0),
        (
            16,
            [event("review", 3, state="COMMENTED"), event("commit", 5), event("review", 7)],
            {},
            [R, A, R, M],
            1,
        ),
        (17, [event("review", 2), event("review", 4, state="COMMENTED")], {}, [R, M], 0),
        (
            18,
            [
                replace(
                    event("review", 2, review_id="r1"),
                    payload={"state": "APPROVED", "review_id": "r1", "dismissed": True},
                ),
                event("review_dismissed", 4, review_id="r1", previous_state="APPROVED"),
            ],
            {},
            [R, M, R],
            0,
        ),
        (
            19,
            [
                event("review", 2, review_id="r1"),
                event("review", 3, review_id="r2"),
                event("review_dismissed", 4, review_id="r1"),
            ],
            {},
            [R, M],
            0,
        ),
        (
            20,
            [
                event("review", 2, state="COMMENTED"),
                event("comment", 3, None),
                event("force_push", 4, None),
            ],
            {"author": Actor(None, False)},
            [R, A, R],
            1,
        ),
        (21, [event("review", 2), event("closed", 10 - 1 / 3600)], {}, [R, M], 0),
        (
            22,
            [
                event("review", 2, "a", review_id="r1"),
                event("review", 3, "b", review_id="r2"),
                event("commit", 4),
                event("review_dismissed", 4, review_id="r1"),
                event("review_dismissed", 4, review_id="r2"),
            ],
            {},
            [R, M, R],
            0,
        ),
    ],
)
def test_spec_cases(case, events, changes, states, rounds):
    pr = record(events=tuple(events), **changes)
    result = build_timeline(pr_input(pr), pr.events, at(20))
    assert [i.state for i in result.intervals] == states
    assert result.review_rounds == rounds
    assert check_invariants(result, pr_input(pr)) == []
    assert build_timeline(pr_input(pr), pr.events, at(20)) == result
    if case == 11:
        assert sum(ledger_hours(result.intervals, start=at(0), end=at(10)).values()) == 8
    if case == 15:
        assert result.intervals[-1].end_at is None


def test_ledger_clipping_skips_closed_time():
    pr = record(events=(event("closed", 2), event("reopened", 4)))
    timeline = build_timeline(pr_input(pr), pr.events, at(20))
    assert ledger_hours(timeline.intervals, start=at(1), end=at(5))[R] == 2


def test_random_legal_sequences_invariants():
    rng = random.Random(42)
    for index in range(200):
        events = []
        for hour in range(1, 25):
            choice = rng.choice(["review", "commit", "comment", "review_dismissed"])
            events.append(
                event(
                    choice,
                    hour,
                    rng.choice(["a", "b", "author", None]),
                    state=rng.choice(["APPROVED", "COMMENTED", "CHANGES_REQUESTED"]),
                    review_id=f"r{rng.randrange(1, 25)}",
                )
            )
        if index % 2:
            events.extend([event("closed", 6), event("reopened", 12)])
        if index % 3:
            events.extend([event("convert_to_draft", 14), event("ready_for_review", 18)])
        pr = record(
            events=tuple(events),
            closed_at=at(30),
            merged_at=at(30),
            author=Actor(None if index % 4 else "author", False),
        )
        if index % 5 == 0:
            pr = replace(pr, state="OPEN", merged_at=None, closed_at=None)
        result = build_timeline(pr_input(pr), pr.events, at(40))
        assert not check_invariants(result, pr_input(pr)), (index, result)
