"""Fetch GitHub Actions runs triggered by pull requests over REST."""

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from insights.domain import CiRun, RepoRef
from insights.sources.github.client import GitHubClient
from insights.sources.github.normalize import remove_nulls

logger = structlog.get_logger(__name__)


def normalize_run(raw: dict[str, Any]) -> CiRun:
    raw = remove_nulls(raw)
    return CiRun(
        int(raw["id"]),
        raw.get("name") or "Unnamed workflow",
        raw["event"],
        raw["head_sha"],
        raw["status"],
        raw.get("conclusion"),
        int(raw.get("run_attempt", 1)),
        datetime.fromisoformat(raw["created_at"]),
        datetime.fromisoformat(raw["run_started_at"]) if raw.get("run_started_at") else None,
        datetime.fromisoformat(raw["updated_at"]),
        tuple(sorted({int(p["number"]) for p in raw.get("pull_requests", [])})),
    )


async def fetch_runs(
    client: GitHubClient, repo: RepoRef, *, created_from: datetime, created_to: datetime
) -> list[CiRun]:
    """Return pull_request runs created in [created_from, created_to], deduplicated by id.

    Queries one UTC day at a time. The listing serves at most 1,000 runs per filter, so busier
    days are split into 6-hour windows; a window still over the cap is truncated and logged.
    """
    found: dict[int, CiRun] = {}
    path = f"/repos/{repo.full_name}/actions/runs"

    async def window(created: str, *, split: bool) -> bool:
        response = await client.rest_get(
            path, {"created": created, "event": "pull_request", "per_page": 100, "page": 1}
        )
        body = response.body
        count = int(body["total_count"])
        if count > 1000 and split:
            return False
        if count > 1000:
            logger.warning("ci_window_truncated", repo=repo.full_name, window=created, total=count)
        pages = min(10, (count + 99) // 100)
        for page in range(1, max(1, pages) + 1):
            if page > 1:
                body = (
                    await client.rest_get(
                        path,
                        {
                            "created": created,
                            "event": "pull_request",
                            "per_page": 100,
                            "page": page,
                        },
                    )
                ).body
            for raw in body["workflow_runs"]:
                run = normalize_run(raw)
                if created_from <= run.created_at <= created_to and run.event == "pull_request":
                    found[run.run_id] = run
        return True

    day = created_from.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= created_to:
        if not await window(day.date().isoformat(), split=True):
            for hour in range(0, 24, 6):
                start = day + timedelta(hours=hour)
                end = start + timedelta(hours=6, seconds=-1)
                await window(f"{start:%Y-%m-%dT%H:%M:%SZ}..{end:%Y-%m-%dT%H:%M:%SZ}", split=False)
        day += timedelta(days=1)
    return sorted(found.values(), key=lambda run: (run.created_at, run.run_id))
