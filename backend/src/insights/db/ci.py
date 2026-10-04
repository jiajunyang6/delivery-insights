"""Load workflow runs linked to PRs as immutable CiRun records."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import BigInteger, and_, any_, literal, select, union
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from insights.db.models import PrEvent, PrFact, PullRequest, WorkflowRun
from insights.domain import CiRun


def run_from_row(row: WorkflowRun) -> CiRun:
    return CiRun(
        row.id,
        row.workflow_name,
        row.event,
        row.head_sha,
        row.status,
        row.conclusion,
        row.run_attempt,
        row.created_at,
        row.run_started_at,
        row.updated_at,
        tuple(row.pr_numbers),
    )


@dataclass(frozen=True, slots=True)
class CiData:
    by_pr: dict[int, tuple[CiRun, ...]]
    runs: tuple[tuple[int, CiRun], ...]


async def load_ci_data(
    session: AsyncSession,
    repo_ids: Sequence[int],
    *,
    pr_ids: Sequence[int] | None = None,
    run_ids: Sequence[int] | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    flow_only: bool = False,
) -> CiData:
    """Link runs to PRs by explicit PR number or by head SHA matching a stored PR commit.

    The SHA join covers runs that carry no PR numbers, such as runs for fork PRs. `flow_only`
    keeps human, non-backport PRs that became ready for review.
    """
    base = select(
        WorkflowRun.id, PullRequest.id.label("pr_id"), PullRequest.number, PullRequest.repo_id
    ).select_from(WorkflowRun)
    explicit = base.join(
        PullRequest,
        and_(
            PullRequest.repo_id == WorkflowRun.repo_id,
            PullRequest.number == any_(WorkflowRun.pr_numbers),
        ),
    )
    commits = base.join(
        PrEvent,
        and_(
            PrEvent.kind == "commit",
            PrEvent.payload["oid"].as_string() == WorkflowRun.head_sha,
        ),
    ).join(
        PullRequest,
        and_(PullRequest.id == PrEvent.pr_id, PullRequest.repo_id == WorkflowRun.repo_id),
    )
    conditions = [WorkflowRun.repo_id.in_(repo_ids), WorkflowRun.event == "pull_request"]
    if pr_ids is not None:
        conditions.append(PullRequest.id == any_(literal(list(pr_ids), type_=ARRAY(BigInteger))))
    if run_ids is not None:
        conditions.append(WorkflowRun.id == any_(literal(list(run_ids), type_=ARRAY(BigInteger))))
    if created_from is not None:
        conditions.append(WorkflowRun.created_at >= created_from)
    if created_to is not None:
        conditions.append(WorkflowRun.created_at < created_to)
    if flow_only:
        explicit = explicit.join(PrFact, PrFact.pr_id == PullRequest.id)
        commits = commits.join(PrFact, PrFact.pr_id == PullRequest.id)
        conditions += [~PrFact.is_bot_author, ~PrFact.is_backport, PrFact.ready_at.is_not(None)]
    links = (
        await session.execute(union(explicit.where(*conditions), commits.where(*conditions)))
    ).all()
    ids = sorted({row[0] for row in links})
    if not ids:
        return CiData({}, ())
    rows = (
        await session.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.id == any_(literal(ids, type_=ARRAY(BigInteger))))
            .order_by(WorkflowRun.id)
        )
    ).all()
    numbers: dict[int, set[int]] = defaultdict(set)
    by_pr: dict[int, list[CiRun]] = defaultdict(list)
    runs = {row.id: run_from_row(row) for row in rows}
    for run_id, pr_id, number, _ in sorted(links):
        numbers[run_id].add(number)
        by_pr[pr_id].append(runs[run_id])
    return CiData(
        {
            identifier: tuple(sorted(values, key=lambda r: (r.created_at, r.run_id)))
            for identifier, values in by_pr.items()
        },
        tuple(
            (row.repo_id, replace(runs[row.id], pr_numbers=tuple(sorted(numbers[row.id]))))
            for row in rows
        ),
    )
