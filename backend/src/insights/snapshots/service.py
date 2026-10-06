"""Snapshot delivery: readiness checks, cache and database lookup, and pure computation."""

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insights.analytics import ANALYTICS_VERSION, derive_key
from insights.analytics.dataset import Dataset, RepoData, SnapshotParams
from insights.analytics.insight import insight_view
from insights.analytics.snapshot import build_snapshot, canonical, etag, identifiers, rounded
from insights.config import Settings
from insights.db.dataset import load_dataset
from insights.db.models import Repository, Snapshot, SyncJob
from insights.redis import snapshot_key
from insights.snapshots.caching import Reply, alive, cache_ttl, matches_etag, snapshot_reply
from insights.snapshots.errors import ResourceError

logger = structlog.get_logger(__name__)


def not_found() -> ResourceError:
    """Construct the shared 404 domain error without exposing resource existence details."""
    return ResourceError(404, "not-found", "Not found", "The requested resource was not found.")


@asynccontextmanager
async def consistent_read(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Read-only REPEATABLE READ session, so readiness checks and loads see one DB state."""
    async with sessions() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield session
        finally:
            await session.rollback()


class SnapshotService:
    """Serves snapshots for one request or job at a fixed observation time."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        settings: Settings,
        now: datetime,
    ) -> None:
        """Bind database/cache dependencies and a fixed request or job observation time."""
        self.sessions, self.redis, self.settings, self.now = sessions, redis, settings, now

    async def cache(self, operation: Awaitable[Any]) -> Any:
        """Treat Redis reads/writes as best effort; callers fall back to database/computation.

        This handles cache transport failures only, not SQL or analytics errors.
        """
        try:
            return await operation
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("cache_unavailable", error_type=type(exc).__name__)
            return None

    async def metadata(self, session: AsyncSession, params: SnapshotParams) -> Dataset | Reply:
        """Check repo readiness; return a metadata-only Dataset or a 202 pending Reply.

        Pending reasons, checked in order: never_synced, backfill (coverage starts after the
        period), open_sweep, rederive (derive key changed), stale (no sync since period start).
        Raises 503 when a repo pending for any reason but rederive has a missing_token,
        auth_error or not_found sync status and no queued or running sync job; with an active
        job (for example after the configuration was fixed) the reply stays 202 with progress.
        """
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
            # The first unmet condition is the reason shown to the user; order matters.
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
                job = None
                if repo is not None:
                    kinds = ["rederive"] if reason == "rederive" else ["incremental", "backfill"]
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
                # Rederive works from stored data, so GitHub access problems do not block it.
                # A queued or running sync may already use fixed settings, so report progress.
                if (
                    reason != "rederive"
                    and job is None
                    and status in {"missing_token", "auth_error", "not_found"}
                ):
                    blocked.append({"repo": name, "last_sync_status": status})
                pending.append(
                    {
                        "repo": name,
                        "covered_since": repo.covered_since if repo else None,
                        "required_since": start,
                        "open_sweep_done": bool(repo and repo.last_open_sweep_at),
                        "last_sync_status": status,
                        "reason": reason,
                        "job": {"status": job.status, "phase": job.phase} if job else None,
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
                        repo_id=repo.id,
                    )
                )
        if blocked:
            raise ResourceError(
                503,
                "data-unavailable",
                "Data unavailable",
                "Data for the requested period cannot currently be synced.",
                extensions={"repos": blocked},
            )
        if pending:
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
                {"Retry-After": "30"},
            )
        return Dataset(
            tuple(repo_data),
            (),
            (),
            params.period_from,
            params.period_to,
        )

    @staticmethod
    def cache_created(raw: Any) -> datetime | None:
        """Parse cached creation time from bytes/text, or return None for invalid values."""
        try:
            return datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else raw)
        except (ValueError, TypeError):
            return None

    async def put_cache(
        self, sid: str, body: bytes, view: bytes, tag: str, created_at: datetime
    ) -> None:
        """Best-effort cache of the full snapshot, its public view and the view's ETag.

        Entries expire with the snapshot's remaining retention.
        """
        ttl = cache_ttl(created_at, self.now)
        if ttl:
            pipe = self.redis.pipeline()
            pipe.hset(
                snapshot_key(sid),
                mapping={
                    "body": body,
                    "view": view,
                    "etag": tag,
                    "created_at": created_at.isoformat(),
                },
            )
            pipe.expire(snapshot_key(sid), ttl)
            await self.cache(pipe.execute())

    async def cached_reply(self, sid: str, conditional: str | None) -> Reply | None:
        """Serve a retained Redis view or matching 304; return None on a miss/cache failure."""
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
                    return snapshot_reply(sid, b"", tag, conditional)
        fields = await self.cache(
            cast(Awaitable[Any], self.redis.hmget(key, ["view", "etag", "created_at"]))
        )
        if (
            fields
            and fields[0] is not None
            and fields[1]
            and (created := self.cache_created(fields[2]))
            and alive(created, self.now)
        ):
            return snapshot_reply(sid, fields[0], fields[1].decode(), conditional)
        return None

    async def persisted(self, session: AsyncSession, sid: str) -> tuple[bytes, bytes, str] | None:
        """Load a retained database snapshot as (body, view, etag) and warm Redis.

        Returns None when the row is absent or expired.
        """
        row = await session.get(Snapshot, sid)
        if row is None or not alive(row.created_at, self.now):
            return None
        body, view = canonical(row.payload), canonical(insight_view(row.payload))
        await self.put_cache(sid, body, view, row.etag, row.created_at)
        return body, view, row.etag

    async def by_id(self, sid: str) -> bytes:
        """Return the full internal snapshot within retention for narrative evidence.

        It never recomputes from newer repo data; old analytics-version snapshots remain
        readable by ID until they expire. Raises 404 when absent or expired.
        """
        fields = await self.cache(
            cast(Awaitable[Any], self.redis.hmget(snapshot_key(sid), ["body", "created_at"]))
        )
        if (
            fields
            and fields[0] is not None
            and (created := self.cache_created(fields[1]))
            and alive(created, self.now)
        ):
            return cast(bytes, fields[0])
        async with self.sessions() as session:
            stored = await self.persisted(session, sid)
        if stored is None:
            raise not_found()
        return stored[0]

    async def delivery(self, params: SnapshotParams, conditional: str | None = None) -> Reply:
        """Return the public insight for params, computing and persisting the snapshot on a miss.

        The ID comes from repo metadata alone, so Redis and the snapshots table are checked
        before PRs are loaded. Returns a 202 Reply while data is pending.
        """
        started = perf_counter()
        async with consistent_read(self.sessions) as session:
            metadata = await self.metadata(session, params)
            if isinstance(metadata, Reply):
                return metadata
            # Cheapest source first: Redis, then the snapshots table, then a full computation.
            sid, _, _ = identifiers(metadata, params)
            cached = await self.cached_reply(sid, conditional)
            if cached is not None:
                return cached
            stored = await self.persisted(session, sid)
            if stored is not None:
                _, view, tag = stored
                return snapshot_reply(sid, view, tag, conditional)
            # Load in the same REPEATABLE READ view that produced the ID, so the data matches it.
            load_started = perf_counter()
            dataset = await load_dataset(session, params, metadata=metadata)
            load_ms = (perf_counter() - load_started) * 1000
            compute_started = perf_counter()
            # SQL has already materialized immutable records; the CPU work uses no DB session
            # and runs off the event loop while retaining the metadata view used for this ID.
            payload = await asyncio.to_thread(build_snapshot, dataset, params=params)
            compute_ms = (perf_counter() - compute_started) * 1000
        # The full snapshot stays internal; the ETag identifies the public view's bytes.
        body, view = canonical(payload), canonical(insight_view(payload))
        tag = etag(view)
        async with self.sessions() as session, session.begin():
            statement = insert(Snapshot).values(
                snapshot_id=sid,
                params=params.canonical_dict(),
                repos=list(params.repos),
                period_from=params.period_from,
                period_to=params.period_to,
                analytics_version=ANALYTICS_VERSION,
                payload=payload,
                etag=tag,
                created_at=self.now,
            )
            # A concurrent request may have stored this ID; refreshing created_at extends retention.
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["snapshot_id"],
                    set_={"created_at": statement.excluded.created_at},
                )
            )
        await self.put_cache(sid, body, view, tag, self.now)
        logger.info(
            "snapshot_computed",
            snapshot_id=sid,
            duration_ms=(perf_counter() - started) * 1000,
            load_ms=load_ms,
            compute_ms=compute_ms,
            merged_prs=payload["meta"]["sample"]["merged_prs"],
        )
        return snapshot_reply(sid, view, tag, conditional)
