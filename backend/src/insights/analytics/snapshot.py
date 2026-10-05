"""Assemble the snapshot payload from a Dataset; deterministic, no I/O."""

import hashlib
import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

import orjson

from insights.analytics import ANALYTICS_VERSION, THRESHOLDS_VERSION
from insights.analytics import bottlenecks as b
from insights.analytics.dataset import Dataset, SnapshotParams, merged
from insights.analytics.efficiency import build_efficiency, slowest_decile_size_ratio
from insights.analytics.stats import SAMPLING_SEED_VERSION


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
    # Replay both historical parameter shapes without retaining CI collection or metrics.
    profile = seed_params.pop("sampling_profile")
    seed_params["ci_source"] = "actions" if profile == "github" else "none"
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

    It holds the time ledger shown on the dashboard and every metric the narrative evidence
    catalog reads; values are rounded once, after computation.
    """
    snapshot_id, _, _ = identifiers(dataset, params)
    # Seed from the sampling hash, not the snapshot ID, so analytics-only releases keep the
    # same bootstrap seeds.
    params_hash = sampling_hash(params)
    ledger = b.time_ledger(dataset)
    locations = b.locations(dataset)
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
        "efficiency": build_efficiency(dataset, params_hash),
        "time_ledger": ledger,
        "bottleneck_analysis": {
            "review_queue": b.review_queue(dataset, dataset.current),
            "locations": locations,
        },
        "drivers": {"slowest_decile_size_ratio": slowest_decile_size_ratio(dataset)},
        "trend": {"attribution": b.attribution(dataset, ledger, locations)},
        "series": {
            "current": b.series(dataset, dataset.current),
            "previous": b.series(dataset, dataset.previous) if dataset.comparison_available else [],
        },
        "meta": {
            "analytics_version": ANALYTICS_VERSION,
            "thresholds_version": THRESHOLDS_VERSION,
            "location_dimension": params.location_dimension,
            "comparison_available": dataset.comparison_available,
            "sample": {"merged_prs": len(merged(dataset, dataset.current))},
        },
    }
    return dict(rounded(snapshot))
