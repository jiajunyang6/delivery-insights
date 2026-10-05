"""Adapt synthetic records to the production analytics pipeline, without database or HTTP I/O."""

from datetime import datetime
from typing import Any

from insights.analytics.classify import is_flow
from insights.analytics.dataset import Dataset, PrData, RepoData, Review, SnapshotParams
from insights.analytics.facts import compute_facts
from insights.analytics.snapshot import build_snapshot
from insights.analytics.timeline import build_timeline, human_event, pr_input
from insights.domain import EventKind
from insights_eval.generator import START, SyntheticRepo


def dataset_from_repo(
    syn: SyntheticRepo,
    *,
    location_dimension: str = "label:area-",
    covered_since: datetime | None = None,
) -> Dataset:
    """Derive timelines and facts, then assemble an in-memory analytics dataset.

    PR numbers serve as local identifiers in this synthetic repo. covered_since overrides the
    reported history coverage (START by default), allowing comparison-availability checks.
    """
    prs: list[PrData] = []
    reviews: list[Review] = []
    for record in syn.records:
        result = build_timeline(pr_input(record), record.events, syn.as_of)
        facts = compute_facts(
            record,
            record.events,
            result,
            default_branch=syn.default_branch,
            location_dimension=location_dimension,
        )
        identifier = record.number
        prs.append(
            PrData(
                identifier,
                syn.repo,
                facts,
                result.intervals,
                record.number,
                record.created_at,
                tuple(e.occurred_at for e in record.events if e.actor.login and not e.actor.is_bot),
            )
        )
        if is_flow(facts):
            reviews.extend(
                Review(e.actor.login.lower(), e.occurred_at, identifier)
                for e in record.events
                if e.kind == EventKind.REVIEW
                and e.actor.login
                and human_event(e, record.author.login)
            )
    return Dataset(
        (RepoData(syn.repo, 1, covered_since or START, syn.as_of),),
        tuple(prs),
        tuple(reviews),
        syn.period_from,
        syn.period_to,
    )


def build_snapshot_from_repo(
    syn: SyntheticRepo,
    *,
    location_dimension: str = "label:area-",
    covered_since: datetime | None = None,
) -> dict[str, Any]:
    """Compute the production snapshot for the synthetic repo's inclusive reporting dates.

    No metrics are planted directly into the snapshot. Production cohort filters, rounding
    and seeded comparisons still apply.
    """
    dataset = dataset_from_repo(
        syn, location_dimension=location_dimension, covered_since=covered_since
    )
    return build_snapshot(
        dataset,
        params=SnapshotParams(
            (syn.repo,),
            syn.period_from,
            syn.period_to,
            location_dimension,
        ),
    )
