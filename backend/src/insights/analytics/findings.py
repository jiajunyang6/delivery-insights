from typing import Any

from insights.analytics import thresholds as t
from insights.analytics.bottlenecks import what_if
from insights.analytics.dataset import Dataset, hours, merged
from insights.analytics.stats import ratio


def resolve_pointer(payload: Any, pointer: str) -> Any:
    current = payload
    for part in pointer.lstrip("/").split("/") if pointer else []:
        key = part.replace("~1", "/").replace("~0", "~")
        current = current[int(key)] if isinstance(current, list) else current[key]
    return current


def build_findings(snapshot: dict[str, Any], dataset: Dataset) -> list[dict[str, Any]]:
    if snapshot["efficiency"]["merged_prs"]["value"] < t.MIN_SAMPLES_P50:
        return []
    result: list[dict[str, Any]] = []
    ledger, analysis, efficiency = (
        snapshot["time_ledger"],
        snapshot["bottleneck_analysis"],
        snapshot["efficiency"],
    )
    states = ledger["states"]

    def value(path: str) -> Any:
        resolved = resolve_pointer(snapshot, path)
        return resolved["value"] if isinstance(resolved, dict) and "value" in resolved else resolved

    def above(path: str, threshold: float) -> bool:
        v = value(path)
        return v is not None and v >= threshold

    def add(
        kind: str,
        title: str,
        recommendation: str,
        impact: float,
        evidence: list[tuple[str, str, str]],
        *,
        high: bool = False,
        location: str | None = None,
        stage: str | None = None,
    ) -> None:
        result.append(
            {
                "id": f"{kind}:{location}" if location else kind,
                "rank": 0,
                "type": kind,
                "severity": "high" if high else "medium",
                "title": title,
                "location": location,
                "impact_pr_hours": impact,
                "impact_share": ratio(impact, ledger["total_pr_hours"]),
                "evidence": [
                    {"label": label, "value": value(ref), "unit": unit, "ref": ref}
                    for label, ref, unit in evidence
                ],
                "recommendation": recommendation,
                "what_if": what_if(dataset, stage, location) if stage else None,
            }
        )

    for index, loc in enumerate(analysis["locations"]):
        if loc["location"] == "other":
            continue
        prefix = f"/bottleneck_analysis/locations/{index}"
        if (
            above(prefix + "/pickup_ratio_vs_rest", t.REVIEW_CAPACITY_PICKUP_RATIO)
            and loc["waiting_reviewer_share"] >= t.REVIEW_CAPACITY_MIN_WAIT_SHARE
            and loc["merged_prs"] >= t.MIN_LOCATION_PRS
        ):
            name = loc["location"]
            add(
                "review_capacity",
                f"First-review wait concentrated in {name}",
                f"Add reviewers or code owners for {name}, enable team auto-assignment, "
                "and set a one-business-day first-review SLA.",
                loc["waiting_reviewer_pr_hours"],
                [
                    (
                        "First-review wait vs rest of repo",
                        prefix + "/pickup_ratio_vs_rest",
                        "ratio",
                    ),
                    ("Share of reviewer-waiting time", prefix + "/waiting_reviewer_share", "share"),
                    ("Median first-review wait", prefix + "/pickup_p50_hours", "hours"),
                ],
                high=loc["waiting_reviewer_share"] >= t.REVIEW_CAPACITY_HIGH_WAIT_SHARE,
                location=name,
                stage="pickup",
            )
    queue = analysis["review_queue"]
    if ratio(
        queue["weeks_inflow_exceeds_outflow"], queue["weeks_total"]
    ) >= t.QUEUE_GROWTH_WEEK_SHARE and above(
        "/bottleneck_analysis/review_queue/open_growth_rel", t.QUEUE_GROWTH_MIN_RELATIVE
    ):
        add(
            "review_queue_growth",
            "Review queue is growing",
            "Review demand exceeds capacity: rebalance review load or "
            "temporarily limit work in progress until the queue stops growing.",
            states["waiting_reviewer"]["pr_hours"],
            [
                (
                    "Weeks with inflow above outflow",
                    "/bottleneck_analysis/review_queue/weeks_inflow_exceeds_outflow",
                    "count",
                ),
                (
                    "Open queue growth",
                    "/bottleneck_analysis/review_queue/open_growth_rel",
                    "change",
                ),
            ],
            high=queue["open_growth_rel"] >= t.QUEUE_GROWTH_HIGH_RELATIVE,
        )
    if above("/efficiency/review_concentration_top_k", t.CONCENTRATION_SHARE):
        add(
            "review_concentration",
            "Reviews concentrated on a few people",
            "Spread reviews through a rotation or CODEOWNERS so a few reviewers "
            "are not a single point of failure.",
            0,
            [
                (
                    "Share of reviews by top K reviewers",
                    "/efficiency/review_concentration_top_k",
                    "share",
                )
            ],
            high=efficiency["review_concentration_top_k"]["value"] >= t.CONCENTRATION_HIGH_SHARE,
        )
    if above("/time_ledger/states/waiting_merge/share", t.MERGE_BLOCKED_SHARE) or above(
        "/efficiency/stage_p50_hours/merge", t.MERGE_BLOCKED_P50_HOURS
    ):
        recommendation = (
            "Review whether two approvals are needed for low-risk changes."
            if above("/bottleneck_analysis/merge_blockers/second_approval_share", 0.5)
            else "Reduce post-approval rebase friction, for example with a merge queue."
        )
        add(
            "merge_blocked",
            "Approved PRs wait long to merge",
            recommendation,
            states["waiting_merge"]["pr_hours"],
            [
                (
                    "Share of PR time waiting to merge",
                    "/time_ledger/states/waiting_merge/share",
                    "share",
                ),
                ("Median approval-to-merge time", "/efficiency/stage_p50_hours/merge", "hours"),
                (
                    "Share with a second approval",
                    "/bottleneck_analysis/merge_blockers/second_approval_share",
                    "share",
                ),
            ],
            high=states["waiting_merge"]["share"] >= t.MERGE_BLOCKED_HIGH_SHARE,
            stage="merge",
        )
    if ledger["ci_data_available"] and states["waiting_ci"]["share"] >= t.CI_WAIT_SHARE:
        ci = analysis["ci"]
        queue_minutes = ci["queue_p50_minutes"]["value"]
        run_minutes = ci["run_p50_minutes"]["value"]
        recommendation = "Reduce CI workflow runtime and remove redundant jobs."
        if queue_minutes is not None and run_minutes is not None and queue_minutes >= run_minutes:
            recommendation = "Increase runner capacity to reduce CI queue time."
        flaky = ci["flaky_rerun_rate"]["value"]
        if flaky is not None and flaky >= 0.1:
            recommendation += " Investigate flaky tests that pass only after a rerun."
        add(
            "ci_wait",
            "CI contributes substantial waiting time",
            recommendation,
            states["waiting_ci"]["pr_hours"],
            [
                (
                    "Share of PR time waiting for CI",
                    "/time_ledger/states/waiting_ci/share",
                    "share",
                ),
                ("Median CI queue time", "/bottleneck_analysis/ci/queue_p50_minutes", "minutes"),
                ("Median CI runtime", "/bottleneck_analysis/ci/run_p50_minutes", "minutes"),
            ],
            high=states["waiting_ci"]["share"] >= t.CI_WAIT_HIGH_SHARE,
            stage="ci",
        )
    if above("/efficiency/avg_review_rounds", t.REWORK_ROUNDS) or above(
        "/efficiency/post_review_commit_share", t.REWORK_POST_REVIEW_SHARE
    ):
        add(
            "rework_high",
            "High rework after review",
            "Agree on the approach before coding (issue or design note) and "
            "keep PRs small to cut review rounds.",
            states["waiting_author"]["pr_hours"],
            [
                ("Average review rounds", "/efficiency/avg_review_rounds", "rounds"),
                (
                    "Share with commits after first review",
                    "/efficiency/post_review_commit_share",
                    "share",
                ),
            ],
        )
    if (
        above("/efficiency/waste_share", t.WASTE_SHARE)
        or snapshot["waste"]["lost_while_waiting"] >= t.LOST_WHILE_WAITING_MIN
    ):
        add(
            "waste_high",
            "Significant work never ships",
            "Review late rejections and PRs lost while waiting for review; "
            "align on scope earlier and triage stale PRs.",
            snapshot["waste"]["wasted_pr_hours"],
            [
                ("Waste share", "/efficiency/waste_share", "share"),
                ("PRs lost while waiting for review", "/waste/lost_while_waiting", "count"),
            ],
            high=above("/efficiency/waste_share", t.WASTE_HIGH_SHARE),
        )
    if snapshot["guardrail"]["verdict"] != "ok":
        add(
            "quality_guardrail",
            "Speed may be costing quality",
            "Faster delivery may be costing quality: check whether review depth "
            "dropped before pushing speed further.",
            0,
            [
                ("Median cycle time change", "/guardrail/cycle_time_p50_change_rel", "change"),
                ("Revert rate", "/guardrail/revert_rate", "share"),
            ],
            high=snapshot["guardrail"]["verdict"] == "tradeoff_suspected",
        )
    if above("/signals/external_pickup_ratio", t.EXTERNAL_PICKUP_RATIO):
        impact = sum(
            hours(p, end=dataset.as_of)["waiting_reviewer"]
            for p in merged(dataset, dataset.current)
            if p.facts.external_contributor
        )
        add(
            "external_contributor_wait",
            "External contributors wait longer for a first review",
            "Set up a triage rotation so community PRs get a first review sooner.",
            impact,
            [
                (
                    "First-review wait, external vs internal",
                    "/signals/external_pickup_ratio",
                    "ratio",
                )
            ],
        )
    result.sort(
        key=lambda f: (
            -f["impact_pr_hours"],
            {"high": 0, "medium": 1, "low": 2}[f["severity"]],
            f["id"],
        )
    )
    for index, finding in enumerate(result, 1):
        finding["rank"] = index
    return result


