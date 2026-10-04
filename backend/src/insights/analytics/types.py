from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PrFacts:
    number: int = 0
    is_bot_author: bool = False
    is_backport: bool = False
    external_contributor: bool = False
    first_commit_at: datetime | None = None
    ready_at: datetime | None = None
    first_review_at: datetime | None = None
    first_approval_at: datetime | None = None
    approved_at: datetime | None = None
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    end_at: datetime | None = None
    coding_hours: float | None = None
    pickup_hours: float | None = None
    review_hours: float | None = None
    merge_hours: float | None = None
    cycle_hours: float | None = None
    review_rounds: int = 0
    feedback_before_approval: int = 0
    commits_after_first_review: int = 0
    updates_after_approval: int = 0
    distinct_approvers: int = 0
    second_approval_wait_hours: float | None = None
    merged_without_approval: bool = False
    review_requested_before_first_review: bool = False
    human_reviews: int = 0
    size_lines: int = 0
    size_bucket: str = ""
    locations: tuple[str, ...] = ()
    location_source: str = ""
    is_revert: bool = False
    reverts_pr_id: int | None = None
    reverted_by_pr_id: int | None = None
    reverted_at: datetime | None = None
    reland_of_pr_id: int | None = None
    close_class: str | None = None
    state_at_close: str | None = None
    late_rejection: bool = False
    ci_covered: bool = False
