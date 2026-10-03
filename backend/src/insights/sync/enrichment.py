from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import BigInteger, any_, delete, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession

from insights.config import Settings
from insights.db.ci import load_ci_data, run_from_row
from insights.db.models import OwnershipRule as StoredRule
from insights.db.models import PrFact, Repository, SyncJob, WorkflowRun
from insights.domain import CiRun, OwnershipRule, RepoRef
from insights.sources.base import SourceAdapter
from insights.sources.github.client import GitHubError
from insights.sync.derive import derive_prs
from insights.sync.jobs import now_for, repository_lock, sessions_for, set_job
from insights.sync.queue import enqueue_sync, ensure_repo

logger = structlog.get_logger(__name__)


async def save_runs(
    session: AsyncSession, repo_id: int, runs: list[CiRun], *, settings: Settings, now: datetime
) -> tuple[int, int]:
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
        return 0, 0
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
    for offset in range(0, len(affected), 500):
        await derive_prs(session, affected[offset : offset + 500], settings=settings, now=now)
    await session.execute(
        update(Repository)
        .where(Repository.id == repo_id)
        .values(data_version=Repository.data_version + 1)
    )
    return len(changed), len(affected)


async def save_ownership(
    session: AsyncSession, repo_id: int, rules: list[OwnershipRule], *, now: datetime
) -> tuple[bool, bool]:
    old = [
        OwnershipRule(r.source, r.pattern, tuple(r.owners), r.line_no)
        for r in await session.scalars(
            select(StoredRule)
            .where(StoredRule.repo_id == repo_id)
            .order_by(StoredRule.source, StoredRule.line_no)
        )
    ]
    new = sorted(rules, key=lambda r: (r.source, r.line_no))
    code_changed = [r for r in old if r.source == "codeowners"] != [
        r for r in new if r.source == "codeowners"
    ]
    area_changed = [r for r in old if r.source == "area_owners"] != [
        r for r in new if r.source == "area_owners"
    ]
    if not code_changed and not area_changed:
        return False, False
    await session.execute(delete(StoredRule).where(StoredRule.repo_id == repo_id))
    if new:
        await session.execute(
            insert(StoredRule),
            [
                {**asdict(r), "owners": list(r.owners), "repo_id": repo_id, "fetched_at": now}
                for r in new
            ],
        )
    values: dict[str, Any] = {"data_version": Repository.data_version + 1}
    if code_changed:
        values["derived_key"] = None
        await session.execute(
            update(PrFact).where(PrFact.repo_id == repo_id).values(derive_key=None)
        )
    await session.execute(update(Repository).where(Repository.id == repo_id).values(**values))
    return code_changed, area_changed


async def execute(ctx: dict[str, Any], repo_full_name: str, kind: str, job_id: str) -> str:
    settings = cast(Settings, ctx["settings"])
    async with sessions_for(ctx)() as session:
        repo = await ensure_repo(session, repo_full_name, now_for(ctx))
        await session.commit()
    async with repository_lock(ctx, repo_full_name, job_id) as acquired:
        if not acquired:
            return "skipped_locked"
        await set_job(ctx, job_id, status="running", phase=kind, started_at=now_for(ctx))
        log = logger.bind(job=job_id, repo=repo_full_name, phase=kind)
        if not settings.github_token:
            await set_job(
                ctx, job_id, status="failed", error="missing_token", finished_at=now_for(ctx)
            )
            log.warning("enrichment_missing_token")
            return "missing_token"
        stats: dict[str, Any] = {}
        changed = False
        try:
            adapter = cast(SourceAdapter, ctx["adapter"])
            ref = RepoRef(repo.owner, repo.name)
            if kind == "ci_runs" and settings.ci_source == "actions" and repo.covered_since:
                async with sessions_for(ctx)() as session:
                    previous = await session.scalar(
                        select(SyncJob.finished_at)
                        .where(
                            SyncJob.repo_id == repo.id,
                            SyncJob.kind == "ci_runs",
                            SyncJob.status == "succeeded",
                        )
                        .order_by(SyncJob.finished_at.desc())
                        .limit(1)
                    )
                start = now_for(ctx) - timedelta(days=2) if previous else repo.covered_since
                runs = await adapter.ci_runs(ref, created_from=start, created_to=now_for(ctx))
                async with sessions_for(ctx)() as session, session.begin():
                    updates, affected = await save_runs(
                        session, repo.id, runs, settings=settings, now=now_for(ctx)
                    )
                stats = {
                    "runs_fetched": len(runs),
                    "runs_changed": updates,
                    "prs_rederived": affected,
                }
                changed = updates > 0
            elif kind == "ownership":
                rules = await adapter.ownership_rules(ref)
                async with sessions_for(ctx)() as session, session.begin():
                    code, area = await save_ownership(session, repo.id, rules, now=now_for(ctx))
                changed = code or area
                stats = {
                    "rules": len(rules),
                    "codeowners_changed": code,
                    "area_owners_changed": area,
                }
                if code:
                    async with sessions_for(ctx)() as session:
                        await enqueue_sync(
                            ctx["redis"], session, repo_full_name, "rederive", now=now_for(ctx)
                        )
            if changed:
                await ctx["redis"].enqueue_job(
                    "precompute_snapshots",
                    repo_full_name,
                    _job_id=f"precompute:{repo_full_name.lower()}",
                )
        except Exception as exc:
            error = str(exc) if isinstance(exc, GitHubError) else type(exc).__name__
            await set_job(ctx, job_id, status="failed", error=error[:500], finished_at=now_for(ctx))
            log.error("enrichment_failed", error=error[:500])
            return "failed"
        await set_job(ctx, job_id, status="succeeded", stats=stats, finished_at=now_for(ctx))
        log.info("enrichment_completed", **stats)
        return "succeeded"


async def sync_ci_runs(ctx: dict[str, Any], repo_full_name: str, kind: str, job_id: str) -> str:
    return await execute(ctx, repo_full_name, kind, job_id)


async def sync_ownership(ctx: dict[str, Any], repo_full_name: str, kind: str, job_id: str) -> str:
    return await execute(ctx, repo_full_name, kind, job_id)
