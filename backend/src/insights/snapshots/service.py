import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast

import orjson
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insights.analytics import ANALYTICS_VERSION, derive_key
from insights.analytics.dataset import Dataset, RepoData, SnapshotParams
from insights.analytics.rows import build_pr_rows
from insights.analytics.snapshot import build_snapshot, canonical, etag, identifiers, rounded
from insights.api.caching import Reply, alive, cache_ttl, matches_etag, page_rows, snapshot_reply
from insights.api.errors import ProblemError
from insights.api.params import PrFilters
from insights.config import Settings
from insights.db.dataset import load_dataset
from insights.db.models import Repository, Snapshot, SyncJob
from insights.redis import rows_key, snapshot_key

logger = structlog.get_logger(__name__)


def not_found() -> ProblemError:
    return ProblemError(404, "not-found", "Not found", "The requested resource was not found.")


@asynccontextmanager
async def consistent_read(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessions() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield session
        finally:
            await session.rollback()


class SnapshotService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        settings: Settings,
        now: datetime,
    ) -> None:
        self.sessions, self.redis, self.settings, self.now = sessions, redis, settings, now

    async def cache(self, operation: Awaitable[Any]) -> Any:
        try:
            return await operation
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("cache_unavailable", error_type=type(exc).__name__)
            return None

    async def metadata(self, session: AsyncSession, params: SnapshotParams) -> Dataset | Reply:
        repositories = {
            r.full_name_lower: r
            for r in await session.scalars(
                select(Repository).where(
                    Repository.full_name_lower.in_([r.lower() for r in params.repos])
                )
            )
        }
        start = datetime.combine(params.period_from, datetime.min.time(), UTC)
        current_key = derive_key(self.settings.location_dimension, self.settings.directory_depth)
        pending: list[dict[str, Any]] = []
        blocked = []
        repo_data = []
        for name in params.repos:
            repo = repositories.get(name.lower())
            reason = None
            if repo is None or repo.covered_since is None or repo.last_synced_at is None:
                reason = "never_synced"
            elif repo.covered_since > start:
                reason = "backfill"
            elif repo.last_open_sweep_at is None:
                reason = "open_sweep"
            elif repo.derived_key != current_key:
                reason = "rederive"
            elif repo.last_synced_at <= start:
                reason = "stale"
            if reason:
                status = repo.last_sync_status if repo else "never"
                if reason != "rederive" and status in {"missing_token", "auth_error", "not_found"}:
                    blocked.append({"repo": name, "last_sync_status": status})
                job = None
                if repo is not None:
                    kinds = (
                        ["rederive"]
                        if reason == "rederive"
                        else ["manual", "incremental", "backfill"]
                    )
                    job = await session.scalar(
                        select(SyncJob)
                        .where(
                            SyncJob.repo_id == repo.id,
                            SyncJob.kind.in_(kinds),
                            SyncJob.status.in_(["queued", "running"]),
                        )
                        .order_by(SyncJob.created_at.desc(), SyncJob.id.desc())
                        .limit(1)
                    )
                pending.append(
                    {
                        "repo": name,
                        "covered_since": repo.covered_since if repo else None,
                        "required_since": start,
                        "open_sweep_done": bool(repo and repo.last_open_sweep_at),
                        "last_sync_status": status,
                        "reason": reason,
                        "job": {
                            "id": str(job.id),
                            "status": job.status,
                            "phase": job.phase,
                            "url": f"/v1/sync-jobs/{job.id}",
                        }
                        if job
                        else None,
                    }
                )
            elif (
                repo is not None
                and repo.covered_since is not None
                and repo.last_synced_at is not None
            ):
                repo_data.append(
                    RepoData(
                        name,
                        repo.data_version,
                        repo.covered_since,
                        repo.last_synced_at,
                        repo.last_sync_status,
                    )
                )
        if blocked:
            raise ProblemError(
                503,
                "data-unavailable",
                "Data unavailable",
                "Data for the requested period cannot currently be synced.",
                extensions={"repos": blocked},
            )
        if pending:
            headers = {"Retry-After": "30"}
            if pending[0]["job"]:
                headers["Location"] = pending[0]["job"]["url"]
            return Reply(
                canonical(
                    rounded(
                        {
                            "status": "pending",
                            "detail": "Data for the requested period is still being synced.",
                            "retry_after_seconds": 30,
                            "repos": pending,
                        }
                    )
                ),
                202,
                headers,
            )
        return Dataset(
            tuple(repo_data),
            (),
            (),
            (),
            params.period_from,
            params.period_to,
            current_day=params.period_to == self.now.date(),
        )

    @staticmethod
    def cache_created(raw: Any) -> datetime | None:
        try:
            return datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else raw)
        except (ValueError, TypeError):
            return None

    async def put_cache(self, sid: str, body: bytes, tag: str, created_at: datetime) -> None:
        ttl = cache_ttl(created_at, self.now)
        if ttl:
            pipe = self.redis.pipeline()
            pipe.hset(
                snapshot_key(sid),
                mapping={"body": body, "etag": tag, "created_at": created_at.isoformat()},
            )
            pipe.expire(snapshot_key(sid), ttl)
            await self.cache(pipe.execute())

    async def cached_reply(
        self, sid: str, conditional: str | None, *, immutable: bool
    ) -> Reply | None:
        key = snapshot_key(sid)
        if conditional:
            fields = await self.cache(
                cast(Awaitable[Any], self.redis.hmget(key, ["etag", "created_at"]))
            )
            if (
                fields
                and fields[0]
                and (created := self.cache_created(fields[1]))
                and alive(created, self.now)
            ):
                tag = fields[0].decode()
                if matches_etag(conditional, tag):
                    return snapshot_reply(sid, b"", tag, conditional, immutable=immutable)
        cached = await self.cache(cast(Awaitable[Any], self.redis.hgetall(key)))
        if (
            cached
            and (created := self.cache_created(cached.get(b"created_at")))
            and alive(created, self.now)
        ):
            body, tag = cached.get(b"body"), cached.get(b"etag")
            if body is not None and tag:
                return snapshot_reply(sid, body, tag.decode(), conditional, immutable=immutable)
        return None

    async def persisted_reply(
        self, session: AsyncSession, sid: str, conditional: str | None, *, immutable: bool
    ) -> Reply | None:
        row = await session.get(Snapshot, sid)
        if row is None or not alive(row.created_at, self.now):
            return None
        body = canonical(row.payload)
        await self.put_cache(sid, body, row.etag, row.created_at)
        return snapshot_reply(sid, body, row.etag, conditional, immutable=immutable)

    async def by_id(self, sid: str, conditional: str | None = None) -> Reply:
        cached = await self.cached_reply(sid, conditional, immutable=True)
        if cached is not None:
            return cached
        async with self.sessions() as session:
            stored = await self.persisted_reply(session, sid, conditional, immutable=True)
        if stored is None:
            raise not_found()
        return stored

    async def delivery(self, params: SnapshotParams, conditional: str | None = None) -> Reply:
        started = perf_counter()
        async with consistent_read(self.sessions) as session:
            metadata = await self.metadata(session, params)
            if isinstance(metadata, Reply):
                return metadata
            sid, _, _ = identifiers(metadata, params)
            cached = await self.cached_reply(sid, conditional, immutable=False)
            if cached is not None:
                return cached
            stored = await self.persisted_reply(session, sid, conditional, immutable=False)
            if stored is not None:
                return stored
            load_started = perf_counter()
            dataset = await load_dataset(session, params, now=self.now)
            load_ms = (perf_counter() - load_started) * 1000
            compute_started = perf_counter()
            payload = await asyncio.to_thread(build_snapshot, dataset, params=params)
            compute_ms = (perf_counter() - compute_started) * 1000
        body, tag = canonical(payload), etag(canonical(payload))
        async with self.sessions() as session, session.begin():
            statement = insert(Snapshot).values(
                snapshot_id=sid,
                params=params.canonical_dict(),
                repos=list(params.repos),
                period_from=params.period_from,
                period_to=params.period_to,
                data_versions={r.repo: r.data_version for r in dataset.repos},
                analytics_version=ANALYTICS_VERSION,
                payload=payload,
                etag=tag,
                created_at=self.now,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["snapshot_id"],
                    set_={"created_at": statement.excluded.created_at},
                )
            )
        await self.put_cache(sid, body, tag, self.now)
        logger.info(
            "snapshot_computed",
            snapshot_id=sid,
            duration_ms=(perf_counter() - started) * 1000,
            load_ms=load_ms,
            compute_ms=compute_ms,
            merged_prs=payload["meta"]["sample"]["merged_prs"],
        )
        return snapshot_reply(sid, body, tag, conditional, immutable=False)

    async def rows(
        self, params: SnapshotParams, filters: PrFilters, limit: int, cursor: str | None
    ) -> Reply:
        async with consistent_read(self.sessions) as session:
            metadata = await self.metadata(session, params)
            if isinstance(metadata, Reply):
                return metadata
            sid, _, _ = identifiers(metadata, params)
            cached = await self.cache(self.redis.get(rows_key(sid)))
            rows = None
            if cached:
                with suppress(orjson.JSONDecodeError):
                    rows = orjson.loads(cached)
            if rows is None:
                dataset = await load_dataset(session, params, now=self.now)
                rows = await asyncio.to_thread(build_pr_rows, dataset)
                await self.cache(self.redis.set(rows_key(sid), canonical(rows), ex=3600))
        page = page_rows(
            cast(list[dict[str, Any]], rows), sid, metadata.as_of, filters, limit, cursor
        )
        return Reply(canonical(rounded(page)), 200, {"Cache-Control": "private, max-age=60"})
