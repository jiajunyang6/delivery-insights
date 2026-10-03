from dataclasses import replace

from insights_eval.generator import AREAS, ScenarioSpec

BASELINE = ScenarioSpec.baseline()
SCENARIOS = {
    "review_capacity": replace(
        BASELINE,
        name="review_capacity",
        pickup_mult={"area-B": 4.0},
        arrival_mult={"area-B": 1.4},
        area_reviewers={"area-B": 1},
    ),
    "ci_slowdown": replace(
        BASELINE,
        name="ci_slowdown",
        ci_enabled=True,
        ci_queue_mult=6.0,
        ci_run_mult=2.0,
        flaky_rate=(0.03, 0.15),
        reviewers_wait_for_ci=True,
    ),
    "pr_size_growth": replace(BASELINE, name="pr_size_growth", size_mult=3.0),
    "quality_tradeoff": replace(
        BASELINE,
        name="quality_tradeoff",
        pickup_mult=dict.fromkeys(AREAS, 0.4),
        first_approval_bonus=0.35,
        rubber_stamp_large_share=0.5,
        revert_rate=(0.02, 0.09),
    ),
    "no_signal": BASELINE,
}
EXPECTED = {
    "review_capacity": ("H_review_capacity", "area-B"),
    "ci_slowdown": ("H_ci_bottleneck", None),
    "pr_size_growth": ("H_pr_size_growth", None),
    "quality_tradeoff": ("H_quality_tradeoff", None),
    "no_signal": (None, None),
}
