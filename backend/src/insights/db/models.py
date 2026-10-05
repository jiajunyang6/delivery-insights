"""SQLAlchemy schema for synced source data, derived facts, snapshots and the job ledger."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Repository(Base):
    """A repository and its sync state.

    `covered_since` starts the fully synced range, `sync_watermark` is the newest PR update
    seen, `backfill_cursor` is where backfill resumes, and `data_version` increments when
    stored data changes, and `derived_key` is set once every PR is derived with that key.
    """

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(Text)
    full_name_lower: Mapped[str] = mapped_column(Text, unique=True)
    owner: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    default_branch: Mapped[str | None] = mapped_column(Text)
    tracked: Mapped[bool] = mapped_column(Boolean, server_default=text("TRUE"))
    covered_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backfill_target_days: Mapped[int | None] = mapped_column(Integer)
    backfill_cursor: Mapped[str | None] = mapped_column(Text)
    sync_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_open_sweep_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str] = mapped_column(Text, server_default=text("'never'"))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    data_version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    derived_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PullRequest(Base):
    __tablename__ = "pull_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    is_draft: Mapped[bool] = mapped_column(Boolean)
    author_login: Mapped[str | None] = mapped_column(Text)
    is_bot_author: Mapped[bool] = mapped_column(Boolean)
    base_ref: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    additions: Mapped[int] = mapped_column(Integer)
    deletions: Mapped[int] = mapped_column(Integer)
    labels: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    content_hash: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("repo_id", "number"),
        Index("ix_pr_repo_merged", "repo_id", "merged_at"),
        Index("ix_pr_repo_created", "repo_id", "created_at"),
        Index("ix_pr_repo_updated", "repo_id", "updated_at"),
        Index("ix_pr_repo_state", "repo_id", "state"),
    )


class PrEvent(Base):
    __tablename__ = "pr_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pr_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_login: Mapped[str | None] = mapped_column(Text)
    actor_is_bot: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    dedup_key: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("pr_id", "dedup_key"),
        Index("ix_events_pr_time", "pr_id", "occurred_at"),
        Index("ix_events_kind_time", "kind", "occurred_at"),
    )


class PrFile(Base):
    __tablename__ = "pr_files"

    pr_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(Text, primary_key=True)


class PrFact(Base):
    __tablename__ = "pr_facts"

    pr_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="CASCADE"), primary_key=True
    )
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    is_bot_author: Mapped[bool] = mapped_column(Boolean)
    is_backport: Mapped[bool] = mapped_column(Boolean)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    coding_hours: Mapped[float | None] = mapped_column(Float)
    pickup_hours: Mapped[float | None] = mapped_column(Float)
    cycle_hours: Mapped[float | None] = mapped_column(Float)
    review_rounds: Mapped[int] = mapped_column(Integer)
    commits_after_first_review: Mapped[int] = mapped_column(Integer)
    size_lines: Mapped[int] = mapped_column(Integer)
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text))
    derive_key: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("ix_facts_repo_merged", "repo_id", "merged_at"),
        Index("ix_facts_repo_ready", "repo_id", "ready_at"),
        Index("ix_facts_repo_end", "repo_id", "end_at"),
    )


class PrInterval(Base):
    __tablename__ = "pr_intervals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pr_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="CASCADE")
    )
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(SmallInteger)
    state: Mapped[str] = mapped_column(Text)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("pr_id", "seq"),
        Index("ix_intervals_repo_state_start", "repo_id", "state", "start_at"),
    )


class Snapshot(Base):
    __tablename__ = "snapshots"

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    repos: Mapped[list[str]] = mapped_column(ARRAY(Text))
    period_from: Mapped[date] = mapped_column(Date)
    period_to: Mapped[date] = mapped_column(Date)
    analytics_version: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    etag: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    __table_args__ = (Index("ix_snapshots_created", "created_at"),)


class Narrative(Base):
    __tablename__ = "narratives"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        Text, ForeignKey("snapshots.snapshot_id", ondelete="CASCADE")
    )
    prompt_version: Mapped[str] = mapped_column(Text)
    pack_hash: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    etag: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    __table_args__ = (UniqueConstraint("snapshot_id", "prompt_version", "model_id", "pack_hash"),)


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    phase: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_sync_jobs_repo_created", "repo_id", created_at.desc()),)
