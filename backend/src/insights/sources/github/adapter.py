from insights.config import REPO_RE, split_list
from insights.domain import PageResult, RepoRef, RepositoryInfo
from insights.sources.github.client import GitHubClient, GitHubNotFoundError, GitHubTransientError
from insights.sources.github.normalize import normalize_pr
from insights.sources.github.queries import PULL_REQUEST_TIMELINE, PULL_REQUESTS_PAGE


class GitHubAdapter:
    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    async def pull_requests_page(
        self, repo: RepoRef, *, cursor: str | None, page_size: int, open_only: bool = False
    ) -> PageResult:
        if not REPO_RE.fullmatch(repo.full_name):
            raise ValueError("Invalid repository")
        size = min(page_size, self.client.page_size)
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
                if size <= 5:
                    raise
                size = max(5, size // 2)
                self.client.page_size = size
        repository = data.get("repository")
        if not repository:
            raise GitHubNotFoundError("repository_not_found")
        connection = repository["pullRequests"]
        cost = int(data["rateLimit"]["cost"])
        for node in connection["nodes"]:
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
        extra_bots = frozenset(x.lower() for x in split_list(self.client.settings.extra_bot_logins))
        prs = tuple(normalize_pr(node, extra_bots) for node in connection["nodes"])
        return PageResult(
            RepositoryInfo(
                repository["nameWithOwner"],
                (repository.get("defaultBranchRef") or {}).get("name", ""),
                repository["isArchived"],
            ),
            prs,
            connection["pageInfo"]["endCursor"],
            connection["pageInfo"]["hasNextPage"],
            min((pr.updated_at for pr in prs), default=None),
            max((pr.updated_at for pr in prs), default=None),
            cost,
        )
