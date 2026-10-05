"""Bottleneck analyses: time ledger, review queue, locations and change attribution; no I/O."""

from collections import defaultdict
from typing import Any

from insights.analytics import (
    LARGE_PR_LINES,
    MAX_LOCATIONS_IN_SNAPSHOT,
    MIN_LOCATION_PRS,
    MIN_SAMPLES_LOCATION_P50,
    MIN_SAMPLES_P50,
    MIN_SAMPLES_WEEKLY_P50,
)
from insights.analytics.dataset import (
    Dataset,
    PrData,
    Window,
    effective_review,
    hours,
    merged,
    weeks,
)
from insights.analytics.efficiency import values
from insights.analytics.stats import percentile, ratio
from insights.analytics.timeline import WAITING_STATES


def location_names(pr: PrData, dataset: Dataset) -> tuple[str, ...]:
    """Return location names, prefixed by repository when comparing multiple repositories."""
    return tuple(
        f"{pr.repo}:{name}" if len(dataset.repos) > 1 else name for name in pr.facts.locations
    )


def time_ledger(dataset: Dataset) -> dict[str, Any]:
    """Post-ready waiting-state hours of flow PRs merged in the current and previous periods.

    Shares are of total waiting hours. Previous-period fields are None without comparison data.
    """
    current, previous = merged(dataset, dataset.current), merged(dataset, dataset.previous)
    totals = {
        state: sum(hours(p, end=dataset.as_of)[state] for p in current) for state in WAITING_STATES
    }
    old = {
        state: sum(hours(p, end=dataset.start)[state] for p in previous) for state in WAITING_STATES
    }
    total, previous_total = sum(totals.values()), sum(old.values())
    comparison = dataset.comparison_available
    states = {}
    for state in WAITING_STATES:
        share, previous_share = ratio(totals[state], total), ratio(old[state], previous_total)
        states[state] = {
            "pr_hours": totals[state],
            "previous_pr_hours": old[state] if comparison else None,
            "share": share,
            "previous_share": previous_share if comparison else None,
            "change_pp": (share - previous_share) * 100 if comparison else None,
        }
    return {
        "merged_prs": len(current),
        "total_pr_hours": total,
        "states": states,
    }


def review_queue(dataset: Dataset, window: Window) -> dict[str, Any]:
    """Count weeks in which ready arrivals outnumber first-review departures.

    Weekly counts use this window's active cohort.
    """
    flow = dataset.flow_in(window)
    imbalanced = [
        sum(week.contains(p.facts.ready_at) for p in flow)
        > sum(week.contains(effective_review(p)) for p in flow)
        for week in weeks(window)
    ]
    return {"weeks_total": len(imbalanced), "weeks_inflow_exceeds_outflow": sum(imbalanced)}


def locations(dataset: Dataset) -> list[dict[str, Any]]:
    """Allocate merged-PR reviewer wait to locations and compare their first-review wait.

    Multi-location hours are split evenly, but a PR can belong to several location counts.
    Small or excess locations are pooled into other, preserving their allocated waiting hours.
    """
    current = {p.pr_id: p for p in merged(dataset, dataset.current)}
    previous = {p.pr_id: p for p in merged(dataset, dataset.previous)}
    all_prs = {p.pr_id: p for p in dataset.flow}
    comparison_prs = {**previous, **all_prs}
    memberships: dict[str, set[int]] = defaultdict(set)
    allocated: dict[str, float] = defaultdict(float)
    old_allocated: dict[str, float] = defaultdict(float)
    for pr in comparison_prs.values():
        names = location_names(pr, dataset)
        for name in names:
            memberships[name].add(pr.pr_id)
            # Split evenly so a multi-location PR's wait is not counted more than once.
            if pr.pr_id in current:
                allocated[name] += hours(pr, end=dataset.as_of)["waiting_reviewer"] / len(names)
            if pr.pr_id in previous:
                old_allocated[name] += hours(pr, end=dataset.start)["waiting_reviewer"] / len(names)
    large = sorted(
        (
            name
            for name, ids in memberships.items()
            if len(ids & current.keys()) >= MIN_LOCATION_PRS
        ),
        key=lambda name: (-allocated[name], name),
    )[:MAX_LOCATIONS_IN_SNAPSHOT]
    other = set(memberships) - set(large)
    groups = [(name, {name}) for name in large]
    if other:
        groups.append(("other", other))
    result = []
    for name, grouped in groups:
        ids = set().union(*(memberships[n] for n in grouped))
        group = [current[i] for i in sorted(ids & current.keys())]
        rest = [pr for i, pr in current.items() if i not in ids]
        pickup = percentile(values(group, "pickup_hours"), 50, MIN_SAMPLES_LOCATION_P50)
        rest_pickup = percentile(values(rest, "pickup_hours"), 50, MIN_SAMPLES_LOCATION_P50)
        result.append(
            {
                "location": name,
                "merged_prs": len(group),
                "pickup_p50_hours": pickup,
                "pickup_ratio_vs_rest": pickup / rest_pickup
                if pickup is not None and rest_pickup
                else None,
                "waiting_reviewer_pr_hours": sum(allocated[n] for n in grouped),
                "previous_waiting_reviewer_pr_hours": sum(old_allocated[n] for n in grouped)
                if dataset.comparison_available
                else None,
            }
        )
    return result


