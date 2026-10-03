import hashlib
from collections.abc import Sequence
from itertools import groupby
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from insights.analytics.thresholds import BOOTSTRAP_CI, BOOTSTRAP_ITERATIONS

Statistic = Literal["median", "p90", "mean", "ratio"]


def percentile(values: Sequence[float], q: float, min_samples: int) -> float | None:
    return (
        float(np.percentile(values, q, method="linear"))
        if len(values) >= max(1, min_samples)
        else None
    )


def seed_for(params_hash: str, metric_name: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"{params_hash}:{metric_name}".encode()).digest()[:8], "big"
    )


def bootstrap_diff(
    current: Sequence[float] | Sequence[tuple[float, float]],
    previous: Sequence[float] | Sequence[tuple[float, float]],
    statistic: Statistic,
    seed: int,
) -> tuple[float, float]:
    if not current or not previous:
        raise ValueError("Bootstrap requires two nonempty samples")
    rng = np.random.default_rng(seed)

    def sample(values: Sequence[float] | Sequence[tuple[float, float]]) -> NDArray[np.float64]:
        array = np.asarray(values, dtype=np.float64)
        draws = array[rng.integers(0, len(values), size=(BOOTSTRAP_ITERATIONS, len(values)))]
        if statistic == "ratio":
            numerator, denominator = draws[:, :, 0].sum(axis=1), draws[:, :, 1].sum(axis=1)
            return np.divide(
                numerator, denominator, out=np.zeros_like(numerator), where=denominator != 0
            )
        if statistic == "mean":
            return np.asarray(np.mean(draws, axis=1), dtype=np.float64)
        return np.asarray(
            np.percentile(draws, 90 if statistic == "p90" else 50, axis=1, method="linear"),
            dtype=np.float64,
        )

    differences = sample(current) - sample(previous)
    alpha = (1 - BOOTSTRAP_CI) / 2
    low, high = np.percentile(differences, [alpha * 100, (1 - alpha) * 100], method="linear")
    return float(low), float(high)


def ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def kaplan_meier(samples: Sequence[tuple[float, bool]]) -> dict[str, Any]:
    """Estimate time-to-event survival; tied events precede censoring."""
    survival = 1.0
    at_risk = len(samples)
    median: float | None = None
    steps: list[tuple[float, float]] = []
    for duration, group in groupby(sorted(samples), key=lambda item: item[0]):
        outcomes = list(group)
        events = sum(event for _, event in outcomes)
        survival *= 1 - events / at_risk
        steps.append((duration, survival))
        if median is None and survival <= 0.5:
            median = duration
        at_risk -= len(outcomes)
    return {
        "n": len(samples),
        "events": sum(event for _, event in samples),
        "median_hours": median,
        "s_at_hours": {
            str(hour): next((s for t, s in reversed(steps) if t <= hour), 1.0)
            for hour in (24, 72, 168, 336)
        },
    }


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) != len(y):
        raise ValueError("Correlation requires paired samples")
    if len(x) < 10 or len(set(x)) < 2 or len(set(y)) < 2:
        return None

    def ranks(values: Sequence[float]) -> NDArray[np.float64]:
        array = np.asarray(values, dtype=np.float64)
        order = np.argsort(array, kind="stable")
        result = np.empty(len(array), dtype=np.float64)
        start = 0
        while start < len(order):
            end = start + 1
            while end < len(order) and array[order[end]] == array[order[start]]:
                end += 1
            result[order[start:end]] = (start + end - 1) / 2
            start = end
        return result

    return float(np.corrcoef(ranks(x), ranks(y))[0, 1])
