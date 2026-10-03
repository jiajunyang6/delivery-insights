from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from insights.analytics import thresholds as t
from insights.analytics.dataset import (
    Dataset,
    PrData,
    Window,
    closed,
    effective_review,
    hours,
    merged,
    open_at,
    reverted,
    weeks,
)
from insights.analytics.efficiency import Measure, compare, rate, values
from insights.analytics.stats import percentile, ratio
from insights.analytics.timeline import WAITING_STATES, state_at


def location_names(pr: PrData, dataset: Dataset) -> tuple[str, ...]:
    return tuple(
        f"{pr.repo}:{name}" if len(dataset.repos) > 1 else name for name in pr.facts.locations
    )


def at_risk(
    dataset: Dataset, *, at: datetime, exclude_current_drafts: bool = False
) -> list[dict[str, Any]]:
    completed: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for baseline in dataset.baselines:
        if at - timedelta(days=t.AT_RISK_FALLBACK_DAYS) <= baseline.end_at < at:
            completed[baseline.repo, baseline.state].append((baseline.end_at, baseline.hours))
    thresholds: dict[tuple[str, str], tuple[float, float, str]] = {}
    for repo in dataset.repos:
        for state in WAITING_STATES:
            completed_values = completed[repo.repo, state]
            recent = [
                h
                for end, h in completed_values
                if end >= at - timedelta(days=t.AT_RISK_BASELINE_DAYS)
            ]
            fallback = [h for _, h in completed_values]
            sample = recent if len(recent) >= t.AT_RISK_MIN_BASELINE else fallback
            source = "90d" if len(recent) >= t.AT_RISK_MIN_BASELINE else "180d"
            warning = percentile(sample, t.AT_RISK_WARNING_PERCENTILE, t.AT_RISK_MIN_BASELINE)
            critical = percentile(sample, t.AT_RISK_CRITICAL_PERCENTILE, t.AT_RISK_MIN_BASELINE)
            if warning is None or critical is None:
                warning, critical, source = (
                    t.AT_RISK_DEFAULT_HOURS[state],
                    t.AT_RISK_DEFAULT_HOURS[state] * 2,
                    "default",
                )
            thresholds[repo.repo, state] = warning, critical, source
    result = []
    for pr in dataset.flow:
        if not open_at(pr, at) or (exclude_current_drafts and pr.is_draft):
            continue
        interval = state_at(pr.intervals, at)
        if interval is None:
            continue
        age = (at - interval.start_at).total_seconds() / 3600
        warning, critical, source = thresholds[pr.repo, interval.state]
        if age <= warning:
            continue
        result.append(
            {
                "repo": pr.repo,
                "number": pr.number,
                "title": pr.title,
                "url": pr.url,
                "author": pr.author,
                "state": interval.state,
                "age_hours": age,
                "threshold_hours": warning,
                "critical_threshold_hours": critical,
                "severity": "critical" if age > critical else "warning",
                "baseline_source": source,
                "locations": list(location_names(pr, dataset)),
                "external_contributor": pr.facts.external_contributor,
                "size_lines": pr.facts.size_lines,
            }
        )
    return sorted(
        result,
        key=lambda p: (
            -ratio(p["age_hours"], p["threshold_hours"]) if p["threshold_hours"] else float("-inf"),
            p["repo"],
            p["number"],
        ),
    )


def risk_summary(risks: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(risks),
        "critical": sum(p["severity"] == "critical" for p in risks),
        "by_state": {state: sum(p["state"] == state for p in risks) for state in WAITING_STATES},
    }


def time_ledger(dataset: Dataset) -> dict[str, Any]:
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
    coverage = ratio(sum(p.facts.ci_covered for p in current), len(current))
    return {
        "scope": "merged_prs",
        "merged_prs": len(current),
        "previous_merged_prs": len(previous) if comparison else None,
        "total_pr_hours": total,
        "previous_total_pr_hours": previous_total if comparison else None,
        "states": states,
        "ci_coverage": coverage,
        "ci_data_available": coverage > 0,
    }


