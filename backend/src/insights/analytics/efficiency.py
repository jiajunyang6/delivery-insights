"""Efficiency metrics for the current vs previous period with bootstrap significance."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from typing import Any

from insights.analytics import (
    CHANGE_MIN_RELATIVE,
    LARGE_PR_LINES,
    MIN_RATE_DENOMINATOR,
    MIN_RATE_EVENTS,
    MIN_SAMPLES_P50,
    REVIEW_CONCENTRATION_TOP_K,
)
from insights.analytics.dataset import Dataset, PrData, Window, merged
from insights.analytics.stats import Statistic, bootstrap_diff, percentile, seed_for


@dataclass(frozen=True, slots=True)
class Measure:
    """A metric value with its sample size and raw samples for bootstrap tests."""

    value: float | int | None
    n: int
    samples: tuple[float, ...] = ()
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
        significant = bool((low > 0 or high < 0) and abs(relative) >= CHANGE_MIN_RELATIVE)
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


def quantile(values: Sequence[float], q: float = 50, minimum: int = MIN_SAMPLES_P50) -> Measure:
    """Wrap a percentile with its sample count and raw values; too few samples yield None."""
    return Measure(percentile(values, q, minimum), len(values), tuple(values))


def mean(values: Sequence[float], minimum: int = MIN_SAMPLES_P50) -> Measure:
    """Wrap the arithmetic mean and raw samples; return a missing value below minimum."""
    return Measure(
        sum(values) / len(values) if len(values) >= minimum else None, len(values), tuple(values)
    )


def rate(values: Sequence[float], *, extra: dict[str, Any] | None = None) -> Measure:
    """Compute a binary-event share only when denominator and event-count thresholds are met."""
    count, events = len(values), int(sum(values))
    return Measure(
        events / count if count >= MIN_RATE_DENOMINATOR and events >= MIN_RATE_EVENTS else None,
        count,
        tuple(values),
        {"events": events, "denominator": count, **(extra or {})},
    )


def values(prs: Sequence[PrData], field: str) -> list[float]:
    """Extract non-None numeric fact values, preserving zero-valued observations."""
    return [float(value) for pr in prs if (value := getattr(pr.facts, field)) is not None]


def measures(dataset: Dataset, window: Window) -> dict[str, Measure]:
    """Collect unrounded metric samples for one period-active cohort.

    PR metrics use merged PRs; review concentration counts review events.
    Keep these raw samples for bootstrap comparisons rather than resampling displayed values.
    """
    prs = merged(dataset, window)
    counts = Counter(r.reviewer for r in dataset.reviews_in(window))
    review_count = sum(counts.values())
    return {
        "merged_prs": Measure(len(prs), len(prs)),
        "cycle_time_p50_hours": quantile(values(prs, "cycle_hours")),
        "pickup": quantile(values(prs, "pickup_hours")),
        # Zero-round PRs remain in the sample; excluding them would inflate the average.
        "avg_review_rounds": mean(values(prs, "review_rounds")),
        "post_review_commit_share": rate(
            [float(p.facts.commits_after_first_review > 0) for p in prs]
        ),
        "review_concentration_top_k": Measure(
            sum(v for _, v in counts.most_common(REVIEW_CONCENTRATION_TOP_K)) / review_count
            if review_count >= MIN_RATE_DENOMINATOR
            else None,
            review_count,
            extra={"k": REVIEW_CONCENTRATION_TOP_K},
        ),
        "pr_size_p50_lines": quantile(values(prs, "size_lines")),
        "large_pr_share": rate([float(p.facts.size_lines >= LARGE_PR_LINES) for p in prs]),
    }


# (output key, unit, bootstrap statistic). The measure name also seeds the bootstrap, so
# renaming a measure would change its sampled draws; "pickup" keeps its original name.
OUTPUTS: dict[str, tuple[str, str, Statistic | None]] = {
    "merged_prs": ("merged_prs", "count", None),
    "cycle_time_p50_hours": ("cycle_time_p50_hours", "hours", "median"),
    "pickup": ("pickup_p50_hours", "hours", "median"),
    "avg_review_rounds": ("avg_review_rounds", "rounds", "mean"),
    "post_review_commit_share": ("post_review_commit_share", "share", "mean"),
    "review_concentration_top_k": ("review_concentration_top_k", "share", None),
    "pr_size_p50_lines": ("pr_size_p50_lines", "lines", "median"),
    # Reported without a significance test; the size hypothesis reads its point change.
    "large_pr_share": ("large_pr_share", "share", None),
}


def build_efficiency(dataset: Dataset, params_hash: str) -> dict[str, Any]:
    """Assemble current/previous metrics with units and the matching bootstrap statistic."""
    current = measures(dataset, dataset.current)
    previous = measures(dataset, dataset.previous) if dataset.comparison_available else {}
    return {
        key: compare(
            measure,
            previous.get(name),
            unit=unit,
            name=name,
            params_hash=params_hash,
            statistic=statistic,
        )
        for name, measure in current.items()
        for key, unit, statistic in [OUTPUTS[name]]
    }


def slowest_decile_size_ratio(dataset: Dataset) -> float | None:
    """Median size of the slowest 10% of merged PRs divided by the median size of the rest.

    Needs at least 50 merged PRs, and ten sized PRs on each side; None otherwise or when the
    rest's median is 0. The comparison is descriptive, not a causal effect of size.
    """
    prs = merged(dataset, dataset.current)
    if len(prs) < 50:
        return None
    ordered = sorted(prs, key=lambda p: (-(p.facts.cycle_hours or 0), p.repo, p.number))
    n = ceil(len(prs) * 0.1)
    slow = percentile(values(ordered[:n], "size_lines"), 50, 10)
    rest = percentile(values(ordered[n:], "size_lines"), 50, 10)
    return slow / rest if slow is not None and rest else None
