from dataclasses import replace
from datetime import datetime
from typing import Any

from insights.analytics.ci import map_runs, mapped_flow_runs, union_intervals
from insights.analytics.classify import LinkInput, is_flow, link_prs
from insights.analytics.dataset import Baseline, Dataset, PrData, RepoData, Review, SnapshotParams
from insights.analytics.facts import compute_facts
from insights.analytics.snapshot import build_snapshot
from insights.analytics.timeline import WAITING_STATES, build_timeline, human_event, pr_input
from insights.domain import EventKind
from insights_eval.generator import START, SyntheticRepo


def dataset_from_repo(
    syn: SyntheticRepo,
    *,
    location_dimension: str = "label:area-",
    covered_since: datetime | None = None,
) -> Dataset:
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
                ci_intervals,
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
