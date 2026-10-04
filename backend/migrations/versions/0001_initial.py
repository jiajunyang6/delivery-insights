"""Consolidated initial schema for the unreleased application.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-02 15:55:18.648749

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("full_name_lower", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.Text(), nullable=True),
        sa.Column("tracked", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("covered_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backfill_target_days", sa.Integer(), nullable=True),
        sa.Column("backfill_cursor", sa.Text(), nullable=True),
        sa.Column("sync_watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_open_sweep_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.Text(), server_default=sa.text("'never'"), nullable=False),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("data_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("derived_key", sa.Text(), nullable=True),
        sa.Column("links_pending", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("full_name_lower"),
    )
    op.create_table(
        "snapshots",
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("repos", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("analytics_version", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("etag", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index("ix_snapshots_created", "snapshots", ["created_at"], unique=False)
    op.create_table(
        "narratives",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("lang", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("pack_hash", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("generated_by", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("etag", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.snapshot_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "audience", "lang", "prompt_version", "model_id", "pack_hash"
        ),
    )
    op.create_table(
        "ownership_rules",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("owners", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "source", "line_no"),
    )
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_excerpt", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("is_draft", sa.Boolean(), nullable=False),
        sa.Column("author_login", sa.Text(), nullable=True),
        sa.Column("author_association", sa.Text(), nullable=False),
        sa.Column("is_bot_author", sa.Boolean(), nullable=False),
        sa.Column("base_ref", sa.Text(), nullable=False),
        sa.Column("head_ref", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merge_commit_oid", sa.Text(), nullable=True),
        sa.Column("additions", sa.Integer(), nullable=False),
        sa.Column("deletions", sa.Integer(), nullable=False),
        sa.Column(
            "labels", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "number"),
    )
    op.create_index(
        "ix_pr_merge_commit", "pull_requests", ["repo_id", "merge_commit_oid"], unique=False
    )
    op.create_index("ix_pr_repo_created", "pull_requests", ["repo_id", "created_at"], unique=False)
    op.create_index("ix_pr_repo_merged", "pull_requests", ["repo_id", "merged_at"], unique=False)
    op.create_index("ix_pr_repo_state", "pull_requests", ["repo_id", "state"], unique=False)
    op.create_index("ix_pr_repo_updated", "pull_requests", ["repo_id", "updated_at"], unique=False)
    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sync_jobs_repo_created",
        "sync_jobs",
        ["repo_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("workflow_name", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "pr_numbers",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runs_repo_created", "workflow_runs", ["repo_id", "created_at"], unique=False
    )
    op.create_index("ix_runs_repo_sha", "workflow_runs", ["repo_id", "head_sha"], unique=False)
    op.create_table(
        "pr_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("pr_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_login", sa.Text(), nullable=True),
        sa.Column("actor_is_bot", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pr_id", "dedup_key"),
    )
    op.create_index("ix_events_kind_time", "pr_events", ["kind", "occurred_at"], unique=False)
    op.create_index("ix_events_pr_time", "pr_events", ["pr_id", "occurred_at"], unique=False)
    op.create_table(
        "pr_facts",
        sa.Column("pr_id", sa.BigInteger(), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("is_bot_author", sa.Boolean(), nullable=False),
        sa.Column("is_backport", sa.Boolean(), nullable=False),
        sa.Column("external_contributor", sa.Boolean(), nullable=False),
        sa.Column("first_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_approval_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coding_hours", sa.Float(), nullable=True),
        sa.Column("pickup_hours", sa.Float(), nullable=True),
        sa.Column("review_hours", sa.Float(), nullable=True),
        sa.Column("merge_hours", sa.Float(), nullable=True),
        sa.Column("cycle_hours", sa.Float(), nullable=True),
        sa.Column("review_rounds", sa.Integer(), nullable=False),
        sa.Column("feedback_before_approval", sa.Integer(), nullable=False),
        sa.Column("commits_after_first_review", sa.Integer(), nullable=False),
        sa.Column("updates_after_approval", sa.Integer(), nullable=False),
        sa.Column("distinct_approvers", sa.Integer(), nullable=False),
        sa.Column("second_approval_wait_hours", sa.Float(), nullable=True),
        sa.Column("merged_without_approval", sa.Boolean(), nullable=False),
        sa.Column("review_requested_before_first_review", sa.Boolean(), nullable=False),
        sa.Column("human_reviews", sa.Integer(), nullable=False),
        sa.Column("size_lines", sa.Integer(), nullable=False),
        sa.Column("size_bucket", sa.Text(), nullable=False),
        sa.Column("locations", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("location_source", sa.Text(), nullable=False),
        sa.Column("is_revert", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("reverts_pr_id", sa.BigInteger(), nullable=True),
        sa.Column("reverted_by_pr_id", sa.BigInteger(), nullable=True),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reland_of_pr_id", sa.BigInteger(), nullable=True),
        sa.Column("close_class", sa.Text(), nullable=True),
        sa.Column("state_at_close", sa.Text(), nullable=True),
        sa.Column("late_rejection", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("ci_covered", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("derive_key", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reland_of_pr_id"], ["pull_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reverted_by_pr_id"], ["pull_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reverts_pr_id"], ["pull_requests.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("pr_id"),
    )
    op.create_index("ix_facts_repo_end", "pr_facts", ["repo_id", "end_at"], unique=False)
    op.create_index("ix_facts_repo_merged", "pr_facts", ["repo_id", "merged_at"], unique=False)
    op.create_index("ix_facts_repo_ready", "pr_facts", ["repo_id", "ready_at"], unique=False)
    op.create_table(
        "pr_files",
        sa.Column("pr_id", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("pr_id", "path"),
    )
    op.create_table(
        "pr_intervals",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("pr_id", sa.BigInteger(), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pr_id", "seq"),
    )
    op.create_index(
        "ix_intervals_repo_state_start",
        "pr_intervals",
        ["repo_id", "state", "start_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_intervals_repo_state_start", table_name="pr_intervals")
    op.drop_table("pr_intervals")
    op.drop_table("pr_files")
    op.drop_index("ix_facts_repo_ready", table_name="pr_facts")
    op.drop_index("ix_facts_repo_merged", table_name="pr_facts")
    op.drop_index("ix_facts_repo_end", table_name="pr_facts")
    op.drop_table("pr_facts")
    op.drop_index("ix_events_pr_time", table_name="pr_events")
    op.drop_index("ix_events_kind_time", table_name="pr_events")
    op.drop_table("pr_events")
    op.drop_index("ix_runs_repo_sha", table_name="workflow_runs")
    op.drop_index("ix_runs_repo_created", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index("ix_sync_jobs_repo_created", table_name="sync_jobs")
    op.drop_table("sync_jobs")
    op.drop_index("ix_pr_repo_updated", table_name="pull_requests")
    op.drop_index("ix_pr_repo_state", table_name="pull_requests")
    op.drop_index("ix_pr_repo_merged", table_name="pull_requests")
    op.drop_index("ix_pr_repo_created", table_name="pull_requests")
    op.drop_index("ix_pr_merge_commit", table_name="pull_requests")
    op.drop_table("pull_requests")
    op.drop_table("ownership_rules")
    op.drop_table("narratives")
    op.drop_index("ix_snapshots_created", table_name="snapshots")
    op.drop_table("snapshots")
    op.drop_table("repositories")