def review_queue(dataset: Dataset, window: Window) -> dict[str, Any]:
    result: list[dict[str, Any]] = []
    for week in weeks(window):
        result.append(
            {
                "week_start": week.start.date().isoformat(),
                "days": (week.end - week.start).total_seconds() / 86400,
                "inflow": sum(week.contains(p.facts.ready_at) for p in dataset.flow),
                "outflow": sum(week.contains(effective_review(p)) for p in dataset.flow),
                "open_at_week_end": sum(
                    open_at(p, week.end)
                    and ((review_at := effective_review(p)) is None or review_at >= week.end)
                    for p in dataset.flow
                ),
            }
        )
    growth = (
        result[-1]["open_at_week_end"] / result[0]["open_at_week_end"] - 1
        if result and result[0]["open_at_week_end"]
        else None
    )
    return {
        "weeks": result,
        "weeks_total": len(result),
        "weeks_inflow_exceeds_outflow": sum(w["inflow"] > w["outflow"] for w in result),
        "open_growth_rel": growth,
    }


def locations(
    dataset: Dataset, risks: Sequence[dict[str, Any]], ledger: dict[str, Any]
) -> list[dict[str, Any]]:
    current = {p.pr_id: p for p in merged(dataset, dataset.current)}
    previous = {p.pr_id: p for p in merged(dataset, dataset.previous)}
    all_prs = {p.pr_id: p for p in dataset.flow}
    memberships: dict[str, set[int]] = defaultdict(set)
    allocated: dict[str, float] = defaultdict(float)
    old_allocated: dict[str, float] = defaultdict(float)
    for pr in dataset.flow:
        names = location_names(pr, dataset)
        for name in names:
            memberships[name].add(pr.pr_id)
            if pr.pr_id in current:
                allocated[name] += hours(pr, end=dataset.as_of)["waiting_reviewer"] / len(names)
            if pr.pr_id in previous:
                old_allocated[name] += hours(pr, end=dataset.start)["waiting_reviewer"] / len(names)
    large = sorted(
        (
            name
            for name, ids in memberships.items()
            if len(ids & current.keys()) >= t.MIN_LOCATION_PRS
        ),
        key=lambda name: (-allocated[name], name),
    )[: t.MAX_LOCATIONS_IN_SNAPSHOT]
    other = set(memberships) - set(large)
    groups = [(name, {name}) for name in large]
    if other:
        groups.append(("other", other))
    risks_by_location: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for risk in risks:
        for name in risk["locations"]:
            risks_by_location[name].add((risk["repo"], risk["number"]))
    owner_counts = {
        f"{r.repo}:{name}" if len(dataset.repos) > 1 else name: count
        for r in dataset.repos
        for name, count in r.owners
    }
    result = []
    for name, grouped in groups:
        ids = set().union(*(memberships[n] for n in grouped))
        group = [current[i] for i in sorted(ids & current.keys())]
        rest = [pr for i, pr in current.items() if i not in ids]
        pickup = percentile(values(group, "pickup_hours"), 50, t.MIN_SAMPLES_LOCATION_P50)
        rest_pickup = percentile(values(rest, "pickup_hours"), 50, t.MIN_SAMPLES_LOCATION_P50)
        wait = sum(allocated[n] for n in grouped)
        group_risks = set().union(*(risks_by_location[n] for n in grouped))
        result.append(
            {
                "location": name,
                "merged_prs": len(group),
                "pickup_p50_hours": pickup,
                "pickup_ratio_vs_rest": pickup / rest_pickup
                if pickup is not None and rest_pickup
                else None,
                "waiting_reviewer_pr_hours": wait,
                "previous_waiting_reviewer_pr_hours": sum(old_allocated[n] for n in grouped)
                if dataset.comparison_available
                else None,
                "waiting_reviewer_share": ratio(
                    wait, ledger["states"]["waiting_reviewer"]["pr_hours"]
                ),
                "inflow": sum(dataset.current.contains(all_prs[i].facts.ready_at) for i in ids),
                "outflow": sum(dataset.current.contains(effective_review(all_prs[i])) for i in ids),
                "at_risk_prs": len(group_risks),
                "owners_count": owner_counts.get(name) if name != "other" else None,
            }
        )
    return result


