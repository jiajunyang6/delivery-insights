"""Source adapter protocol: the upstream-agnostic contract that sync jobs call."""

from typing import Protocol

from insights.domain import PageResult, RepoRef


class SourceAdapter(Protocol):
    """Contract a source must meet to feed the sync pipeline."""

    def reset(self) -> None:
        """Reset adaptive source state before starting a new repository sync."""
        ...

    async def pull_requests_page(
        self, repo: RepoRef, *, cursor: str | None, page_size: int, open_only: bool = False
    ) -> PageResult:
        """Fetch updated-order PRs with complete timelines, cursor bounds and skip counts."""
        ...
