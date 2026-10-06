"""GraphQL normalization: bots, dismissals, cross-references, hashing."""

from dataclasses import asdict, replace

import pytest

from insights.domain import EventKind
from insights.sources.github.normalize import actor, normalize_pr, remove_nulls
from insights.sync.store import content_hash


@pytest.mark.parametrize(
    ("node", "extra", "expected"),
    [
        ({"__typename": "Bot", "login": "service"}, frozenset(), True),
        ({"__typename": "User", "login": "service[bot]"}, frozenset(), True),
        ({"__typename": "User", "login": "CoPiLoT"}, frozenset(), True),
        ({"__typename": "User", "login": "copi\x00lot"}, frozenset(), True),
        ({"__typename": "User", "login": "custom"}, frozenset({"custom"}), True),
        (None, frozenset(), False),
        ({"__typename": "Mannequin", "login": "human"}, frozenset(), False),
    ],
)
def test_bot_detection(node, extra, expected):
    assert actor(node, extra).is_bot is expected


def test_normalization_and_hash(github_page):
    node = github_page["data"]["repository"]["pullRequests"]["nodes"][0]
    node["author"] = None
    node["files"]["pageInfo"]["hasNextPage"] = True
    pr = normalize_pr(node)
    assert pr.author.login is None and not pr.author.is_bot
    assert pr.labels == ("area-A",)
    assert pr.files == ("src/A/file.cs",)
    assert content_hash(pr) == content_hash(replace(pr, events=tuple(reversed(pr.events))))
    assert pr.events[0].payload == {
        "oid": "abcdef012345",
        "authored_at": "2025-12-31T23:00:00Z",
        "committed_at": "2026-01-01T00:00:00Z",
    }


def test_all_upstream_strings_are_cleaned_before_hashing(github_page):
    node = github_page["data"]["repository"]["pullRequests"]["nodes"][0]
    node["timelineItems"]["nodes"].append(
        {
            "__typename": "LabeledEvent",
            "id": "label-event",
            "createdAt": "2026-01-02T00:00:00Z",
            "actor": {"__typename": "User", "login": "labeler"},
            "label": {"name": "area-A"},
        }
    )
    clean = normalize_pr(node)

    def inject(value):
        if isinstance(value, str):
            return "\x00" + value + "\x00"
        if isinstance(value, dict):
            return {inject(key): inject(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inject(item) for item in value]
        return value

    dirty = normalize_pr(inject(node))
    assert asdict(dirty) == asdict(clean)
    assert content_hash(dirty) == content_hash(clean)
    assert remove_nulls({"nested\x00": ["a\x00", {"b": "c\x00"}]}) == {"nested": ["a", {"b": "c"}]}


def test_dismissals_keep_original_decision_and_distinct_ids(github_page):
    node = github_page["data"]["repository"]["pullRequests"]["nodes"][0]
    nodes = node["timelineItems"]["nodes"]
    for number in (1, 2):
        nodes.extend(
            [
                {
                    "__typename": "PullRequestReview",
                    "id": f"r{number}",
                    "state": "DISMISSED",
                    "submittedAt": "2026-01-02T01:00:00Z",
                    "author": {"__typename": "User", "login": f"reviewer{number}"},
                },
                {
                    "__typename": "ReviewDismissedEvent",
                    "id": f"d{number}",
                    "createdAt": "2026-01-02T02:00:00Z",
                    "actor": {"__typename": "User", "login": "author"},
                    "previousReviewState": "APPROVED",
                    "review": {"id": f"r{number}", "author": {"login": f"reviewer{number}"}},
                },
            ]
        )
    nodes.extend(
        [
            nodes[-1],
            {
                "__typename": "PullRequestReview",
                "id": "orphan",
                "state": "DISMISSED",
                "submittedAt": "2026-01-02T01:00:00Z",
            },
            {
                "__typename": "PullRequestReview",
                "id": "pending",
                "state": "PENDING",
                "submittedAt": None,
            },
        ]
    )
    events = normalize_pr(node).events
    dismissed = [event for event in events if event.kind == EventKind.REVIEW_DISMISSED]
    approvals = [event for event in events if event.kind == EventKind.REVIEW]
    assert len(dismissed) == 2
    assert len({event.dedup_key for event in dismissed}) == 2
    assert all(e.payload["state"] == "APPROVED" and e.payload["dismissed"] for e in approvals)
    assert {e.payload["review_author"] for e in dismissed} == {"reviewer1", "reviewer2"}
    assert all(e.payload["previous_state"] == "APPROVED" for e in dismissed)
    assert events == normalize_pr(node).events


def test_cross_reference_ignores_issues_and_maps_pr(github_page):
    node = github_page["data"]["repository"]["pullRequests"]["nodes"][0]
    nodes = node["timelineItems"]["nodes"]
    nodes.extend(
        [
            {
                "__typename": "CrossReferencedEvent",
                "id": "x1",
                "createdAt": "2026-01-03T00:00:00Z",
                "willCloseTarget": False,
                "source": {"__typename": "Issue"},
            },
            {
                "__typename": "CrossReferencedEvent",
                "id": "x2",
                "createdAt": "2026-01-03T00:00:00Z",
                "willCloseTarget": True,
                "source": {
                    "__typename": "PullRequest",
                    "number": 2,
                    "state": "MERGED",
                    "mergedAt": "2026-01-04T00:00:00Z",
                    "author": None,
                    "repository": {"nameWithOwner": "a/b"},
                },
            },
        ]
    )
    refs = [e for e in normalize_pr(node).events if e.kind == EventKind.CROSS_REFERENCED]
    assert len(refs) == 1 and refs[0].payload["source_author"] is None
    assert refs[0].payload["source_repo"] == "a/b"