def series(dataset: Dataset, window: Window) -> list[dict[str, Any]]:
    """Build weekly metrics for merged PRs that belong to the overall window's active cohort.

    Waiting shares aggregate elapsed PR-hours; weekly percentiles require adequate samples.
    """
    result = []
    for week in weeks(window):
        prs = merged(dataset, week, scope=window)
        totals = {
            state: sum(hours(p, end=week.end)[state] for p in prs) for state in WAITING_STATES
        }
        result.append(
            {
                "week_start": week.start.date().isoformat(),
                "merged": len(prs),
                "cycle_p50_hours": percentile(
                    values(prs, "cycle_hours"), 50, MIN_SAMPLES_WEEKLY_P50
                ),
                "pickup_p50_hours": percentile(
                    values(prs, "pickup_hours"), 50, MIN_SAMPLES_WEEKLY_P50
                ),
                "pr_size_p50_lines": percentile(
                    values(prs, "size_lines"), 50, MIN_SAMPLES_WEEKLY_P50
                ),
                "waiting_reviewer_share": ratio(totals["waiting_reviewer"], sum(totals.values()))
                if prs
                else None,
            }
        )
    return result


def attribution(
    dataset: Dataset, ledger: dict[str, Any], locations_: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Decompose mean hours per merged PR; this is accounting, not a causal explanation.

    Each component's share of increase divides by the gross total of positive changes, not by the
    net cycle-time change, which could be small or cancelled by other components.
    """
    current, previous = merged(dataset, dataset.current), merged(dataset, dataset.previous)
    if not dataset.comparison_available or min(len(current), len(previous)) < MIN_SAMPLES_P50:
        return None
    components = {}
    for name in ("coding", *WAITING_STATES):
        new = (
            sum(p.facts.coding_hours or 0 for p in current)
            if name == "coding"
            else ledger["states"][name]["pr_hours"]
        )
        old = (
            sum(p.facts.coding_hours or 0 for p in previous)
            if name == "coding"
            else ledger["states"][name]["previous_pr_hours"]
        )
        components[name] = {
            "current": new / len(current),
            "previous": old / len(previous),
            "change": new / len(current) - old / len(previous),
        }
    increase = sum(max(0, c["change"]) for c in components.values())
    for c in components.values():
        c["share_of_increase"] = ratio(max(0, c["change"]), increase)
    locs = [
        {
            "location": p["location"],
            "change": p["waiting_reviewer_pr_hours"] / len(current)
            - p["previous_waiting_reviewer_pr_hours"] / len(previous),
        }
        for p in locations_
    ]
    gross = sum(max(0, p["change"]) for p in locs)
    for p in locs:
        share = ratio(max(0, p["change"]), gross)
        p.update(
            share_of_increase=components["waiting_reviewer"]["share_of_increase"] * share,
        )
    current_cycles, previous_cycles = (
        values(current, "cycle_hours"),
        values(previous, "cycle_hours"),
    )
    cycle_current, cycle_previous = (
        ratio(sum(current_cycles), len(current_cycles)),
        ratio(sum(previous_cycles), len(previous_cycles)),
    )
    large_current = sum(
        p.facts.cycle_hours or 0 for p in current if p.facts.size_lines >= LARGE_PR_LINES
    ) / len(current)
    large_previous = sum(
        p.facts.cycle_hours or 0 for p in previous if p.facts.size_lines >= LARGE_PR_LINES
    ) / len(previous)
    cycle_delta = cycle_current - cycle_previous
    return {
        "basis": "mean_hours_per_merged_pr",
        "states": components,
        "locations": locs,
        "large_prs": {
            "current": large_current,
            "previous": large_previous,
            "change": large_current - large_previous,
            "share_of_increase": min(1, max(0, large_current - large_previous) / cycle_delta)
            if cycle_delta > 0
            else 0,
        },
    }
