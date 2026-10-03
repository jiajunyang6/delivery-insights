"""I/O boundary: materialize one consistent database view for pure analytics."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from insights.analytics.ci import union_intervals
from insights.analytics.classify import ownership_counts
from insights.analytics.dataset import Baseline, Dataset, PrData, RepoData, Review, SnapshotParams
from insights.analytics.timeline import WAITING_STATES, Interval
from insights.db.ci import load_ci_data
from insights.db.models import OwnershipRule as StoredRule
from insights.db.models import PrEvent, PrFact, PrInterval, PullRequest, Repository
from insights.db.records import facts_from_row
from insights.domain import OwnershipRule


async def load_dataset(session: AsyncSession, params: SnapshotParams, *, now: datetime) -> Dataset:
    """Caller owns a REPEATABLE READ, READ ONLY transaction."""
    repositories = (
        await session.scalars(
            select(Repository)
            .where(Repository.full_name_lower.in_([r.lower() for r in params.repos]))
            .order_by(Repository.full_name)
        )
    ).all()
    display = {name.lower(): name for name in params.repos}
    names = {r.id: display[r.full_name_lower] for r in repositories}
    rules: dict[int, list[OwnershipRule]] = defaultdict(list)
    for rule in await session.scalars(select(StoredRule).where(StoredRule.repo_id.in_(names))):
        rules[rule.repo_id].append(
            OwnershipRule(rule.source, rule.pattern, tuple(rule.owners), rule.line_no)
        )
    repo_data = []
    for repo in repositories:
        if repo.covered_since is None or repo.last_synced_at is None:
            raise ValueError("Repository is not ready for analytics")
        repo_data.append(
            RepoData(
                names[repo.id],
                repo.data_version,
                repo.covered_since,
                repo.last_synced_at,
                repo.last_sync_status,
                ownership_counts(rules[repo.id]),
            )
        )
    if len(repo_data) != len(params.repos):
        raise ValueError("Repository is missing")
    start = datetime.combine(params.period_from, datetime.min.time(), UTC)
    end = datetime.combine(params.period_to + timedelta(days=1), datetime.min.time(), UTC)
    previous_start = start - timedelta(days=(params.period_to - params.period_from).days + 1)
    as_of = min(end, *(r.last_synced_at for r in repo_data))
    rows = (
        await session.execute(
            select(
                PrFact,
                PullRequest.title,
                PullRequest.url,
                PullRequest.author_login,
                PullRequest.is_draft,
                PullRequest.created_at,
            )
            .join(PullRequest, PullRequest.id == PrFact.pr_id)
            .where(
                PrFact.repo_id.in_(names),
                or_(
                    select(PrEvent.id)
                    .where(
                        PrEvent.pr_id == PrFact.pr_id,
                        ~PrEvent.actor_is_bot,
                        PrEvent.actor_login.is_not(None),
                        PrEvent.occurred_at >= previous_start,
                        PrEvent.occurred_at < as_of,
                    )
                    .exists(),
                    and_(
                        PrFact.ready_at < end,
                        or_(PrFact.end_at.is_(None), PrFact.end_at >= previous_start),
                    ),
                    and_(
                        PrFact.end_at >= start,
                        PrFact.end_at < as_of,
                        or_(PrFact.is_bot_author, PrFact.is_backport, PrFact.ready_at.is_(None)),
                    ),
                ),
            )
            .order_by(PrFact.pr_id)
        )
    ).all()
    ids = [row[0].pr_id for row in rows]
    intervals: dict[int, list[Interval]] = defaultdict(list)
    if ids:
        for pr_id, state, began, ended in (
            await session.execute(
                select(PrInterval.pr_id, PrInterval.state, PrInterval.start_at, PrInterval.end_at)
                .where(PrInterval.pr_id.in_(ids))
                .order_by(PrInterval.pr_id, PrInterval.seq)
            )
        ).all():
            intervals[pr_id].append(Interval(state, began, ended))
    activity: dict[int, list[datetime]] = defaultdict(list)
    if ids:
        for pr_id, at in (
            await session.execute(
                select(PrEvent.pr_id, PrEvent.occurred_at)
                .where(
                    PrEvent.pr_id.in_(ids),
                    ~PrEvent.actor_is_bot,
                    PrEvent.actor_login.is_not(None),
                    PrEvent.occurred_at >= previous_start,
                    PrEvent.occurred_at < as_of,
                )
                .order_by(PrEvent.pr_id, PrEvent.occurred_at)
            )
        ).all():
            activity[pr_id].append(at)
    ci = await load_ci_data(session, list(names), pr_ids=ids)
    prs = tuple(
        PrData(
            f.pr_id,
            names[f.repo_id],
            facts_from_row(f),
            tuple(intervals[f.pr_id]),
            f.number,
            title,
            url,
            author,
            draft,
            created,
            union_intervals(ci.by_pr.get(f.pr_id, ())),
            tuple(activity[f.pr_id]),
        )
        for f, title, url, author, draft, created in rows
    )
    flow_filter = (
        PrFact.repo_id.in_(names),
        ~PrFact.is_bot_author,
        ~PrFact.is_backport,
        PrFact.ready_at.is_not(None),
    )
    baseline_rows = (
        await session.execute(
            select(PrInterval.repo_id, PrInterval.state, PrInterval.end_at, PrInterval.start_at)
            .join(PrFact, PrFact.pr_id == PrInterval.pr_id)
            .where(
                *flow_filter,
                PrInterval.state.in_(WAITING_STATES),
                PrInterval.end_at >= start - timedelta(days=180),
                PrInterval.end_at < as_of,
            )
        )
    ).all()
    baselines = tuple(
        Baseline(names[repo_id], state, ended, (ended - began).total_seconds() / 3600)
        for repo_id, state, ended, began in baseline_rows
        if ended is not None
    )
    reviews = tuple(
        Review(login.lower(), at, pr_id)
        for login, at, pr_id in (
            await session.execute(
                select(PrEvent.actor_login, PrEvent.occurred_at, PrEvent.pr_id)
                .join(PrFact, PrFact.pr_id == PrEvent.pr_id)
                .join(PullRequest, PullRequest.id == PrFact.pr_id)
                .where(
                    *flow_filter,
                    PrEvent.kind == "review",
                    ~PrEvent.actor_is_bot,
                    PrEvent.actor_login.is_not(None),
                    PrEvent.occurred_at >= previous_start,
                    PrEvent.occurred_at < as_of,
                    or_(
                        PullRequest.author_login.is_(None),
                        func.lower(PrEvent.actor_login) != func.lower(PullRequest.author_login),
                    ),
                )
            )
        ).all()
        if login is not None
    )
    history = tuple(
        (names[repo_id], at, cycle)
        for repo_id, at, cycle in (
            await session.execute(
                select(PrFact.repo_id, PrFact.merged_at, PrFact.cycle_hours).where(
                    *flow_filter,
                    PrFact.merged_at >= previous_start - timedelta(days=90),
                    PrFact.merged_at < start,
                    PrFact.cycle_hours.is_not(None),
                )
            )
        ).all()
        if at is not None and cycle is not None
    )
    period_ci = await load_ci_data(
        session, list(names), created_from=previous_start, created_to=as_of, flow_only=True
    )
    return Dataset(
        tuple(repo_data),
        prs,
        reviews,
        baselines,
        params.period_from,
        params.period_to,
        ci_runs=tuple((names[repo_id], run) for repo_id, run in period_ci.runs),
        history=history,
        current_day=params.period_to == now.date(),
    )
