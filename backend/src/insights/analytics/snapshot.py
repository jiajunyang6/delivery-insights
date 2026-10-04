"""Assemble the snapshot payload and PR rows from a Dataset; deterministic, no I/O."""

import hashlib
import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

import orjson

from insights.analytics import ANALYTICS_VERSION
from insights.analytics import bottlenecks as b
from insights.analytics import thresholds as t
from insights.analytics.bottlenecks import at_risk, location_names
from insights.analytics.ci import build_ci
from insights.analytics.dataset import Dataset, SnapshotParams, hours, merged, open_at, reverted
from insights.analytics.drivers import build_drivers
from insights.analytics.efficiency import build_efficiency
from insights.analytics.findings import build_findings, headline
from insights.analytics.stats import SAMPLING_SEED_VERSION
from insights.analytics.timeline import state_at


def iso(at: datetime) -> str:
    """Format a timestamp in UTC at whole-second precision with a trailing Z."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(value: Any) -> bytes:
    """Sorted-key JSON bytes shared by content hashes, stored payloads and HTTP ETags."""
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)


def digest(value: Any) -> str:
    """Return the full SHA-256 digest of canonical JSON bytes."""
    return hashlib.sha256(canonical(value)).hexdigest()


def freshness(dataset: Dataset) -> list[dict[str, Any]]:
    """Serialize repository coverage, sync status and data versions in repository order."""
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
    """Return (snapshot_id, params_hash, versions_hash).

    The ID covers canonical params (with analytics and thresholds versions), per-repo data
    versions and sync times, and as_of. It needs repo metadata only, not PRs.
    """
    params_hash = digest(params.canonical_dict())[:16]
    versions_hash = digest(freshness(dataset))[:16]
    snapshot_id = (
        "s_"
        + hashlib.sha256(
            f"{params_hash}|{versions_hash}|{iso(dataset.as_of)}".encode()
        ).hexdigest()[:16]
    )
    return snapshot_id, params_hash, versions_hash


def sampling_hash(params: SnapshotParams) -> str:
    """Bootstrap seed hash using SAMPLING_SEED_VERSION instead of the output schema version.

    Data/cache identity can change without randomly changing a comparison's sampled draws.
    """
    seed_params = params.canonical_dict()
    seed_params["analytics_version"] = SAMPLING_SEED_VERSION
    return digest(seed_params)[:16]


def etag(payload: bytes) -> str:
    """Return a quoted 32-hex-character SHA-256 prefix for these exact payload bytes."""
    return '"' + hashlib.sha256(payload).hexdigest()[:32] + '"'


def rounded(value: Any, key: str = "", unit: str = "") -> Any:
    """Apply display precision recursively after computation; reject non-finite numbers.

    Shares/relative changes retain four decimals, most other floats two. Independently rounded
    current, previous and delta values can differ from arithmetic on the displayed values.
    """
    if isinstance(value, dict):
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
            )
            else 2
        )
        return round(value, precision)
    return value


def build_snapshot(dataset: Dataset, *, params: SnapshotParams) -> dict[str, Any]:
    """Build the JSON-ready snapshot payload; pure and deterministic for a dataset and params.

    Findings and the headline are derived from the assembled snapshot before rounding.
    """
    snapshot_id, _, _ = identifiers(dataset, params)
    # Seed from the sampling hash, not the snapshot ID, so analytics-only releases keep the
    # same bootstrap seeds.
    params_hash = sampling_hash(params)
    efficiency = build_efficiency(dataset, params_hash)
    risks = b.at_risk(dataset, at=dataset.as_of, exclude_current_drafts=dataset.current_day)
    ledger = b.time_ledger(dataset)
    ledger["ci_data_available"] = params.ci_source != "none" and ledger["ci_coverage"] > 0
    locations = b.locations(dataset, risks, ledger)
    waste, rework = b.waste_rework(dataset)
    current = merged(dataset, dataset.current)
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
            "review_load": b.review_load(dataset),
            "ci": build_ci(dataset, params_hash) if params.ci_source == "actions" else None,
        },
        "drivers": build_drivers(dataset),
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
                "open_prs_at_as_of": sum(open_at(p, dataset.as_of) for p in dataset.flow),
            },
        },
    }
    snapshot["bottlenecks"] = build_findings(snapshot, dataset)
    snapshot["headline"] = headline(snapshot)
    return dict(rounded(snapshot))


def build_pr_rows(dataset: Dataset) -> list[dict[str, Any]]:
    """Rows for period-active flow PRs merged or closed unmerged in the period, or open at as_of.

    Sorted by repo and number; values are rounded like the snapshot.
    """
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
