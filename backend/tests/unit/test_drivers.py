"""Slowest-decile PR size ratio and its sample gates."""

from tests.analytics_factory import dataset, pr

from insights.analytics.efficiency import slowest_decile_size_ratio


def test_slowest_decile_size_ratio_uses_ceil_and_sample_gates():
    prs = [pr(i, cycle_hours=i + 1, size_lines=1000 if i >= 90 else 100) for i in range(101)]
    # ceil(101 * 0.1) = 11 slowest PRs, which are exactly the eleven 1000-line PRs.
    assert slowest_decile_size_ratio(dataset(prs)) == 10
    assert slowest_decile_size_ratio(dataset(prs[:49])) is None
    # Fifty PRs give a five-PR slowest decile, below the ten-PR median gate.
    assert slowest_decile_size_ratio(dataset(prs[:50])) is None
    zero_rest = [pr(i, cycle_hours=i + 1, size_lines=1000 if i >= 90 else 0) for i in range(101)]
    assert slowest_decile_size_ratio(dataset(zero_rest)) is None
