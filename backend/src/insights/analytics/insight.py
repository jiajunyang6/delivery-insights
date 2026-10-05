"""Public insight view projected from a snapshot: where PR time goes, stated in words; no I/O.

The full snapshot stays internal as narrative evidence; this view is what the API returns.
"""

from typing import Any

WAITING_LABELS = {
    "waiting_reviewer": "waiting on reviewers",
    "waiting_author": "waiting on authors",
    "waiting_merge": "waiting to merge after approval",
}
STATE_FIELDS = ("pr_hours", "share", "previous_pr_hours", "previous_share", "change_pp")


def percent(share: float) -> str:
    """Format a fractional share as a whole percent."""
    return f"{share * 100:.0f}%"


def statement(
    merged: int,
    states: dict[str, dict[str, Any]],
    largest: str | None,
    change: str | None,
    cycle: dict[str, Any],
    comparison: bool,
) -> str:
    """Describe the observed waits and cycle time factually; never name a cause."""
    if largest is None:
        sentences = [f"{merged} merged PRs recorded no post-ready waiting time in this period."]
    else:
        current = states[largest]
        detail = f"previous period {percent(current['previous_share'])}" if comparison else ""
        if change == largest:
            direction = "up" if current["change_pp"] > 0 else "down"
            detail += f", {direction} {abs(current['change_pp']):.1f} pp"
        sentences = [
            f"Across {merged} merged PRs, {percent(current['share'])} of post-ready waiting time "
            f"was spent {WAITING_LABELS[largest]}" + (f" ({detail})." if detail else ".")
        ]
        if change is not None and change != largest:
            moved = states[change]
            direction = "rose" if moved["change_pp"] > 0 else "fell"
            sentences.append(
                f"The largest shift: time {WAITING_LABELS[change]} {direction} "
                f"{abs(moved['change_pp']):.1f} pp to {percent(moved['share'])}."
            )
    if cycle["value"] is None:
        sentences.append("Too few merged PRs for a median cycle time.")
    elif cycle["significant"] and cycle["change_rel"] is not None:
        verb = "rose" if cycle["change_rel"] > 0 else "fell"
        sentences.append(
            f"Median cycle time {verb} {abs(cycle['change_rel']) * 100:.0f}% to "
            f"{cycle['value']:.1f} h."
        )
    elif comparison and cycle["previous"] is not None:
        sentences.append(
            f"Median cycle time was {cycle['value']:.1f} h, with no significant change."
        )
    else:
        sentences.append(f"Median cycle time was {cycle['value']:.1f} h.")
    return " ".join(sentences)


def insight_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project a rounded snapshot to the public insight; deterministic for the same snapshot.

    largest_wait is the state with the largest share of post-ready waiting time, None when no
    merged PR waited. largest_change is the state whose share moved most in percentage points,
    None without a comparison or when no share moved. Ties break by state name.
    """
    ledger, efficiency = snapshot["time_ledger"], snapshot["efficiency"]
    comparison = snapshot["meta"]["comparison_available"]
    states = ledger["states"]
    largest = (
        max(states, key=lambda state: (states[state]["share"], state))
        if ledger["total_pr_hours"] > 0
        else None
    )
    movers = [state for state in states if states[state]["change_pp"]]
    change = (
        max(movers, key=lambda state: (abs(states[state]["change_pp"]), state))
        if comparison and movers
        else None
    )
    cycle, merged = efficiency["cycle_time_p50_hours"], efficiency["merged_prs"]
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "repo": snapshot["repos"][0],
        "period": snapshot["period"],
        "as_of": snapshot["as_of"],
        "comparison_available": comparison,
        "insight": {
            "statement": statement(merged["value"], states, largest, change, cycle, comparison),
            "largest_wait": {
                "state": largest,
                "share": states[largest]["share"],
                "previous_share": states[largest]["previous_share"],
            }
            if largest
            else None,
            "largest_change": {"state": change, "change_pp": states[change]["change_pp"]}
            if change
            else None,
            "cycle_time_p50_hours": {
                key: cycle[key] for key in ("value", "previous", "change_rel", "significant", "n")
            },
            "merged_prs": {"value": merged["value"], "previous": merged["previous"]},
        },
        "time_ledger": {
            "total_pr_hours": ledger["total_pr_hours"],
            "states": {
                state: {field: values[field] for field in STATE_FIELDS}
                for state, values in states.items()
            },
        },
        "links": {"narrative": f"/v1/snapshots/{snapshot['snapshot_id']}/narrative"},
    }
