from datetime import datetime
from typing import Protocol

from insights.domain import CiRun, OwnershipRule, PageResult, RepoRef


class SourceAdapter(Protocol):
    def reset(self) -> None: ...

    async def pull_requests_page(
        self, repo: RepoRef, *, cursor: str | None, page_size: int, open_only: bool = False
    ) -> PageResult: ...

    async def ci_runs(
        self, repo: RepoRef, *, created_from: datetime, created_to: datetime
    ) -> list[CiRun]: ...

    async def ownership_rules(self, repo: RepoRef) -> list[OwnershipRule]: ...
