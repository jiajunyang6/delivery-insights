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
    source_id: Mapped[str] = mapped_column(Text, unique=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    body_excerpt: Mapped[str] = mapped_column(Text, server_default=text("''"))
    url: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    is_draft: Mapped[bool] = mapped_column(Boolean)
    author_login: Mapped[str | None] = mapped_column(Text)
    author_type: Mapped[str] = mapped_column(Text)
    author_association: Mapped[str] = mapped_column(Text)
    is_bot_author: Mapped[bool] = mapped_column(Boolean)
    base_ref: Mapped[str] = mapped_column(Text)
    head_ref: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_by: Mapped[str | None] = mapped_column(Text)
    merge_commit_oid: Mapped[str | None] = mapped_column(Text)
    additions: Mapped[int] = mapped_column(Integer)
    deletions: Mapped[int] = mapped_column(Integer)
    changed_files: Mapped[int] = mapped_column(Integer)
    labels: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    files_truncated: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    content_hash: Mapped[str] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("repo_id", "number"),
        Index("ix_pr_repo_merged", "repo_id", "merged_at"),
        Index("ix_pr_repo_created", "repo_id", "created_at"),
        Index("ix_pr_repo_updated", "repo_id", "updated_at"),
        Index("ix_pr_repo_state", "repo_id", "state"),
        Index("ix_pr_merge_commit", "repo_id", "merge_commit_oid"),
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
    external_contributor: Mapped[bool] = mapped_column(Boolean)
    first_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_approval_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    coding_hours: Mapped[float | None] = mapped_column(Float)
    pickup_hours: Mapped[float | None] = mapped_column(Float)
    review_hours: Mapped[float | None] = mapped_column(Float)
    merge_hours: Mapped[float | None] = mapped_column(Float)
    cycle_hours: Mapped[float | None] = mapped_column(Float)
    review_rounds: Mapped[int] = mapped_column(Integer)
    feedback_before_approval: Mapped[int] = mapped_column(Integer)
    commits_after_first_review: Mapped[int] = mapped_column(Integer)
    force_pushes_after_first_review: Mapped[int] = mapped_column(Integer)
    updates_after_approval: Mapped[int] = mapped_column(Integer)
    distinct_approvers: Mapped[int] = mapped_column(Integer)
    second_approval_wait_hours: Mapped[float | None] = mapped_column(Float)
    merged_without_approval: Mapped[bool] = mapped_column(Boolean)
    review_requested_before_first_review: Mapped[bool] = mapped_column(Boolean)
    human_reviews: Mapped[int] = mapped_column(Integer)
    size_lines: Mapped[int] = mapped_column(Integer)
    size_bucket: Mapped[str] = mapped_column(Text)
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text))
    location_source: Mapped[str] = mapped_column(Text)
    is_revert: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    reverts_pr_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="SET NULL")
    )
    reverted_by_pr_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="SET NULL")
    )
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_reland: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    reland_of_pr_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="SET NULL")
    )
    superseded_by_pr_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="SET NULL")
    )
    close_class: Mapped[str | None] = mapped_column(Text)
    state_at_close: Mapped[str | None] = mapped_column(Text)
    late_rejection: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    ci_covered: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    author_open_prs_at_ready: Mapped[int | None] = mapped_column(Integer)
    ready_weekday: Mapped[int | None] = mapped_column(SmallInteger)
    ready_hour: Mapped[int | None] = mapped_column(SmallInteger)
    derive_key: Mapped[str | None] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
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
        Index("ix_intervals_pr", "pr_id"),
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"))
    workflow_name: Mapped[str] = mapped_column(Text)
    event: Mapped[str] = mapped_column(Text)
    head_sha: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    conclusion: Mapped[str | None] = mapped_column(Text)
    run_attempt: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pr_numbers: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default=text("'{}'"))
    __table_args__ = (
        Index("ix_runs_repo_sha", "repo_id", "head_sha"),
        Index("ix_runs_repo_created", "repo_id", "created_at"),
    )


class OwnershipRule(Base):
    __tablename__ = "ownership_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(Text)
    pattern: Mapped[str] = mapped_column(Text)
    owners: Mapped[list[str]] = mapped_column(ARRAY(Text))
    line_no: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("repo_id", "source", "line_no"),)


class Snapshot(Base):
    __tablename__ = "snapshots"

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    repos: Mapped[list[str]] = mapped_column(ARRAY(Text))
    period_from: Mapped[date] = mapped_column(Date)
    period_to: Mapped[date] = mapped_column(Date)
    data_versions: Mapped[dict[str, Any]] = mapped_column(JSONB)
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
    audience: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    pack_hash: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    etag: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "audience", "lang", "prompt_version", "model_id", "pack_hash"
        ),
    )


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
