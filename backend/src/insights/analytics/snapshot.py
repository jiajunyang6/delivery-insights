import hashlib
import math
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

import orjson

from insights.analytics import ANALYTICS_VERSION
from insights.analytics import bottlenecks as b
from insights.analytics import thresholds as t
from insights.analytics.ci import build_ci
from insights.analytics.dataset import Dataset, SnapshotParams, active_in, closed, merged, open_at
from insights.analytics.drivers import build_drivers
from insights.analytics.efficiency import build_efficiency
from insights.analytics.findings import build_findings, headline


def iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(value: Any) -> bytes:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def freshness(dataset: Dataset) -> list[dict[str, Any]]:
    return [
        {
            "repo": r.repo,
            "data_version": r.data_version,
            "covered_since": iso(r.covered_since),
            "last_synced_at": iso(r.last_synced_at),
            "last_sync_status": r.last_sync_status,
        }
        for r in sorted(dataset.repos, key=lambda r: r.repo)
    ]


def identifiers(dataset: Dataset, params: SnapshotParams) -> tuple[str, str, str]:
    params_hash = digest(params.canonical_dict())[:16]
    versions_hash = digest(freshness(dataset))[:16]
    snapshot_id = (
        "s_"
        + hashlib.sha256(
            f"{params_hash}|{versions_hash}|{iso(dataset.as_of)}".encode()
        ).hexdigest()[:16]
    )
    return snapshot_id, params_hash, versions_hash


def etag(payload: bytes) -> str:
    return '"' + hashlib.sha256(payload).hexdigest()[:32] + '"'


def rounded(value: Any, key: str = "", unit: str = "") -> Any:
    if isinstance(value, dict):
        if key == "s_at_hours":
            return {k: rounded(v, k, "share") for k, v in value.items()}
        if str(value.get("feature", "")).endswith("_share"):
            return {
                k: rounded(v, k, "share" if k in {"slowest", "rest"} else "")
                for k, v in value.items()
            }
        current_unit = value.get("unit", unit)
        return {
            k: rounded(v, k, current_unit if k in {"value", "previous", "change_abs"} else "")
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [rounded(v, key, unit) for v in value]
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite analytics value")
        if unit == "count" or (key == "days" and value.is_integer()):
            return int(value)
        precision = (
            4
            if (
                unit in {"share", "change", "coefficient"}
                or any(
                    part in key
                    for part in ("share", "rate", "change_rel", "growth_rel", "coverage")
                )
                or key in {"spearman"}
            )
            else 2
        )
        return round(value, precision)
    return value


def build_snapshot(dataset: Dataset, *, params: SnapshotParams) -> dict[str, Any]:
    snapshot_id, params_hash, _ = identifiers(dataset, params)
    efficiency = build_efficiency(dataset, params_hash)
    risks = b.at_risk(dataset, at=dataset.as_of, exclude_current_drafts=dataset.current_day)
    ledger = b.time_ledger(dataset)
    ledger["ci_data_available"] = params.ci_source != "none" and ledger["ci_coverage"] > 0
    locations = b.locations(dataset, risks, ledger)
    waste, rework = b.waste_rework(dataset)
    current = merged(dataset, dataset.current)
    excluded = dict.fromkeys(("bot_prs", "backport_prs", "never_ready_drafts"), 0)
    for pr in dataset.prs:
        f = pr.facts
        if not dataset.current.contains(f.end_at) or not active_in(pr, dataset.current):
            continue
        if f.is_bot_author:
            excluded["bot_prs"] += 1
        elif f.is_backport:
            excluded["backport_prs"] += 1
        elif f.ready_at is None and f.closed_at:
            excluded["never_ready_drafts"] += 1
    source_counts = Counter(p.facts.location_source for p in current)
    snapshot: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "repos": sorted(r.repo for r in dataset.repos),
        "period": {
            "from": dataset.period_from,
            "to": dataset.period_to,
            "days": dataset.days,
            "complete": dataset.as_of == dataset.to_excl,
            "compared_to": {
                "from": dataset.previous.start.date(),
                "to": (dataset.start.date() - timedelta(days=1)),
            },
        },
        "as_of": dataset.as_of,
        "headline": "",
        "efficiency": efficiency,
        "time_ledger": ledger,
        "bottlenecks": [],
        "bottleneck_analysis": {
            "review_queue": b.review_queue(dataset, dataset.current),
            "locations": locations,
            "merge_blockers": b.merge_blockers(dataset),
            "pareto": b.pareto(ledger, locations),
            "what_if": [
                scenario
                for stage in (
                    ("pickup", "merge", "ci")
                    if ledger["ci_data_available"]
                    else ("pickup", "merge")
                )
                if (scenario := b.what_if(dataset, stage)) is not None
            ],
            "review_load": b.review_load(dataset),
            "ci": build_ci(dataset, params_hash, ledger["ci_coverage"])
            if params.ci_source == "actions"
            else None,
        },
        "drivers": build_drivers(dataset, efficiency["stage_p50_hours"]["pickup"]["value"]),
        "at_risk_prs": risks[: t.AT_RISK_MAX_ITEMS],
        "at_risk_summary": b.risk_summary(risks),
        "waste": waste,
        "rework": rework,
        "guardrail": b.guardrail(efficiency),
        "trend": b.trend(dataset, ledger, locations),
        "signals": b.signals(dataset, risks, params_hash),
        "series": {
            "current": b.series(dataset, dataset.current),
            "previous": b.series(dataset, dataset.previous) if dataset.comparison_available else [],
        },
        "per_repo": None,
        "links": {
            "self": f"/v1/snapshots/{snapshot_id}",
            "narrative": f"/v1/snapshots/{snapshot_id}/narrative",
        },
        "meta": {
            "schema_version": "1",
            "analytics_version": ANALYTICS_VERSION,
            "thresholds_version": t.THRESHOLDS_VERSION,
            "location_dimension": params.location_dimension,
            "time_basis": "utc_wall_clock",
            "comparison_available": dataset.comparison_available,
            "ci_source": params.ci_source,
            "data_freshness": freshness(dataset),
            "sample": {
                "merged_prs": len(current),
                "closed_unmerged_prs": len(closed(dataset, dataset.current)),
                "ready_prs": sum(dataset.current.contains(p.facts.ready_at) for p in dataset.flow),
                "open_prs_at_as_of": sum(open_at(p, dataset.as_of) for p in dataset.flow),
                "human_reviews": len(dataset.reviews_in(dataset.current)),
            },
            "excluded": excluded,
            "location_sources": {
                name: source_counts[name]
                for name in ("label", "codeowners", "directory", "unclassified")
            },
        },
    }
    if len(dataset.repos) > 1:
        per_repo = []
        for repo in sorted(dataset.repos, key=lambda r: r.repo):
            metrics = build_efficiency(dataset.for_repo(repo.repo), params_hash)
            per_repo.append(
                {
                    "repo": repo.repo,
                    "merged_prs": metrics["merged_prs"]["value"],
                    "cycle_time_p50_hours": metrics["cycle_time_p50_hours"]["value"],
                    "pickup_p50_hours": metrics["stage_p50_hours"]["pickup"]["value"],
                    "waiting_share": metrics["waiting_share"]["value"],
                }
            )
        snapshot["per_repo"] = per_repo
    snapshot["bottlenecks"] = build_findings(snapshot, dataset)
    snapshot["headline"] = headline(snapshot)
    return dict(rounded(snapshot))