def what_if(dataset: Dataset, stage: str, location: str | None = None) -> dict[str, Any] | None:
    prs = [p for p in merged(dataset, dataset.current) if p.facts.cycle_hours is not None]
    before, after, affected = [], [], 0
    target = t.WHAT_IF_TARGET_HOURS[stage]
    for pr in prs:
        cycle = pr.facts.cycle_hours
        if cycle is None:
            continue
        value = (
            hours(pr, end=dataset.as_of)["waiting_ci"]
            if stage == "ci"
            else getattr(pr.facts, stage + "_hours") or 0
        )
        if location is not None and location not in location_names(pr, dataset):
            value = 0
        before.append(cycle)
        after.append(cycle - max(0, value - target))
        affected += value > target
    old, new = percentile(before, 50, t.MIN_SAMPLES_P50), percentile(after, 50, t.MIN_SAMPLES_P50)
    if old is None or old == 0 or new is None:
        return None
    return {
        "stage": stage,
        "location": location,
        "target_hours": target,
        "affected_prs": affected,
        "cycle_p50_before_hours": old,
        "cycle_p50_after_hours": new,
        "change_rel": new / old - 1,
    }


def merge_blockers(dataset: Dataset) -> dict[str, Any]:
    prs = [p for p in merged(dataset, dataset.current) if p.facts.approved_at is not None]
    return {
        "approved_merged_prs": len(prs),
        "second_approval_share": sum(p.facts.distinct_approvers >= 2 for p in prs) / len(prs)
        if len(prs) >= 10
        else None,
        "second_approval_wait_p50_hours": percentile(
            values(prs, "second_approval_wait_hours"), 50, 10
        ),
        "post_approval_update_share": sum(p.facts.updates_after_approval > 0 for p in prs)
        / len(prs)
        if len(prs) >= 10
        else None,
        "ci_after_approval_p50_hours": None,
    }


