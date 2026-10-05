"""Slowest-decile PR size comparison among current merged PRs; descriptive, no I/O."""

from math import ceil

from insights.analytics.dataset import Dataset, merged
from insights.analytics.efficiency import values
from insights.analytics.stats import percentile


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
