"""Per-PR facts derived during sync, persisted, and read back as analytics input."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PrFacts:
    number: int = 0
    is_bot_author: bool = False
    is_backport: bool = False
    ready_at: datetime | None = None
    first_review_at: datetime | None = None
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    end_at: datetime | None = None
    coding_hours: float | None = None
    pickup_hours: float | None = None
    cycle_hours: float | None = None
    review_rounds: int = 0
    commits_after_first_review: int = 0
    size_lines: int = 0
    locations: tuple[str, ...] = ()
