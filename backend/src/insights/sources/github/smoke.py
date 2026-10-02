import argparse
import asyncio

from insights.config import REPO_RE, Settings
from insights.domain import RepoRef
from insights.sources.github.adapter import GitHubAdapter
from insights.sources.github.client import GitHubClient


async def run(repo: str, pages: int) -> None:
    settings = Settings()
    if not settings.github_token:
        raise SystemExit("GITHUB_TOKEN is not set")
    client = GitHubClient(settings)
    adapter = GitHubAdapter(client)
    count = events = 0
    cursor = None
    owner, name = repo.split("/")
    try:
        for _ in range(pages):
            page = await adapter.pull_requests_page(
                RepoRef(owner, name), cursor=cursor, page_size=settings.graphql_page_size
            )
            count += len(page.prs)
            events += sum(len(pr.events) for pr in page.prs)
            cursor = page.end_cursor
            if not page.has_next_page:
                break
        print(f"prs={count} events={events} rate_limit_remaining={client.rate_limit_remaining}")
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pages", type=int, default=1)
    args = parser.parse_args()
    if not REPO_RE.fullmatch(args.repo) or args.pages < 1:
        parser.error("A valid owner/name and positive page count are required")
    asyncio.run(run(args.repo, args.pages))


if __name__ == "__main__":
    main()