def pareto(ledger: dict[str, Any], locations_: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = [p for p in locations_ if p["location"] != "other"][:5]
    remainder = sum(p["waiting_reviewer_pr_hours"] for p in locations_ if p not in kept)
    entries = [("waiting_reviewer", p["location"], p["waiting_reviewer_pr_hours"]) for p in kept]
    if len(kept) < len(locations_):
        entries.append(("waiting_reviewer", "other", remainder))
    entries.extend(
        (state, None, ledger["states"][state]["pr_hours"])
        for state in WAITING_STATES
        if state != "waiting_reviewer"
    )
    return sorted(
        [
            {
                "cause": state,
                "location": location,
                "pr_hours": value,
                "share": ratio(value, ledger["total_pr_hours"]),
            }
            for state, location, value in entries
        ],
        key=lambda e: (-e["pr_hours"], e["cause"], e["location"] or ""),
    )


def review_load(dataset: Dataset) -> dict[str, Any]:
    counts = Counter(r.reviewer for r in dataset.reviews if dataset.current.contains(r.occurred_at))
    total = sum(counts.values())
    return {
        "reviewers": len(counts),
        "reviews": total,
        "distribution": [
            {"reviewer": name, "reviews": count, "share": ratio(count, total)}
            for name, count in sorted(counts.items(), key=lambda p: (-p[1], p[0]))[:10]
        ],
    }


def waste_rework(dataset: Dataset) -> tuple[dict[str, Any], dict[str, Any]]:
    prs, lost = merged(dataset, dataset.current), closed(dataset, dataset.current)
    wasted = [p for p in lost if p.facts.close_class != "superseded"]
    reverted_prs = [p for p in prs if reverted(p, dataset.as_of)]
    wasted_ids = {p.pr_id for p in wasted}
    reviews = [r for r in dataset.reviews if dataset.current.contains(r.occurred_at)]
    waste = {
        "closed_unmerged": len(lost),
        "by_class": {
            name: sum(p.facts.close_class == name for p in lost)
            for name in ("superseded", "rejected", "abandoned", "no_review")
        },
        "lost_while_waiting": sum(
            p.facts.close_class in {"no_review", "abandoned"}
            and p.facts.state_at_close == "waiting_reviewer"
            for p in lost
        ),
        "late_rejections": sum(p.facts.late_rejection for p in lost),
        "wasted_review_share": sum(r.pr_id in wasted_ids for r in reviews) / len(reviews)
        if len(reviews) >= 30
        else None,
        "wasted_pr_hours": sum(
            sum(hours(p, end=dataset.as_of).values()) for p in [*wasted, *reverted_prs]
        ),
    }
    by_id = {p.pr_id: p for p in dataset.prs}
    relands: dict[int, PrData] = {}
    for pr in sorted(dataset.prs, key=lambda p: (p.facts.merged_at or dataset.as_of, p.pr_id)):
        if (
            pr.facts.reland_of_pr_id is not None
            and pr.facts.merged_at
            and pr.facts.merged_at < dataset.as_of
        ):
            relands.setdefault(pr.facts.reland_of_pr_id, pr)
    chains = []
    for original in sorted(
        reverted_prs, key=lambda p: (p.facts.reverted_at or dataset.as_of, p.pr_id), reverse=True
    ):
        revert = by_id.get(original.facts.reverted_by_pr_id or -1)
        if revert is None or revert.facts.merged_at is None or original.facts.merged_at is None:
            continue
        reland = relands.get(original.pr_id)
        chains.append(
            {
                "original": {"number": original.number, "url": original.url},
                "revert": {"number": revert.number, "url": revert.url},
                "reland": {"number": reland.number, "url": reland.url} if reland else None,
                "exposure_hours": (
                    revert.facts.merged_at - original.facts.merged_at
                ).total_seconds()
                / 3600,
                "revert_pr_cycle_hours": (
                    revert.facts.merged_at - revert.created_at
                ).total_seconds()
                / 3600,
            }
        )
    return waste, {
        "reverts": len(reverted_prs),
        "revert_prs": sum(p.facts.is_revert for p in prs),
        "relanded": sum(p.pr_id in relands for p in reverted_prs),
        "revert_chains": chains[:10],
    }


def guardrail(efficiency: dict[str, Any]) -> dict[str, Any]:
    cycle, revert = efficiency["cycle_time_p50_hours"], efficiency["revert_rate"]
    delta = (
        (revert["value"] - revert["previous"])
        if revert["value"] is not None and revert["previous"] is not None
        else None
    )
    increased = delta is not None and delta >= t.GUARDRAIL_REVERT_RATE_DELTA
    faster = (
        cycle["significant"] is True
        and cycle["change_rel"] is not None
        and cycle["change_rel"] <= -0.10
    )
    return {
        "cycle_time_p50_change_rel": cycle["change_rel"],
        "revert_rate": revert["value"],
        "previous_revert_rate": revert["previous"],
        "revert_rate_change_pp": delta * 100 if delta is not None else None,
        "verdict": "tradeoff_suspected"
        if increased and faster
        else ("watch" if increased else "ok"),
    }


def signal_measures(
    dataset: Dataset, window: Window, risks: Sequence[dict[str, Any]]
) -> dict[str, Measure]:
    prs = merged(dataset, window)
    internal = percentile(
        values([p for p in prs if not p.facts.external_contributor], "pickup_hours"), 50, 10
    )
    external = percentile(
        values([p for p in prs if p.facts.external_contributor], "pickup_hours"), 50, 10
    )
    waiting = [p for p in risks if p["state"] == "waiting_reviewer"]
    counts = Counter(location for p in waiting for location in p["locations"])
    return {
        "large_pr_share": rate([float(p.facts.size_lines >= t.LARGE_PR_LINES) for p in prs]),
        "merged_without_approval_share": rate(
            [float(p.facts.merged_without_approval) for p in prs]
        ),
        "fast_large_approval_share": rate(
            [
                float(
                    p.facts.size_lines >= t.FAST_APPROVAL_MIN_LINES
                    and p.facts.first_approval_at is not None
                    and p.facts.ready_at is not None
                    and p.facts.first_approval_at - p.facts.ready_at
                    <= timedelta(minutes=t.FAST_APPROVAL_MINUTES)
                    and p.facts.feedback_before_approval == 0
                )
                for p in prs
            ]
        ),
        "external_pickup_ratio": Measure(
            external / internal if external is not None and internal else None, len(prs)
        ),
        "at_risk_reviewer_top_location_share": Measure(
            max(counts.values(), default=0) / len(waiting) if len(waiting) >= 4 else None,
            len(waiting),
        ),
    }


def signals(dataset: Dataset, risks: Sequence[dict[str, Any]], params_hash: str) -> dict[str, Any]:
    current = signal_measures(dataset, dataset.current, risks)
    previous = (
        signal_measures(dataset, dataset.previous, at_risk(dataset, at=dataset.start))
        if dataset.comparison_available
        else {}
    )
    return {
        name: compare(
            measure,
            previous.get(name),
            unit="ratio" if name == "external_pickup_ratio" else "share",
            name=name,
            params_hash=params_hash,
        )
        for name, measure in current.items()
    }


def series(dataset: Dataset, window: Window) -> list[dict[str, Any]]:
    result = []
    for week in weeks(window):
        prs = merged(dataset, week)
        totals = {
            state: sum(hours(p, end=week.end)[state] for p in prs) for state in WAITING_STATES
        }
        result.append(
            {
                "week_start": week.start.date().isoformat(),
                "days": (week.end - week.start).total_seconds() / 86400,
                "merged": len(prs),
                "cycle_p50_hours": percentile(
                    values(prs, "cycle_hours"), 50, t.MIN_SAMPLES_WEEKLY_P50
                ),
                "pickup_p50_hours": percentile(
                    values(prs, "pickup_hours"), 50, t.MIN_SAMPLES_WEEKLY_P50
                ),
                "pr_size_p50_lines": percentile(
                    values(prs, "size_lines"), 50, t.MIN_SAMPLES_WEEKLY_P50
                ),
                "waiting_reviewer_share": ratio(totals["waiting_reviewer"], sum(totals.values()))
                if prs
                else None,
                "waiting_ci_share": ratio(totals["waiting_ci"], sum(totals.values()))
                if prs
                else None,
                "reverts": sum(reverted(p, window.end) for p in prs),
            }
        )
    return result


def attribution(
    dataset: Dataset, ledger: dict[str, Any], locations_: list[dict[str, Any]]
) -> dict[str, Any] | None:
    current, previous = merged(dataset, dataset.current), merged(dataset, dataset.previous)
    if not dataset.comparison_available or min(len(current), len(previous)) < t.MIN_SAMPLES_P50:
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
    decrease = sum(max(0, -c["change"]) for c in components.values())
    for c in components.values():
        c.update(
            share_of_increase=ratio(max(0, c["change"]), increase),
            share_of_decrease=ratio(max(0, -c["change"]), decrease),
        )
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
            share_of_reviewer_increase=share,
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
        p.facts.cycle_hours or 0 for p in current if p.facts.size_lines >= t.LARGE_PR_LINES
    ) / len(current)
    large_previous = sum(
        p.facts.cycle_hours or 0 for p in previous if p.facts.size_lines >= t.LARGE_PR_LINES
    ) / len(previous)
    cycle_delta = cycle_current - cycle_previous
    return {
        "basis": "mean_hours_per_merged_pr",
        "cycle_mean_hours": {
            "current": cycle_current,
            "previous": cycle_previous,
            "change": cycle_delta,
        },
        "total_increase_hours": increase,
        "total_decrease_hours": decrease,
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


def trend(
    dataset: Dataset, ledger: dict[str, Any], locations_: list[dict[str, Any]]
) -> dict[str, Any]:
    shifts = [
        (entry["change_pp"], state)
        for state, entry in ledger["states"].items()
        if entry["change_pp"] is not None and entry["change_pp"] >= 5
    ]
    shift = max(shifts, default=None)
    return {
        "bottleneck_shift": f"{shift[1]} share {shift[0]:+.1f}pp vs previous period"
        if shift
        else None,
        "attribution": attribution(dataset, ledger, locations_),
    }
