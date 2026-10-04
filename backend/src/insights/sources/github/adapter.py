"""GitHub implementation of SourceAdapter. sources/github is the only package that calls GitHub."""

from contextlib import suppress
from datetime import datetime
from urllib.parse import quote

import structlog

from insights.config import REPO_RE, split_list
from insights.domain import (
    CiRun,
    OwnershipRule,
    PageResult,
    PullRequestRecord,
    RepoRef,
    RepositoryInfo,
)
from insights.sources.github.actions import fetch_runs
from insights.sources.github.client import GitHubClient, GitHubNotFoundError, GitHubTransientError
from insights.sources.github.normalize import normalize_pr, parse_time, remove_nulls
from insights.sources.github.ownership import parse_area_owners, parse_codeowners
from insights.sources.github.queries import PULL_REQUEST_TIMELINE, PULL_REQUESTS_PAGE

logger = structlog.get_logger(__name__)


class GitHubAdapter:
    def __init__(self, client: GitHubClient) -> None:
        self.client = client
        self.reset()

    def reset(self) -> None:
        self.page_size = self.client.settings.graphql_page_size
        self.successful_pages = 0

    async def pull_requests_page(
        self, repo: RepoRef, *, cursor: str | None, page_size: int, open_only: bool = False
    ) -> PageResult:
        """Fetch one page of PRs, most recently updated first, with complete timelines.

        Transient errors halve the page size (floor 5) and retry; the configured size returns
        after two consecutive successful pages. PRs that fail normalization are counted in
        `skipped_prs` instead of failing the page.
        """
        try:
            page = await self._pull_requests_page(
                repo, cursor=cursor, page_size=page_size, open_only=open_only
            )
        except Exception:
            self.successful_pages = 0
            raise
        self.successful_pages = min(2, self.successful_pages + 1)
        if self.successful_pages == 2:
            self.page_size = self.client.settings.graphql_page_size
        return page

    async def _pull_requests_page(
        self, repo: RepoRef, *, cursor: str | None, page_size: int, open_only: bool = False
    ) -> PageResult:
        if not REPO_RE.fullmatch(repo.full_name):
            raise ValueError("Invalid repository")
        size = min(page_size, self.page_size)
        while True:
            try:
                data = await self.client.graphql(
                    PULL_REQUESTS_PAGE,
                    {
                        "owner": repo.owner,
                        "name": repo.name,
                        "pageSize": size,
                        "cursor": cursor,
                        "states": ["OPEN"] if open_only else None,
                    },
                )
                break
            except GitHubTransientError:
                self.successful_pages = 0
                if size <= 5:
                    raise
                size = max(5, size // 2)
                self.page_size = size
        repository = data.get("repository")
        if not repository:
            raise GitHubNotFoundError("repository_not_found")
        connection = repository["pullRequests"]
        cost = int(data["rateLimit"]["cost"])
        extra_bots = frozenset(x.lower() for x in split_list(self.client.settings.extra_bot_logins))
        prs: list[PullRequestRecord] = []
        updated_at: list[datetime] = []
        skipped = 0
        for node in connection["nodes"]:
            # Preserve page bounds even when a PR's other fields cannot be normalized.
            with suppress(KeyError, TypeError, ValueError, AttributeError):
                updated_at.append(parse_time(remove_nulls(node["updatedAt"])))
            try:
                # Timelines over 100 items continue by PR node id, which is why queries fetch `id`.
                timeline = node["timelineItems"]
                seen: set[str] = set()
                while timeline["pageInfo"]["hasNextPage"]:
                    next_cursor = timeline["pageInfo"]["endCursor"]
                    if not next_cursor or next_cursor in seen:
                        raise GitHubTransientError("timeline_cursor_did_not_advance")
                    seen.add(next_cursor)
                    extra = await self.client.graphql(
                        PULL_REQUEST_TIMELINE, {"id": node["id"], "cursor": next_cursor}
                    )
                    cost += int(extra["rateLimit"]["cost"])
                    if not extra.get("node"):
                        raise GitHubNotFoundError("pull_request_not_found")
                    page = extra["node"]["timelineItems"]
                    timeline["nodes"].extend(page["nodes"])
                    timeline["pageInfo"] = page["pageInfo"]
                prs.append(normalize_pr(node, extra_bots))
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                number = node.get("number") if isinstance(node, dict) else None
                logger.warning(
                    "pr_normalize_failed",
                    repo=repo.full_name,
                    number=number if isinstance(number, int) else None,
                    error=type(exc).__name__,
                )
                skipped += 1
        return PageResult(
            RepositoryInfo(
                remove_nulls(repository["nameWithOwner"]),
                remove_nulls((repository.get("defaultBranchRef") or {}).get("name", "")),
                repository["isArchived"],
            ),
            tuple(prs),
            connection["pageInfo"]["endCursor"],
            connection["pageInfo"]["hasNextPage"],
            min(updated_at, default=None),
            max(updated_at, default=None),
            cost,
            skipped,
        )

    async def ci_runs(
        self, repo: RepoRef, *, created_from: datetime, created_to: datetime
    ) -> list[CiRun]:
        if not REPO_RE.fullmatch(repo.full_name):
            raise ValueError("Invalid repository")
        return await fetch_runs(self.client, repo, created_from=created_from, created_to=created_to)

    async def ownership_rules(self, repo: RepoRef) -> list[OwnershipRule]:
        if not REPO_RE.fullmatch(repo.full_name):
            raise ValueError("Invalid repository")
        rules: list[OwnershipRule] = []
        for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
            try:
                response = await self.client.rest_get(
                    f"/repos/{repo.full_name}/contents/{path}",
                    accept="application/vnd.github.raw+json",
                )
            except GitHubNotFoundError:
                continue
            rules.extend(parse_codeowners(response.body))
            break
        try:
            path = quote(self.client.settings.area_owners_path.lstrip("/"), safe="/")
            response = await self.client.rest_get(
                f"/repos/{repo.full_name}/contents/{path}",
                accept="application/vnd.github.raw+json",
            )
        except GitHubNotFoundError:
            pass
        else:
            rules.extend(parse_area_owners(response.body))
        return rules