def headline(snapshot: dict[str, Any]) -> str:
    cycle = snapshot["efficiency"]["cycle_time_p50_hours"]
    value, previous, change = cycle["value"], cycle["previous"], cycle["change_rel"]
    if value is None:
        text = "Not enough merged PRs for a reliable cycle time"
    elif change is None or previous is None:
        text = f"Median cycle time is {value:.1f}h (no previous period to compare)"
    elif cycle["significant"]:
        direction = "rose" if change > 0 else "fell"
        text = (
            f"Median cycle time {direction} {abs(change) * 100:.0f}% "
            f"({previous:.1f}h → {value:.1f}h)"
        )
    else:
        text = (
            f"Median cycle time is {value:.1f}h "
            f"({change * 100:+.0f}% vs previous period, not significant)"
        )
    if snapshot["bottlenecks"]:
        first = snapshot["bottlenecks"][0]
        text += "; the main bottleneck is " + first["title"][0].lower() + first["title"][1:]
        if first["type"] in {"review_capacity", "review_queue_growth"}:
            share = snapshot["time_ledger"]["states"]["waiting_reviewer"]["share"]
            text += f" ({share * 100:.0f}% of PR time waits on reviewers)"
        scenario = first["what_if"]
        if scenario and scenario["change_rel"] is not None:
            text += (
                f". Capping {scenario['stage']} at {scenario['target_hours']:.0f}h would cut "
                f"median cycle time by about {abs(scenario['change_rel']) * 100:.0f}%"
            )
    return text + "."
