"""Efficiency metrics for the current vs previous period with bootstrap significance."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import numpy as np

from insights.analytics import thresholds as t
from insights.analytics.dataset import (
    Dataset,
    PrData,
    Window,
    closed,
    hours,
    merged,
    reverted,
    weeks,
)
from insights.analytics.stats import (
    Statistic,
    bootstrap_diff,
    kaplan_meier,
    percentile,
    ratio,
    seed_for,
)

Samples = tuple[float, ...] | tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class Measure:
    value: float | int | None
    n: int
    samples: Samples = ()
    extra: dict[str, Any] | None = None


def compare(
    current: Measure,
    previous: Measure | None,
    *,
    unit: str,
    name: str,
    params_hash: str,
    statistic: Statistic | None = None,
) -> dict[str, Any]:
    """Compare a measure with the previous period's.

    significant is None without a statistic, a previous value, or samples on both sides.
    change_rel is None when the previous value is missing or 0.
    """
    old = previous.value if previous else None
    delta = current.value - old if current.value is not None and old is not None else None
    relative = delta / old if delta is not None and old else None
    significant = None
    if statistic and relative is not None and previous and current.samples and previous.samples:
        low, high = bootstrap_diff(
            current.samples, previous.samples, statistic, seed_for(params_hash, name)
        )
        # The CI rules out sampling noise; the minimum relative change rules out trivial shifts.
        significant = bool((low > 0 or high < 0) and abs(relative) >= t.CHANGE_MIN_RELATIVE)
    return {
        "value": current.value,
        "unit": unit,
        "n": current.n,
        "previous": old,
        "n_previous": previous.n if previous else None,
        "change_abs": delta,
        "change_rel": relative,
        "significant": significant,
        "status": "ok" if current.value is not None else "insufficient_sample",
        "extra": current.extra or {},
    }


def quantile(values: Sequence[float], q: float = 50, minimum: int = t.MIN_SAMPLES_P50) -> Measure:
    return Measure(percentile(values, q, minimum), len(values), tuple(values))


def mean(values: Sequence[float], minimum: int = t.MIN_SAMPLES_P50) -> Measure:
    return Measure(
        sum(values) / len(values) if len(values) >= minimum else None, len(values), tuple(values)
    )


def rate(values: Sequence[float], *, extra: dict[str, Any] | None = None) -> Measure:
    count, events = len(values), int(sum(values))
    return Measure(
        events / count if count >= t.MIN_RATE_DENOMINATOR and events >= t.MIN_RATE_EVENTS else None,
        count,
        tuple(values),
        {"events": events, "denominator": count, **(extra or {})},
    )


def values(prs: Sequence[PrData], field: str) -> list[float]:
    return [float(value) for pr in prs if (value := getattr(pr.facts, field)) is not None]


def measures(dataset: Dataset, window: Window) -> dict[str, Measure]:
    prs, lost = merged(dataset, window), closed(dataset, window)
    counts = Counter(r.reviewer for r in dataset.reviews_in(window))
    review_count = sum(counts.values())
    pairs = []
    for pr in prs:
        ledger = hours(pr, end=window.end)
        numerator = sum(ledger[s] for s in ("waiting_reviewer", "waiting_ci", "waiting_merge"))
        denominator = sum(ledger.values()) + (pr.facts.coding_hours or 0)
        pairs.append((numerator, denominator))
    # Only PRs ready at least N days before the window ends had the full N days to merge.
    eligible = [
        p
        for p in dataset.flow_in(window)
        if p.facts.ready_at is not None
        and window.start <= p.facts.ready_at <= window.end - timedelta(days=t.N_DAYS_MERGED)
    ]
    within = [
        float(
            p.facts.merged_at is not None
            and p.facts.ready_at is not None
            and p.facts.merged_at - p.facts.ready_at <= timedelta(days=t.N_DAYS_MERGED)
        )
        for p in eligible
    ]
    result = {
        "merged_prs": Measure(len(prs), len(prs)),
        "effective_throughput": Measure(
            len(prs)
            - sum(reverted(p, window.end) for p in prs)
            - sum(p.facts.is_revert for p in prs),
            len(prs),
        ),
        "cycle_time_p50_hours": quantile(values(prs, "cycle_hours")),
        "cycle_time_p90_hours": quantile(values(prs, "cycle_hours"), 90, t.MIN_SAMPLES_P90),
        "merged_within_n_days": rate(within, extra={"n_days": t.N_DAYS_MERGED}),
        "waiting_share": Measure(
            ratio(sum(p[0] for p in pairs), sum(p[1] for p in pairs))
            if len(prs) >= t.MIN_SAMPLES_P50
            else None,
            len(prs),
            tuple(pairs),
        ),
        # Superseded closes are not waste: their work landed through another merged PR.
        "waste_share": rate(
            [float(p.facts.close_class != "superseded") for p in lost]
            + [float(reverted(p, window.end)) for p in prs]
        ),
        "avg_review_rounds": mean(values(prs, "review_rounds")),
        "post_review_commit_share": rate(
            [float(p.facts.commits_after_first_review > 0) for p in prs]
        ),
        "review_concentration_top_k": Measure(
            sum(v for _, v in counts.most_common(t.REVIEW_CONCENTRATION_TOP_K)) / review_count
            if review_count >= t.MIN_RATE_DENOMINATOR
            else None,
            review_count,
            extra={"k": t.REVIEW_CONCENTRATION_TOP_K},
        ),
        "revert_rate": rate([float(reverted(p, window.end)) for p in prs]),
        "pr_size_p50_lines": quantile(values(prs, "size_lines")),
    }
    for stage in ("coding", "pickup", "review", "merge"):
        result[stage] = quantile(values(prs, stage + "_hours"))
    return result


def build_efficiency(dataset: Dataset, params_hash: str) -> dict[str, Any]:
    current = measures(dataset, dataset.current)
    previous = measures(dataset, dataset.previous) if dataset.comparison_available else {}
    result: dict[str, Any] = {
        "stage_p50_hours": {},
        "predictability": predictability(dataset, params_hash),
        "survival": {
            "current": survival_cohort(dataset, dataset.current),
            "previous": survival_cohort(dataset, dataset.previous)
            if dataset.comparison_available
            else None,
        },
    }
    for name, measure in current.items():
        unit = "share"
        statistic: Statistic | None = "mean"
        if name in {"merged_prs", "effective_throughput"}:
            unit, statistic = "count", None
        elif name == "review_concentration_top_k":
            statistic = None
        elif name == "avg_review_rounds":
            unit = "rounds"
        elif name == "pr_size_p50_lines":
            unit, statistic = "lines", "median"
        elif name == "waiting_share":
            statistic = "ratio"
        elif "hours" in name or name in {"coding", "pickup", "review", "merge"}:
            unit, statistic = "hours", "p90" if "p90" in name else "median"
        metric = compare(
            measure,
            previous.get(name),
            unit=unit,
            name=name,
            params_hash=params_hash,
            statistic=statistic,
        )
        if name in {"coding", "pickup", "review", "merge"}:
            result["stage_p50_hours"][name] = metric
        else:
            result[name] = metric
    return result


def survival_cohort(dataset: Dataset, window: Window) -> dict[str, Any] | None:
    samples = []
    for pr in dataset.flow_in(window):
        f = pr.facts
        if f.ready_at is None or not window.contains(f.ready_at):
            continue
        event = f.merged_at is not None and f.merged_at < window.end
        end = f.merged_at if event else min(window.end, f.closed_at or window.end)
        if end is not None:
            samples.append((max(0, (end - f.ready_at).total_seconds() / 3600), event))
    return kaplan_meier(samples) if len(samples) >= t.MIN_SAMPLES_P50 else None


def predictability(dataset: Dataset, params_hash: str) -> dict[str, Any]:
    def historical(window: Window) -> Measure:
        prs = merged(dataset, window)
        start = window.start - timedelta(days=90)
        if any(repo.covered_since > start for repo in dataset.repos):
            return Measure(None, len(prs), extra={"reason": "baseline_not_covered"})
        history = [cycle for _, at, cycle in dataset.history if start <= at < window.start]
        baseline = percentile(history, 85, 30)
        if baseline is None:
            return Measure(None, len(prs), extra={"baseline_n": len(history)})
        return rate(
            [float(cycle <= baseline) for cycle in values(prs, "cycle_hours")],
            extra={"baseline_p85_hours": baseline, "baseline_n": len(history)},
        )

    def weekly_cv(window: Window) -> Measure:
        counts = [
            len(merged(dataset, week, scope=window))
            for week in weeks(window)
            if week.end - week.start == timedelta(days=7)
        ]
        value = (
            float(np.std(counts) / np.mean(counts))
            if len(counts) >= 4 and sum(counts) > 0
            else None
        )
        return Measure(value, len(counts))

    result = {}
    for name, unit, measure in (
        ("within_hist_p85", "share", historical),
        ("weekly_throughput_cv", "coefficient", weekly_cv),
    ):
        result[name] = compare(
            measure(dataset.current),
            measure(dataset.previous) if dataset.comparison_available else None,
            unit=unit,
            name=name,
            params_hash=params_hash,
        )
    return result
