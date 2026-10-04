import re
from datetime import UTC, datetime
from hashlib import sha1
from typing import Any

import structlog

from insights.domain import Actor, Event, EventKind, PullRequestRecord

logger = structlog.get_logger(__name__)
BOT_LOGINS = frozenset(
    {
        "dependabot",
        "renovate",
        "github-actions",
        "dotnet-maestro",
        "dotnet-policy-service",
        "msftbot",
        "copilot",
        "copilot-pull-request-reviewer",
        "copilot-swe-agent",
        "coderabbitai",
        "azure-pipelines",
        "codecov",
        "mergify",
        "pre-commit-ci",
        "stale",
    }
)
KINDS = {
    "ReadyForReviewEvent": EventKind.READY_FOR_REVIEW,
    "ConvertToDraftEvent": EventKind.CONVERT_TO_DRAFT,
    "ReviewRequestedEvent": EventKind.REVIEW_REQUESTED,
    "ReviewRequestRemovedEvent": EventKind.REVIEW_REQUEST_REMOVED,
    "PullRequestReview": EventKind.REVIEW,
    "ReviewDismissedEvent": EventKind.REVIEW_DISMISSED,
    "PullRequestCommit": EventKind.COMMIT,
    "HeadRefForcePushedEvent": EventKind.FORCE_PUSH,
    "IssueComment": EventKind.COMMENT,
    "LabeledEvent": EventKind.LABELED,
    "UnlabeledEvent": EventKind.UNLABELED,
    "ClosedEvent": EventKind.CLOSED,
    "ReopenedEvent": EventKind.REOPENED,
    "MergedEvent": EventKind.MERGED,
    "CrossReferencedEvent": EventKind.CROSS_REFERENCED,
}


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Upstream timestamps must include a timezone")
    return parsed.astimezone(UTC)


def remove_nulls(value: Any) -> Any:
    """Remove characters PostgreSQL cannot store, including nested JSON strings."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {remove_nulls(key): remove_nulls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [remove_nulls(item) for item in value]
    return value


def actor(node: dict[str, Any] | None, extra_bots: frozenset[str] = frozenset()) -> Actor:
    node = remove_nulls(node or {})
    login = node.get("login")
    lowered = (login or "").lower()
    return Actor(
        login,
        node.get("__typename") == "Bot"
        or lowered.endswith("[bot]")
        or lowered.removesuffix("[bot]") in BOT_LOGINS | extra_bots,
    )


def dedup_key(kind: str, occurred_at: datetime, actor_login: str | None, stable: str) -> str:
    value = f"{kind}|{occurred_at.isoformat()}|{actor_login or ''}|{stable}"
    return sha1(value.encode(), usedforsecurity=False).hexdigest()[:16]


def normalize_events(
    nodes: list[dict[str, Any]], extra_bots: frozenset[str] = frozenset()
) -> tuple[Event, ...]:
    nodes = remove_nulls(nodes)
    dismissed = {
        n["review"]["id"]: n
        for n in nodes
        if n and n.get("__typename") == "ReviewDismissedEvent" and n.get("review")
    }
    output: dict[str, Event] = {}
    for node in nodes:
        if not node:
            continue
        kind = KINDS.get(node.get("__typename", ""))
        if kind is None:
            logger.debug("timeline_event_ignored")
            continue
        person = actor(node.get("actor") or node.get("author"), extra_bots)
        occurred = node.get("createdAt")
        payload: dict[str, Any] = {}
        if kind == EventKind.REVIEW:
            state = node["state"]
            occurred = node.get("submittedAt")
            if not occurred or state == "PENDING":
                continue
            was_dismissed = state == "DISMISSED"
            if was_dismissed:
                prior = dismissed.get(node["id"])
                if prior is None:
                    logger.debug("dismissed_review_without_history")
                    continue
                state = prior["previousReviewState"]
            if state not in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}:
                continue
            payload = {"state": state, "review_id": node["id"], "dismissed": was_dismissed}
        elif kind == EventKind.REVIEW_DISMISSED:
            review = node.get("review") or {}
            payload = {
                "review_id": review.get("id"),
                "review_author": (review.get("author") or {}).get("login"),
                "previous_state": node.get("previousReviewState"),
            }
        elif kind == EventKind.REVIEW_REQUESTED:
            reviewer = node.get("requestedReviewer") or {}
            typename = reviewer.get("__typename", "Unknown")
            payload = {
                "reviewer": reviewer.get("login") or reviewer.get("slug"),
                "reviewer_type": typename if typename in {"User", "Team"} else "Unknown",
            }
        elif kind == EventKind.COMMIT:
            commit = node["commit"]
            occurred = commit["committedDate"]
            person = Actor(None, False)
            payload = {
                "oid": commit["oid"],
                "authored_at": commit["authoredDate"],
                "committed_at": commit["committedDate"],
                "reverts": re.findall(
                    r"This reverts commit ([0-9a-f]{7,40})",
                    commit.get("messageHeadline", "") + "\n" + commit.get("messageBody", ""),
                ),
            }
        elif kind in {EventKind.LABELED, EventKind.UNLABELED}:
            payload = {"label": node["label"]["name"]}
        elif kind == EventKind.CROSS_REFERENCED:
            source = node.get("source") or {}
            if source.get("__typename") != "PullRequest":
                continue
            payload = {
                "source_repo": source["repository"]["nameWithOwner"],
                "source_number": source["number"],
                "source_state": source["state"],
                "source_merged_at": source.get("mergedAt"),
                "source_author": (source.get("author") or {}).get("login"),
                "will_close": node["willCloseTarget"],
            }
        if not isinstance(occurred, str):
            raise ValueError("Missing event timestamp")
        timestamp = parse_time(occurred)
        key = dedup_key(kind, timestamp, person.login, node["id"])
        output[key] = Event(kind, timestamp, person, payload, key)
    return tuple(sorted(output.values(), key=lambda e: (e.occurred_at, e.kind, e.dedup_key)))


def normalize_pr(
    node: dict[str, Any], extra_bots: frozenset[str] = frozenset()
) -> PullRequestRecord:
    node = remove_nulls(node)
    author = node.get("author") or {}
    typename = author.get("__typename", "Unknown")
    return PullRequestRecord(
        source_id=node["id"],
        number=node["number"],
        title=node["title"],
        body_excerpt=(node.get("body") or "")[:4000],
        url=node["url"],
        state=node["state"],
        is_draft=node["isDraft"],
        author=actor(author, extra_bots),
        author_type=typename if typename in {"User", "Bot", "Mannequin"} else "Unknown",
        author_association=node["authorAssociation"],
        base_ref=node["baseRefName"],
        head_ref=node["headRefName"],
        created_at=parse_time(node["createdAt"]),
        updated_at=parse_time(node["updatedAt"]),
        closed_at=parse_time(node["closedAt"]) if node.get("closedAt") else None,
        merged_at=parse_time(node["mergedAt"]) if node.get("mergedAt") else None,
        merged_by=(node.get("mergedBy") or {}).get("login"),
        merge_commit_oid=(node.get("mergeCommit") or {}).get("oid"),
        additions=node["additions"],
        deletions=node["deletions"],
        changed_files=node["changedFiles"],
        labels=tuple(dict.fromkeys(n["name"] for n in node["labels"]["nodes"])),
        files=tuple(dict.fromkeys(n["path"] for n in (node.get("files") or {}).get("nodes", []))),
        files_truncated=bool((node.get("files") or {}).get("pageInfo", {}).get("hasNextPage")),
        events=normalize_events(node["timelineItems"]["nodes"], extra_bots),
    )
