from collections.abc import Sequence
from itertools import pairwise
from math import ceil
from typing import Any

from insights.analytics.dataset import Dataset, PrData, hours, merged
from insights.analytics.efficiency import values
from insights.analytics.stats import percentile, spearman


def quotient(value: float | None, baseline: float | None) -> float | None:
    return value / baseline if value is not None and baseline else None


def pickup_group(prs: Sequence[PrData]) -> dict[str, Any]:
    return {"n": len(prs), "pickup_p50_hours": percentile(values(prs, "pickup_hours"), 50, 10)}


def build_drivers(dataset: Dataset, first_pickup: float | None) -> dict[str, Any]:
    prs = merged(dataset, dataset.current)
    assigned = pickup_group([p for p in prs if p.facts.review_requested_before_first_review])
    unassigned = pickup_group([p for p in prs if not p.facts.review_requested_before_first_review])
    rounds: list[dict[str, Any]] = []
    for index, label in enumerate(("0", "1", "2", "3+")):
        group = [p for p in prs if min(3, p.facts.review_rounds) == index]
        rounds.append(
            {
                "rounds": label,
                "n": len(group),
                "cycle_p50_hours": percentile(values(group, "cycle_hours"), 50, 10),
            }
        )
    costs = [
        b["cycle_p50_hours"] - a["cycle_p50_hours"]
        for a, b in pairwise(rounds)
        if a["cycle_p50_hours"] is not None and b["cycle_p50_hours"] is not None
    ]
    re_review = [
        (after.end_at - after.start_at).total_seconds() / 3600
        for p in prs
        for before, after in pairwise(p.intervals)
        if before.state == "waiting_author"
        and after.state == "waiting_reviewer"
        and after.end_at is not None
    ]
    wip_pairs = [
        (p.facts.author_open_prs_at_ready, hours(p, end=dataset.as_of)["waiting_author"])
        for p in prs
        if p.facts.author_open_prs_at_ready is not None
    ]
    wip = []
    for label, low, high in (("0", 0, 0), ("1-2", 1, 2), ("3+", 3, float("inf"))):
        waits = [wait for count, wait in wip_pairs if low <= count <= high]
        wip.append(
            {"wip": label, "n": len(waits), "waiting_author_p50_hours": percentile(waits, 50, 10)}
        )
    timing = {
        "by_weekday": [
            {"weekday": day, **pickup_group([p for p in prs if p.facts.ready_weekday == day])}
            for day in range(7)
        ],
        "by_hour_block": [
            {
                "hours": f"{start:02}-{start + 5:02}",
                **pickup_group(
                    [
                        p
                        for p in prs
                        if p.facts.ready_hour is not None
                        and start <= p.facts.ready_hour < start + 6
                    ]
                ),
            }
            for start in range(0, 24, 6)
        ],
    }
    slowest = None
    if len(prs) >= 50:
        ordered = sorted(prs, key=lambda p: (-(p.facts.cycle_hours or 0), p.repo, p.number))
        n = ceil(len(prs) * 0.1)
        slow, rest = ordered[:n], ordered[n:]

        def features(group: Sequence[PrData]) -> dict[str, float | None]:
            return {
                "size_lines_p50": percentile(values(group, "size_lines"), 50, 10),
                "external_share": sum(p.facts.external_contributor for p in group) / len(group),
                "multi_location_share": sum(len(p.facts.locations) >= 2 for p in group)
                / len(group),
                "review_rounds_p50": percentile(values(group, "review_rounds"), 50, 10),
                "unrequested_share": sum(
                    not p.facts.review_requested_before_first_review for p in group
                )
                / len(group),
            }

        slow_features, rest_features = features(slow), features(rest)
        slowest = {
            "n": n,
            "features": [
                {
                    "feature": name,
                    "slowest": value,
                    "rest": rest_features[name],
                    "ratio": quotient(value, rest_features[name]),
                }
                for name, value in slow_features.items()
            ],
        }
    return {
        "assignment": {
            "assigned": assigned,
            "unassigned": unassigned,
            "ratio": quotient(unassigned["pickup_p50_hours"], assigned["pickup_p50_hours"]),
        },
        "review_round_cost": {
            "buckets": rounds,
            "hours_per_extra_round": percentile(costs, 50, 1),
            "re_review_wait_p50_hours": percentile(re_review, 50, 10),
            "first_pickup_p50_hours": first_pickup,
        },
        "author_wip": {
            "buckets": wip,
            "spearman": spearman(
                [count for count, _ in wip_pairs], [wait for _, wait in wip_pairs]
            ),
        },
        "submit_timing": timing,
        "slowest_decile": slowest,
    }
