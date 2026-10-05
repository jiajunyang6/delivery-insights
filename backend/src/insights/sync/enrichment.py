"""CI enrichment job: GitHub Actions runs, rederiving the PRs whose CI waiting changed."""

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import BigInteger, any_, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession

from insights.config import Settings
from insights.db.ci import load_ci_data, run_from_row
from insights.db.models import Repository, WorkflowRun
from insights.domain import CiRun, RepoRef
from insights.sources.base import SourceAdapter
from insights.sync.derive import derive_prs
from insights.sync.queue import (
    enqueue_precompute,
    last_success,
    now_for,
    run_job,
    sessions_for,
)

logger = structlog.get_logger(__name__)


async def save_runs(
    session: AsyncSession, repo_id: int, runs: list[CiRun], *, settings: Settings, now: datetime
) -> tuple[int, int, int]:
    """Upsert changed runs and rederive PRs linked to them before or after the change.

    Returns (runs changed, PRs rederived, invariant violations).
    """
    ids = [r.run_id for r in runs]
    existing = (
        {
            r.id: run_from_row(r)
            for r in await session.scalars(
                select(WorkflowRun).where(
                    WorkflowRun.id == any_(literal(ids, type_=ARRAY(BigInteger)))
                )
            )
        }
        if ids
        else {}
    )
    changed = [r for r in runs if existing.get(r.run_id) != r]
    if not changed:
        return 0, 0, 0
    changed_ids = [r.run_id for r in changed]
    before = await load_ci_data(session, [repo_id], run_ids=changed_ids)
    for offset in range(0, len(changed), 500):
        values = []
        for run in changed[offset : offset + 500]:
            data = asdict(run)
            data["id"] = data.pop("run_id")
            data.update(repo_id=repo_id, pr_numbers=list(run.pr_numbers))
            values.append(data)
        statement = insert(WorkflowRun).values(values)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=["id"],
                set_={key: getattr(statement.excluded, key) for key in values[0] if key != "id"},
            )
        )
    after = await load_ci_data(session, [repo_id], run_ids=changed_ids)
    affected = sorted(before.by_pr.keys() | after.by_pr.keys())
    violations = 0
    for offset in range(0, len(affected), 500):
        violations += await derive_prs(
            session, affected[offset : offset + 500], settings=settings, now=now
        )
    await session.execute(
        update(Repository)
        .where(Repository.id == repo_id)
        .values(data_version=Repository.data_version + 1)
    )
    return len(changed), len(affected), violations


async def enrich_repo(ctx: dict[str, Any], repo_full_name: str, kind: str, job_id: str) -> str:
    """arq entry point for the "ci_runs" job of one repository."""
    settings = cast(Settings, ctx["settings"])
    async with run_job(ctx, repo_full_name, kind, job_id) as job:
        if job is None:
            return "skipped_locked"
        repo = job.repo
        log = logger.bind(job=job_id, repo=repo_full_name, phase=kind)
        if not settings.github_token:
            await job.finish("failed", "missing_token")
            log.warning("enrichment_missing_token")
            return "missing_token"
        stats: dict[str, Any] = {}
        changed = False
        adapter = cast(SourceAdapter, ctx["adapter"])
        ref = RepoRef(repo.owner, repo.name)
        if kind == "ci_runs" and settings.ci_source == "actions" and repo.covered_since:
            previous = await last_success(ctx, repo.id, "ci_runs")
            # After the first pass, re-read only the last two days of runs.
            start = now_for(ctx) - timedelta(days=2) if previous else repo.covered_since
            runs = await adapter.ci_runs(ref, created_from=start, created_to=now_for(ctx))
            async with sessions_for(ctx)() as session, session.begin():
                updates, affected, violations = await save_runs(
                    session, repo.id, runs, settings=settings, now=now_for(ctx)
                )
            stats = {
                "runs_fetched": len(runs),
                "runs_changed": updates,
                "prs_rederived": affected,
                "invariant_violations": violations,
            }
            changed = updates > 0
        if changed:
            await enqueue_precompute(ctx, repo)
        job.stats = stats
        await job.finish()
        log.info("enrichment_completed", **stats)
    return job.result
