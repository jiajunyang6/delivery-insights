"""Adapt synthetic records to the production analytics pipeline, without database or HTTP I/O."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from insights.analytics.ci import union_intervals
from insights.analytics.classify import LinkInput, is_flow, link_prs
from insights.analytics.dataset import Baseline, Dataset, PrData, RepoData, Review, SnapshotParams
from insights.analytics.facts import compute_facts
from insights.analytics.snapshot import build_snapshot
from insights.analytics.timeline import WAITING_STATES, build_timeline, human_event, pr_input
from insights.domain import CiRun, EventKind, PullRequestRecord
from insights_eval.generator import START, SyntheticRepo


def dataset_from_repo(
    syn: SyntheticRepo,
    *,
    location_dimension: str = "label:area-",
    covered_since: datetime | None = None,
) -> Dataset:
    """Derive timelines, facts and cross-PR links, then assemble an in-memory analytics dataset.

    PR numbers serve as local identifiers in this synthetic repo. covered_since overrides the
    reported history coverage (START by default), allowing comparison-availability checks.
    Waiting baselines use elapsed hours; linked facts replace provisional per-PR facts.
    """
    prs: list[PrData] = []
    reviews: list[Review] = []
    baselines: list[Baseline] = []
    links: list[LinkInput] = []
    history: list[tuple[str, datetime, float]] = []
    mapped = map_runs({p.number: p for p in syn.records}, syn.ci_runs)
    for record in syn.records:
        ci_intervals = union_intervals(mapped.get(record.number, ()))
        result = build_timeline(pr_input(record), record.events, ci_intervals, syn.as_of)
        facts = compute_facts(
            record,
            record.events,
            result,
            default_branch=syn.default_branch,
            location_rules=(),
            now=syn.as_of,
            location_dimension=location_dimension,
            ci_covered=bool(ci_intervals),
        )
        identifier = record.number
        links.append(LinkInput(identifier, record, facts))
        prs.append(
            PrData(
                identifier,
                syn.repo,
                facts,
                result.intervals,
                record.number,
                record.title,
                record.url,
                record.author.login,
                record.is_draft,
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
            baselines.extend(
                Baseline(
                    syn.repo, i.state, i.end_at, (i.end_at - i.start_at).total_seconds() / 3600
                )
                for i in result.intervals
                if i.state in WAITING_STATES and i.end_at is not None
            )
            if facts.merged_at and facts.cycle_hours is not None:
                history.append((syn.repo, facts.merged_at, facts.cycle_hours))
    # Revert, reland and supersession classification requires the complete repository view,
    # not just the PR currently being derived in the loop above.
    linked = link_prs(links, repo_full_name=syn.repo, default_branch=syn.default_branch)
    return Dataset(
        (RepoData(syn.repo, 1, covered_since or START, syn.as_of),),
        tuple(replace(p, facts=linked[p.pr_id]) for p in prs),
        tuple(reviews),
        tuple(baselines),
        syn.period_from,
        syn.period_to,
        tuple(
            (syn.repo, run)
            for run in mapped_flow_runs(
                mapped, {p.pr_id: p.number for p in prs if is_flow(p.facts)}
            )
        ),
        tuple(history),
    )


def build_snapshot_from_repo(
    syn: SyntheticRepo,
    *,
    location_dimension: str = "label:area-",
    covered_since: datetime | None = None,
) -> dict[str, Any]:
    """Compute the production snapshot for the synthetic repo's inclusive reporting dates.

    CI source is actions when any runs exist, otherwise none; no metrics are planted directly
    into the snapshot. Production cohort filters, rounding and seeded comparisons still apply.
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
            ci_source="actions" if syn.ci_runs else "none",
        ),
    )


def map_runs(
    records: Mapping[int, PullRequestRecord], runs: Sequence[CiRun]
) -> dict[int, tuple[CiRun, ...]]:
    """Map CI runs to record IDs using the union of commit-SHA and explicit PR-number links.

    A run may map to multiple PRs; each per-PR sequence is ordered by creation time and run ID.
    Unknown PR numbers do not create mappings, and unmatched runs are omitted.
    """
    numbers = {p.number: identifier for identifier, p in records.items()}
    sha: dict[str, set[int]] = defaultdict(set)
    for identifier, record in records.items():
        for event in record.events:
            if event.kind == EventKind.COMMIT:
                sha[event.payload["oid"]].add(identifier)
    mapped: dict[int, list[CiRun]] = defaultdict(list)
    for run in sorted(runs, key=lambda r: (r.created_at, r.run_id)):
        ids = set(sha.get(run.head_sha, ()))
        ids.update(numbers[n] for n in run.pr_numbers if n in numbers)
        for identifier in sorted(ids):
            mapped[identifier].append(run)
    return {identifier: tuple(values) for identifier, values in mapped.items()}


def mapped_flow_runs(
    mapped: Mapping[int, Sequence[CiRun]], flow_numbers: Mapping[int, int]
) -> tuple[CiRun, ...]:
    """Deduplicate runs by ID and retain only their associations to eligible flow PRs.

    Rewritten pr_numbers and run order are sorted so shared runs have stable snapshot inputs.
    """
    runs: dict[int, CiRun] = {}
    numbers: dict[int, set[int]] = defaultdict(set)
    for identifier, num in flow_numbers.items():
        for run in mapped.get(identifier, ()):
            runs[run.run_id] = run
            numbers[run.run_id].add(num)
    return tuple(replace(runs[i], pr_numbers=tuple(sorted(numbers[i]))) for i in sorted(runs))
