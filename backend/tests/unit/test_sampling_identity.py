from insights.analytics.dataset import SnapshotParams
from insights.analytics.efficiency import Measure, compare
from insights.analytics.snapshot import digest, sampling_hash
from insights.analytics.stats import bootstrap_diff, seed_for
from insights_eval.generator import generate
from insights_eval.scenarios import SCENARIOS


def test_sampling_seeds_keep_the_complete_legacy_parameter_structure():
    for spec in SCENARIOS.values():
        for seed in (101, 202):
            repo = generate(spec, seed)
            params = SnapshotParams(
                (repo.repo,),
                repo.period_from,
                repo.period_to,
                ci_source="actions" if repo.ci_runs else "none",
            )
            old = params.canonical_dict()
            old["analytics_version"] = "1.4.0"
            legacy_hash = digest(old)[:16]
            assert sampling_hash(params) == legacy_hash
            assert digest(params.canonical_dict())[:16] != legacy_hash
            for metric in (
                "cycle_time_p50_hours",
                "cycle_time_p90_hours",
                "pickup",
                "review",
                "merge",
                "coding",
                "waiting_share",
                "revert_rate",
                "ci.queue_p50_minutes",
                "ci.run_p50_minutes",
                "ci.flaky_rerun_rate",
            ):
                assert seed_for(legacy_hash, metric) == seed_for(sampling_hash(params), metric)


def test_bootstrap_and_significance_near_zero_and_ten_percent_boundaries():
    repo = generate(SCENARIOS["no_signal"], 101)
    params = SnapshotParams((repo.repo,), repo.period_from, repo.period_to)
    old = params.canonical_dict()
    old["analytics_version"] = "1.4.0"
    legacy_hash, current_hash = digest(old)[:16], sampling_hash(params)
    previous = tuple(10 + (i - 10) / 3.5 for i in range(1, 20)) * 2
    for shift in (0.9999, 1.0, 1.0001):
        current = tuple(v + shift for v in previous)
        intervals = [
            bootstrap_diff(current, previous, "median", seed_for(h, "cycle_time_p50_hours"))
            for h in (legacy_hash, current_hash)
        ]
        assert intervals[0] == intervals[1]
        assert abs(intervals[0][0]) < 0.001
        results = [
            compare(
                Measure(10 + shift, len(current), current),
                Measure(10, len(previous), previous),
                unit="hours",
                name="cycle_time_p50_hours",
                params_hash=h,
                statistic="median",
            )
            for h in (legacy_hash, current_hash)
        ]
        assert results[0] == results[1]
