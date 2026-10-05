"""Source-neutral records shared by the GitHub adapter, sync pipeline and analytics."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class EventKind(StrEnum):
    """Timeline event kinds kept from GitHub; values are stored in pr_events.kind."""

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
    """Owner/name pair identifying a repository at the source."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        """Repository identity in owner/name form, preserving the stored spelling."""
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    """Repository metadata returned with each page, used to detect backports."""

    full_name: str
    default_branch: str


@dataclass(frozen=True, slots=True)
class Actor:
    """Who performed an event; login is None for commits and deleted accounts."""

    login: str | None
    is_bot: bool


@dataclass(frozen=True, slots=True)
class Event:
    """One normalized timeline event; dedup_key is stable across re-reads."""

    kind: EventKind
    occurred_at: datetime
    actor: Actor
    payload: dict[str, Any]
    dedup_key: str


@dataclass(frozen=True, slots=True)
class PullRequestRecord:
    """Immutable source view of one PR with its labels, files and events."""

    number: int
    title: str
    url: str
    state: str
    is_draft: bool
    author: Actor
    base_ref: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    merged_at: datetime | None
    additions: int
    deletions: int
    labels: tuple[str, ...]
    files: tuple[str, ...]
    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class PageResult:
    """One fetched page of PRs plus the cursor and update bounds sync needs."""

    repository: RepositoryInfo
    prs: tuple[PullRequestRecord, ...]
    end_cursor: str | None
    has_next_page: bool
    oldest_updated_at: datetime | None
    newest_updated_at: datetime | None
    graphql_cost: int
    skipped_prs: int = 0


class GitHubError(Exception):
    """Sanitized upstream error; never includes headers or response bodies."""


class GitHubAuthError(GitHubError):
    """GitHub rejected the token (HTTP 401)."""


class GitHubNotFoundError(GitHubError):
    """The repository or PR node does not exist or is not visible."""


class GitHubRateLimited(GitHubError):  # noqa: N818
    """Rate-limit waits exceeded the retry or 15-minute wait budget."""


class GitHubQueryError(GitHubError):
    """A non-retryable GraphQL or HTTP error, or a malformed response."""


class GitHubTransientError(GitHubError):
    """A retryable network, upstream or pagination failure."""
