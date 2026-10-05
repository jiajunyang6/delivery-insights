"""Percentiles and the seeded bootstrap."""

import pytest

from insights.analytics.stats import bootstrap_diff, percentile, seed_for


def test_quantiles_minimum_and_linear_interpolation():
    assert percentile([], 50, 1) is None
    assert percentile([1, 3], 50, 3) is None
    assert percentile([1, 3], 50, 2) == 2
    assert percentile([0, 10], 90, 2) == 9
    assert percentile([1], 50, 0) == 1


@pytest.mark.parametrize("statistic", ["median", "mean"])
def test_deterministic_bootstrap_matches_vectorized_reference(statistic):
    a, b, seed = [10.0, 20.0, 30.0], [1.0, 2.0, 3.0], 42
    assert bootstrap_diff(a, b, statistic, seed) == bootstrap_diff(a, b, statistic, seed)
    low, high = bootstrap_diff(a, b, statistic, seed)
    assert 0 < low <= high
    assert bootstrap_diff([1.0] * 30, [1.0] * 30, statistic, 7) == (0, 0)


def test_seed_identity_and_empty_samples():
    assert seed_for("a", "x") != seed_for("a", "y")
    assert seed_for("a", "x") == seed_for("a", "x")
    with pytest.raises(ValueError):
        bootstrap_diff([], [1.0], "mean", 42)
