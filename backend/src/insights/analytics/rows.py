from typing import Any

from insights.analytics.bottlenecks import at_risk, location_names
from insights.analytics.dataset import Dataset, hours, open_at, reverted
from insights.analytics.snapshot import rounded
from insights.analytics.timeline import state_at


def build_pr_rows(dataset: Dataset) -> list[dict[str, Any]]:
    risks = {
        (p["repo"], p["number"]): p
        for p in at_risk(dataset, at=dataset.as_of, exclude_current_drafts=dataset.current_day)
    }
    rows = []
    for pr in sorted(dataset.flow, key=lambda p: (p.repo, p.number)):
        f = pr.facts
        status = (
            "merged"
            if dataset.current.contains(f.merged_at)
            else "closed"
            if f.merged_at is None and dataset.current.contains(f.closed_at)
            else "open"
            if open_at(pr, dataset.as_of)
            else None
        )
        if status is None:
            continue
        interval = state_at(pr.intervals, dataset.as_of) if status == "open" else None
        risk = risks.get((pr.repo, pr.number))
        rows.append(
            {
                "repo": pr.repo,
                "number": pr.number,
                "title": pr.title,
                "url": pr.url,
                "author": pr.author,
                "status": status,
                "created_at": pr.created_at,
                "ready_at": f.ready_at,
                "merged_at": f.merged_at if f.merged_at and f.merged_at < dataset.as_of else None,
                "closed_at": f.closed_at if f.closed_at and f.closed_at < dataset.as_of else None,
                "size_lines": f.size_lines,
                "size_bucket": f.size_bucket,
                "locations": list(location_names(pr, dataset)),
                "external_contributor": f.external_contributor,
                "is_revert": f.is_revert,
                "reverted": reverted(pr, dataset.as_of),
                "close_class": f.close_class,
                "review_rounds": f.review_rounds,
                "human_reviews": f.human_reviews,
                "cycle_hours": f.cycle_hours if status == "merged" else None,
                "stage_hours": {
                    stage: getattr(f, stage + "_hours")
                    for stage in ("coding", "pickup", "review", "merge")
                },
                "ledger_hours": hours(pr, end=dataset.as_of),
                "current_state": interval.state if interval else None,
                "current_state_age_hours": (dataset.as_of - interval.start_at).total_seconds()
                / 3600
                if interval
                else None,
                "at_risk": {
                    key: risk[key]
                    for key in (
                        "severity",
                        "threshold_hours",
                        "critical_threshold_hours",
                        "baseline_source",
                    )
                }
                if risk
                else None,
            }
        )
    return list(rounded(rows))
