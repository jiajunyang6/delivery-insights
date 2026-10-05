"""Planted process changes and expected top explanations for synthetic evaluation."""

from dataclasses import replace

from insights_eval.generator import ScenarioSpec

BASELINE = ScenarioSpec.baseline()
# Alter source-generating assumptions, not snapshot metrics or hypothesis scores. The real
# analytics/scoring pipeline must detect the planted changes despite sampling variation.
SCENARIOS = {
    "review_capacity": replace(
        BASELINE,
        name="review_capacity",
        pickup_mult={"area-B": 4.0},
        arrival_mult={"area-B": 1.4},
        area_reviewers={"area-B": 1},
    ),
    "pr_size_growth": replace(BASELINE, name="pr_size_growth", size_mult=3.0),
    "no_signal": BASELINE,
}
# An expected location constrains the top-hit check. None for the hypothesis ID means the
# correct outcome is abstention; a planted change can still be too weak to qualify in one seed.
EXPECTED = {
    "review_capacity": ("H_review_capacity", "area-B"),
    "pr_size_growth": ("H_pr_size_growth", None),
    "no_signal": (None, None),
}
