from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from insights.analytics.dataset import Dataset, Window
from insights.analytics.efficiency import Measure, compare, quantile, rate
from insights.domain import CiRun, EventKind, PullRequestRecord


def map_runs(
    records: Mapping[int, PullRequestRecord], runs: Sequence[CiRun]
) -> dict[int, tuple[CiRun, ...]]:
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
    runs: dict[int, CiRun] = {}
    numbers: dict[int, set[int]] = defaultdict(set)
    for identifier, num in flow_numbers.items():
        for run in mapped.get(identifier, ()):
            runs[run.run_id] = run
            numbers[run.run_id].add(num)
    return tuple(replace(runs[i], pr_numbers=tuple(sorted(numbers[i]))) for i in sorted(runs))


def union_intervals(runs: Sequence[CiRun]) -> tuple[tuple[datetime, datetime], ...]:
    ranges = sorted((r.created_at, r.updated_at) for r in runs if r.updated_at > r.created_at)
    result: list[tuple[datetime, datetime]] = []
    for start, end in ranges:
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return tuple(result)


def measures(dataset: Dataset, window: Window) -> tuple[dict[str, Measure], list[CiRun]]:
    eligible = {(p.repo, p.number) for p in dataset.flow_in(window)}
    runs = [
        (repo, replace(r, pr_numbers=numbers))
        for repo, r in dataset.ci_runs
        if window.contains(r.created_at)
        and (numbers := tuple(n for n in r.pr_numbers if (repo, n) in eligible))
    ]
    queue = [
        (r.run_started_at - r.created_at).total_seconds() / 60
        for _, r in runs
        if r.run_started_at is not None and r.run_started_at >= r.created_at
    ]
    duration = [
        (r.updated_at - r.run_started_at).total_seconds() / 60
        for _, r in runs
        if r.status == "completed"
        and r.run_started_at is not None
        and r.updated_at >= r.run_started_at
    ]
    return {
        "queue_p50_minutes": quantile(queue),
        "run_p50_minutes": quantile(duration),
        "flaky_rerun_rate": rate(
            [float(r.run_attempt > 1 and r.conclusion == "success") for _, r in runs]
        ),
    }, [r for _, r in runs]


def build_ci(dataset: Dataset, params_hash: str) -> dict[str, Any]:
    current, _ = measures(dataset, dataset.current)
    previous, _ = measures(dataset, dataset.previous)
    result: dict[str, Any] = {}
    for name, measurement in current.items():
        unit = (
            "minutes" if name.endswith("minutes") else "share" if name.endswith("rate") else "count"
        )
        result[name] = compare(
            measurement,
            previous[name] if dataset.comparison_available else None,
            unit=unit,
            name=f"ci.{name}",
            params_hash=params_hash,
            statistic="mean" if unit == "share" else "median",
        )
    return result
