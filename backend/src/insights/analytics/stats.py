import hashlib
from collections.abc import Sequence
from typing import Literal

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
