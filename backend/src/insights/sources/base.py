"""Source adapter protocol: the upstream-agnostic contract that sync jobs call."""

from datetime import datetime
from typing import Protocol

from insights.domain import CiRun, PageResult, RepoRef


class SourceAdapter(Protocol):
    def reset(self) -> None:
        """Reset adaptive source state before starting a new repository sync."""
        ...

    async def pull_requests_page(
        self, repo: RepoRef, *, cursor: str | None, page_size: int, open_only: bool = False
    ) -> PageResult:
        """Fetch updated-order PRs with complete timelines, cursor bounds and skip counts."""
        ...

    async def ci_runs(
        self, repo: RepoRef, *, created_from: datetime, created_to: datetime
    ) -> list[CiRun]:
        """Fetch CI records created within the requested inclusive timestamp bounds."""
        ...
