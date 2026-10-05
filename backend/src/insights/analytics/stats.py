"""Percentiles and seeded bootstrap intervals; deterministic for a given seed."""

import hashlib
from collections.abc import Sequence
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from insights.analytics.thresholds import BOOTSTRAP_CI, BOOTSTRAP_ITERATIONS

# Change this only when intentionally changing statistical sampling results. It stands in for
# ANALYTICS_VERSION in sampling_hash, so analytics releases keep the same bootstrap seeds.
SAMPLING_SEED_VERSION = "1.4.0"

Statistic = Literal["median", "mean"]


def percentile(values: Sequence[float], q: float, min_samples: int) -> float | None:
    """Compute a linear percentile (q from 0 to 100), or None below the minimum sample count."""
    return (
        float(np.percentile(values, q, method="linear"))
        if len(values) >= max(1, min_samples)
        else None
    )


def seed_for(params_hash: str, metric_name: str) -> int:
    """Stable 64-bit RNG seed per (sampling hash, metric) so each comparison is reproducible."""
    return int.from_bytes(
        hashlib.sha256(f"{params_hash}:{metric_name}".encode()).digest()[:8], "big"
    )


def bootstrap_diff(
    current: Sequence[float],
    previous: Sequence[float],
    statistic: Statistic,
    seed: int,
) -> tuple[float, float]:
    """Bootstrap BOOTSTRAP_CI interval for statistic(current) - statistic(previous).

    Deterministic for a given seed; raises ValueError on an empty sample.
    """
    if not current or not previous:
        raise ValueError("Bootstrap requires two nonempty samples")
    rng = np.random.default_rng(seed)

    def sample(values: Sequence[float]) -> NDArray[np.float64]:
        """Resample observations with the shared seeded RNG and compute each draw's statistic."""
        array = np.asarray(values, dtype=np.float64)
        draws = array[rng.integers(0, len(values), size=(BOOTSTRAP_ITERATIONS, len(values)))]
        if statistic == "mean":
            return np.asarray(np.mean(draws, axis=1), dtype=np.float64)
        return np.asarray(
            np.percentile(draws, 50, axis=1, method="linear"),
            dtype=np.float64,
        )

    differences = sample(current) - sample(previous)
    alpha = (1 - BOOTSTRAP_CI) / 2
    low, high = np.percentile(differences, [alpha * 100, (1 - alpha) * 100], method="linear")
    return float(low), float(high)


def ratio(numerator: float, denominator: float) -> float:
    """Divide numerator by denominator, returning 0.0 when the denominator is zero."""
    return numerator / denominator if denominator else 0.0
