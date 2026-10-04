from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class EventKind(StrEnum):
    READY_FOR_REVIEW = "ready_for_review"
    CONVERT_TO_DRAFT = "convert_to_draft"
    REVIEW_REQUESTED = "review_requested"
    REVIEW_REQUEST_REMOVED = "review_request_removed"
    REVIEW = "review"
    REVIEW_DISMISSED = "review_dismissed"
    COMMIT = "commit"
    FORCE_PUSH = "force_push"
    COMMENT = "comment"
    LABELED = "labeled"
    UNLABELED = "unlabeled"
    CLOSED = "closed"
    REOPENED = "reopened"
    MERGED = "merged"
    CROSS_REFERENCED = "cross_referenced"


@dataclass(frozen=True, slots=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    full_name: str
    default_branch: str
    is_archived: bool


@dataclass(frozen=True, slots=True)
class Actor:
    login: str | None
    is_bot: bool


@dataclass(frozen=True, slots=True)
class Event:
    kind: EventKind
    occurred_at: datetime
    actor: Actor
    payload: dict[str, Any]
    dedup_key: str


@dataclass(frozen=True, slots=True)
class PullRequestRecord:
    source_id: str
    number: int
    title: str
    body_excerpt: str
    url: str
    state: str
    is_draft: bool
    author: Actor
    author_type: str
    author_association: str
    base_ref: str
    head_ref: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    merged_at: datetime | None
    merged_by: str | None
    merge_commit_oid: str | None
    additions: int
    deletions: int
    changed_files: int
    labels: tuple[str, ...]
    files: tuple[str, ...]
    files_truncated: bool
    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class PageResult:
    repository: RepositoryInfo
    prs: tuple[PullRequestRecord, ...]
    end_cursor: str | None
    has_next_page: bool
    oldest_updated_at: datetime | None
    newest_updated_at: datetime | None
    graphql_cost: int
    skipped_prs: int = 0


@dataclass(frozen=True, slots=True)
class CiRun:
    run_id: int
    workflow_name: str
    event: str
    head_sha: str
    status: str
    conclusion: str | None
    run_attempt: int
    created_at: datetime
    run_started_at: datetime | None
    updated_at: datetime
    pr_numbers: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class OwnershipRule:
    source: str
    pattern: str
    owners: tuple[str, ...]
    line_no: int


class GitHubError(Exception):
    """Sanitized upstream error; never includes headers or response bodies."""


class GitHubAuthError(GitHubError):
    pass


class GitHubNotFoundError(GitHubError):
    pass


class GitHubRateLimited(GitHubError):  # noqa: N818
    pass


class GitHubQueryError(GitHubError):
    pass


class GitHubTransientError(GitHubError):
    pass
